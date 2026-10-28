from .base import *  # noqa

DEBUG = True
CORS_ALLOW_ALL_ORIGINS = True

# CHROMA_MODE comes from the environment (base.py), defaulting to "local":
#   local venv  → "local" (embedded PersistentClient at ./chromadb/)
#   Docker      → docker-compose sets CHROMA_MODE=http to use the chromadb service
