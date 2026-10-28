"""
ChromaDB client and collection management.

Why one collection per agent?
    If all agents shared one collection, a user's PDFs could pollute
    another user's search results. Isolation is handled at the collection
    level — the collection name is 'agent_<uuid_without_dashes>'.

Two modes controlled by CHROMA_MODE in settings:
    "local"  — PersistentClient (embedded, writes to ./chromadb/ on disk).
               Used in dev with local venv. No server needed.
    "http"   — HttpClient (connects to the ChromaDB Docker container).
               Used in production (Docker Compose).
"""
import logging

import chromadb
from chromadb.config import Settings as ChromaSettings
from django.conf import settings

logger = logging.getLogger(__name__)

_chroma_client = None

# AgentForge promises nothing leaves the machine: switch off Chroma's PostHog telemetry.
_CHROMA_SETTINGS = ChromaSettings(anonymized_telemetry=False)


def get_chroma_client() -> chromadb.Client:
    """
    Returns a singleton ChromaDB client.
    Re-uses the same connection across requests in the same process.
    """
    global _chroma_client
    if _chroma_client is not None:
        return _chroma_client

    if settings.CHROMA_MODE == "http":
        # Production: ChromaDB running as a separate Docker service
        _chroma_client = chromadb.HttpClient(
            host=settings.CHROMA_HOST,
            port=settings.CHROMA_PORT,
            settings=_CHROMA_SETTINGS,
        )
        logger.info("ChromaDB connected via HTTP at %s:%s", settings.CHROMA_HOST, settings.CHROMA_PORT)
    else:
        # Development: embedded persistent client — writes to ./chromadb/
        _chroma_client = chromadb.PersistentClient(path=settings.CHROMA_LOCAL_PATH, settings=_CHROMA_SETTINGS)
        logger.info("ChromaDB PersistentClient at %s", settings.CHROMA_LOCAL_PATH)

    return _chroma_client


def get_or_create_collection(collection_name: str) -> chromadb.Collection:
    """
    Get an existing ChromaDB collection or create it if it doesn't exist.
    Called during ingestion (create) and during chat (get).
    """
    client = get_chroma_client()
    collection = client.get_or_create_collection(
        name=collection_name,
        metadata={"hnsw:space": "cosine"},   # cosine similarity for semantic search
    )
    logger.debug("Collection '%s' ready (%d items)", collection_name, collection.count())
    return collection


def delete_document_vectors(collection_name: str, document_id: str, file_path: str) -> int:
    """
    Remove every chunk of one document from an agent's collection; returns how many.

    New chunks carry a `document_id` metadata key (set at ingestion). Chunks from
    before that existed are matched by the `file_path` SimpleDirectoryReader stores.
    """
    client = get_chroma_client()
    try:
        collection = client.get_collection(collection_name)
    except Exception:
        return 0  # nothing was ever indexed for this agent

    before = collection.count()
    collection.delete(where={"document_id": document_id})
    collection.delete(where={"file_path": file_path})
    removed = before - collection.count()
    logger.info("Removed %d chunk(s) of document %s from '%s'", removed, document_id, collection_name)
    return removed


def delete_collection(collection_name: str) -> None:
    """
    Completely removes a collection and all its vectors.
    Called by the reindex endpoint to wipe and rebuild from scratch.
    """
    client = get_chroma_client()
    try:
        client.delete_collection(collection_name)
        logger.info("Deleted collection '%s'", collection_name)
    except Exception as e:
        logger.warning("Could not delete collection '%s': %s", collection_name, e)
