from llama_index.embeddings.ollama import OllamaEmbedding
from django.conf import settings


def get_embedding_model() -> OllamaEmbedding:
    """
    Returns a configured OllamaEmbedding instance.

    Model: nomic-embed-text (pulled via `ollama pull nomic-embed-text:latest`)
    This converts text chunks into 768-dimension vectors that get stored in ChromaDB.
    """
    return OllamaEmbedding(
        model_name=settings.OLLAMA_EMBED_MODEL,
        base_url=settings.OLLAMA_BASE_URL,
    )
