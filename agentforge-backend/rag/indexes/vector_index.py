"""
VectorStoreIndex management using ChromaDB as the backend.

Key design:
  - build_index_for_document(): called by the Celery ingestion task.
    Loads ONE document, chunks it, embeds it, and APPENDS nodes to the
    agent's existing ChromaDB collection. Does NOT touch other documents.

  - load_index(): called at chat time to get a query-ready index.
    Simply wraps the existing ChromaDB collection — no re-embedding.

This means:
  - Adding document #100 only embeds document #100.  
  - All 99 previous documents are already in ChromaDB and untouched.  
  - Load time at chat is instant (just opens the collection).  
"""
import logging

from llama_index.core import VectorStoreIndex, StorageContext, Settings
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.schema import Document as LlamaDocument
from llama_index.llms.ollama import Ollama
from llama_index.vector_stores.chroma import ChromaVectorStore
from django.conf import settings

from rag.embeddings.ollama_embeddings import get_embedding_model
from rag.indexes.chroma_store import get_or_create_collection

logger = logging.getLogger(__name__)

_llama_configured = False


def get_splitter() -> SentenceSplitter:
    """The one splitter used everywhere — sizes come from RAG_CHUNK_* settings."""
    return SentenceSplitter(
        chunk_size=settings.RAG_CHUNK_SIZE,
        chunk_overlap=settings.RAG_CHUNK_OVERLAP,
    )


def configure_llama_settings() -> None:
    """
    Set global LlamaIndex LLM, embedding model, and chunk size.
    Only runs once per process (idempotent).
    """
    global _llama_configured
    if _llama_configured:
        return

    Settings.llm = Ollama(
        model=settings.OLLAMA_LLM_MODEL,
        base_url=settings.OLLAMA_BASE_URL,
        request_timeout=120.0,
    )
    Settings.embed_model = get_embedding_model()
    Settings.node_parser = get_splitter()

    _llama_configured = True
    logger.info(
        "LlamaIndex configured — LLM: %s | Embed: %s",
        settings.OLLAMA_LLM_MODEL,
        settings.OLLAMA_EMBED_MODEL,
    )


def build_index_for_document(
    collection_name: str,
    documents: list[LlamaDocument],
) -> None:
    """
    Embed a list of LlamaIndex Documents and APPEND their nodes into
    the agent's ChromaDB collection.

    This is the incremental ingestion function. Each new PDF upload
    calls this with only the newly uploaded document's content.
    The existing nodes for all previous PDFs remain untouched in ChromaDB.

    Args:
        collection_name: The agent's ChromaDB collection (e.g. 'agent_<uuid>').
        documents: LlamaIndex Document objects from the new PDF only.
    """
    configure_llama_settings()

    # Get or create the collection (creates on first upload, gets on subsequent ones)
    chroma_collection = get_or_create_collection(collection_name)

    # Wrap ChromaDB collection as a LlamaIndex vector store
    vector_store = ChromaVectorStore(chroma_collection=chroma_collection)
    storage_context = StorageContext.from_defaults(vector_store=vector_store)

    # Split the new document into chunks
    nodes = get_splitter().get_nodes_from_documents(documents)

    logger.info(
        "Embedding %d nodes into collection '%s' (collection currently has %d items)",
        len(nodes),
        collection_name,
        chroma_collection.count(),
    )

    # VectorStoreIndex.from_documents() here would rebuild everything.
    # Instead we use from_nodes() which only processes the nodes we pass in,
    # then adds them to the existing collection via the storage_context.
    VectorStoreIndex(
        nodes=nodes,
        storage_context=storage_context,
        show_progress=True,
    )

    logger.info(
        "Done. Collection '%s' now has %d items.",
        collection_name,
        chroma_collection.count(),
    )


def load_index(collection_name: str) -> VectorStoreIndex:
    """
    Load an existing ChromaDB collection as a LlamaIndex VectorStoreIndex.
    This is called at chat time. No embedding happens here — the vectors
    are already stored in ChromaDB from previous ingestion runs.

    Args:
        collection_name: The agent's ChromaDB collection name.

    Returns:
        A VectorStoreIndex ready to be used as a query engine.
    """
    configure_llama_settings()

    chroma_collection = get_or_create_collection(collection_name)

    if chroma_collection.count() == 0:
        raise ValueError(
            f"Collection '{collection_name}' is empty. "
            "The agent has no indexed documents yet."
        )

    vector_store = ChromaVectorStore(chroma_collection=chroma_collection)
    storage_context = StorageContext.from_defaults(vector_store=vector_store)

    # from_vector_store() loads the existing index without re-embedding anything.
    index = VectorStoreIndex.from_vector_store(
        vector_store=vector_store,
        storage_context=storage_context,
    )

    logger.info(
        "Loaded index from collection '%s' (%d items)",
        collection_name,
        chroma_collection.count(),
    )
    return index
