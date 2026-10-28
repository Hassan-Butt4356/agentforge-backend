"""
IngestionService — handles embedding a single document into ChromaDB.

Called exclusively from the Celery task (rag/tasks/ingestion_tasks.py).
Never called directly from a Django view.

Flow for a new document:
  1. Load the PDF from disk → LlamaIndex Document objects.
  2. Call build_index_for_document() which:
       - Splits text into 256-token chunks.
       - Sends each chunk to nomic-embed-text via Ollama → get vectors.
       - Appends the vectors to the agent's ChromaDB collection.
       - Does NOT touch any other documents already in that collection.
  3. Mark the Document.ingested = True in PostgreSQL.
  4. If ALL documents for this agent are now ingested, mark
     agent.is_indexed = True and agent.indexing_status = "ready".
"""
import logging

from rag.loaders.pdf_loader import load_pdf
from rag.indexes.vector_index import build_index_for_document
from rag.indexes.chroma_store import delete_collection, delete_document_vectors

logger = logging.getLogger(__name__)


class IngestionService:

    def __init__(self, agent):
        self.agent = agent
        self.collection_name = agent.chroma_collection_name

    def ingest_single_document(self, document) -> bool:
        """
        Embed ONE document and append it to the agent's ChromaDB collection.

        This is the incremental approach:
          - Document #1 uploaded → only document #1 is embedded.
          - Document #2 uploaded → only document #2 is embedded.
          - Document #50 uploaded → only document #50 is embedded.
          - All previous documents stay in ChromaDB, untouched.

        Returns True on success, False on failure.
        """
        logger.info(
            "Starting ingestion: document='%s' agent='%s'",
            document.filename, self.agent.name,
        )

        # Step 1: Load PDF from disk
        documents = load_pdf(document.file.path)
        if not documents:
            logger.error("Failed to load PDF: %s", document.file.path)
            self._mark_agent_error()
            return False

        # Tag every chunk with its Document row so it can be removed on its own later.
        # Kept out of the embedding and LLM text: it's a lookup key, not content.
        for doc in documents:
            doc.metadata["document_id"] = str(document.id)
            doc.excluded_embed_metadata_keys.append("document_id")
            doc.excluded_llm_metadata_keys.append("document_id")

        # Step 2: Embed and append to ChromaDB (incremental, not full rebuild)
        try:
            build_index_for_document(self.collection_name, documents)
        except Exception as e:
            logger.error("Embedding failed for '%s': %s", document.filename, e, exc_info=True)
            self._mark_agent_error()
            return False

        # Step 3: Mark this document as ingested in PostgreSQL
        document.ingested = True
        document.save(update_fields=["ingested"])

        # Step 4: Check if ALL documents for this agent are now ingested
        self._update_agent_status()

        logger.info("Ingestion complete for document '%s'", document.filename)
        return True

    def reindex_all(self) -> bool:
        """
        Wipe the ChromaDB collection and re-ingest ALL documents.
        Used by the /reindex/ endpoint.

        This is the only time we do a full rebuild — and it's explicit,
        not automatic on every upload.
        """
        logger.info("Reindexing agent '%s' — wiping collection first.", self.agent.name)

        # Wipe the existing collection
        delete_collection(self.collection_name)

        # Reset all document ingested flags
        self.agent.documents.update(ingested=False)

        # Re-ingest each document one by one
        all_ok = True
        for doc in self.agent.documents.all():
            ok = self.ingest_single_document(doc)
            if not ok:
                all_ok = False

        return all_ok

    def delete_document(self, document) -> int:
        """
        Remove one document: its vectors, its PDF on disk and its row.
        The agent's status is recomputed from what is left. Returns chunks removed.
        """
        removed = delete_document_vectors(self.collection_name, str(document.id), document.file.path)
        document.file.delete(save=False)
        document.delete()

        remaining = self.agent.documents.count()
        if remaining == 0:
            # Nothing left to chat with.
            self.agent.is_indexed = False
            self.agent.indexing_status = "idle"
            self.agent.save(update_fields=["is_indexed", "indexing_status"])
        else:
            self._update_agent_status()
        return removed

    def _update_agent_status(self):
        """
        Mark agent as ready only when ALL its documents have been ingested.
        If some are still pending (ingested=False), leave status as 'indexing'.
        """
        total = self.agent.documents.count()
        ingested = self.agent.documents.filter(ingested=True).count()

        if ingested >= total:
            self.agent.is_indexed = True
            self.agent.indexing_status = "ready"
        else:
            self.agent.indexing_status = "indexing"

        self.agent.save(update_fields=["is_indexed", "indexing_status"])
        logger.info(
            "Agent '%s' status: %s (%d/%d docs ingested)",
            self.agent.name, self.agent.indexing_status, ingested, total,
        )

    def _mark_agent_error(self):
        self.agent.indexing_status = "error"
        self.agent.save(update_fields=["indexing_status"])
