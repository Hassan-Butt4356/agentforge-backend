from .base import *  # noqa

DEBUG = True
CORS_ALLOW_ALL_ORIGINS = True

# In dev with local venv, ChromaDB runs as an embedded persistent client
# pointing at ./chromadb/ folder. No separate server needed.
CHROMA_MODE = "local"
