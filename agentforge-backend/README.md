# AgentForge 🔥

**Production-ready Django Agentic RAG platform** — create AI agents, upload PDFs, chat with your documents using a fully local LLM stack.

[![Python](https://img.shields.io/badge/Python-3.11+-3572A5?style=flat&logo=python&logoColor=white)](https://python.org)
[![Django](https://img.shields.io/badge/Django-5.0-092e20?style=flat&logo=django&logoColor=white)](https://djangoproject.com)
[![DRF](https://img.shields.io/badge/DRF-3.15-a30000?style=flat)](https://www.django-rest-framework.org)
[![LlamaIndex](https://img.shields.io/badge/LlamaIndex-0.10-7c6aff?style=flat)](https://llamaindex.ai)
[![ChromaDB](https://img.shields.io/badge/ChromaDB-0.5-orange?style=flat)](https://trychroma.com)
[![Celery](https://img.shields.io/badge/Celery-5.4-brightgreen?style=flat)](https://docs.celeryq.dev)
[![Redis](https://img.shields.io/badge/Redis-7-red?style=flat&logo=redis&logoColor=white)](https://redis.io)
[![Ollama](https://img.shields.io/badge/Ollama-llama3-black?style=flat)](https://ollama.com)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?style=flat&logo=docker&logoColor=white)](https://docker.com)
[![License](https://img.shields.io/badge/License-MIT-green?style=flat)](LICENSE)

---

## What is AgentForge?

AgentForge is a **multi-tenant chatbot platform** where each user creates AI agents powered by their own PDF knowledge bases. Upload documents, and an AI agent answers questions about them using **Retrieval-Augmented Generation (RAG)**.

Everything runs **100% locally** — no OpenAI API key, no cloud LLM costs.

---

## Architecture

```
User uploads PDF
      │
      ▼
Django View (saves file to disk, returns 202 instantly)
      │
      ▼ queues task
    Redis
      │
      ▼ picks up task
Celery Worker
      │
      ├── LlamaIndex SimpleDirectoryReader (load PDF)
      ├── SentenceSplitter (RAG_CHUNK_SIZE=512, RAG_CHUNK_OVERLAP=64)
      ├── nomic-embed-text via Ollama (generate vectors)
      └── ChromaDB (append vectors to agent's collection)
                                         ▲
User sends chat message                  │
      │                                  │
      ▼                                  │
Django View ──► ChatService ──► ReActAgent ──► QueryEngineTool
                                    │
                                    ▼
                              Ollama llama3 (generate answer)
```

---

## Key Design Decisions

### Incremental Ingestion (not full rebuild)

When you upload document #50, only document #50 is embedded. The previous 49 documents are already in ChromaDB and stay untouched.

| Approach | Upload #50 | Upload #100 |
|---|---|---|
| Full rebuild (old) | Re-embeds all 50 ❌ | Re-embeds all 100 ❌ |
| Incremental (current) | Embeds only #50   | Embeds only #100   |

### Background Processing with Celery

The upload endpoint returns `202 Accepted` in milliseconds. Embedding runs in a separate Celery worker. The frontend polls `GET /api/agents/{id}/status/` until `indexing_status` becomes `ready`.

### One ChromaDB Collection Per Agent

Each agent gets its own isolated collection named `agent_<uuid>`. Documents from different agents never mix.

### Same Vector Store in Dev and Prod

ChromaDB is used in both environments. In dev (local venv) it runs as an embedded persistent client writing to `./chromadb/`. In Docker it runs as a standalone HTTP service. Switches automatically via `CHROMA_MODE` env variable.

### Ollama runs on Host Machine (not in Docker)

Ollama is intentionally kept outside Docker to allow GPU access and avoid model re-downloads on every container rebuild. Django and Celery containers connect to it via `host.docker.internal:11434`.

---

## Tech Stack

| Layer | Technology |
|---|---|
| **Backend** | Django 5.0, Django REST Framework 3.15 |
| **Auth** | SimpleJWT |
| **AI / RAG** | LlamaIndex 0.10, ReActAgent |
| **LLM** | Ollama (llama3:latest) — runs on host |
| **Embeddings** | Ollama (nomic-embed-text:latest) — runs on host |
| **Vector Store** | ChromaDB 0.5 |
| **Task Queue** | Celery 5.4 + Redis 7 |
| **Database** | SQLite (local default) / PostgreSQL 16 (Docker, prod) |
| **Containers** | Docker, Docker Compose |

---

## Project Structure

```
agentforge/
│
├── config/                        # Django project package
│   ├── settings/
│   │   ├── base.py                # All shared settings
│   │   ├── development.py         # Debug=True, ChromaDB local mode
│   │   └── production.py          # Security headers, ChromaDB HTTP mode
│   ├── celery.py                  # Celery app + autodiscover_tasks()
│   ├── __init__.py                # Imports celery app so it loads with Django
│   └── urls.py
│
├── accounts/                      # Auth: register, login, JWT, profile
├── agents/                        # Agent CRUD + PDF upload + status polling
├── chat/                          # Conversations + messages + chat endpoint
│
├── rag/                           # All AI logic — no Django coupling
│   ├── loaders/pdf_loader.py      # SimpleDirectoryReader → LlamaIndex Documents
│   ├── embeddings/                # nomic-embed-text via Ollama
│   ├── indexes/
│   │   ├── chroma_store.py        # ChromaDB client, get/create/delete collections
│   │   └── vector_index.py        # build_index_for_document() + load_index()
│   ├── agents/base_agent.py       # ReActAgent factory with QueryEngineTool
│   ├── retrievers/                # Direct retriever (no agent overhead)
│   ├── services/
│   │   ├── ingestion_service.py   # ingest_single_document() + reindex_all()
│   │   └── chat_service.py        # Wraps ReActAgent with history + error handling
│   └── tasks/ingestion_tasks.py   # Celery tasks: ingest_document + reindex_agent
│
├── media/                         # Uploaded PDFs (gitignored)
├── chromadb/                      # ChromaDB local data in dev (gitignored)
├── scripts/entrypoint.sh          # Docker prod entrypoint
├── Dockerfile
├── docker-compose.yml             # Stack: PostgreSQL + Redis + ChromaDB + Celery
├── .env.example
└── README.md
```

---

## Prerequisites

- **Python 3.11+**
- **PostgreSQL 16+**
- **Redis**
- **Docker + Docker Compose** (for containerised setup)
- **Ollama** — [install from ollama.com](https://ollama.com)

```bash
# Pull required models (do this once)
ollama pull llama3:latest
ollama pull nomic-embed-text:latest
```

---

## Quick Start — Option A: Local Development (venv)

Use this when running everything directly on your machine without Docker.

```bash
# 1. Clone
git clone https://github.com/<your-username>/agentforge.git
cd agentforge/agentforge-backend

# 2. Virtual environment
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Defaults work as-is: DB_ENGINE=sqlite, CHROMA_MODE=local.
# For Postgres set DB_ENGINE=postgres + DB_* and run `createdb agentforge`.

# 5. Run migrations
python manage.py migrate

# 6. Open 4 terminals and run each:
redis-server                                        # Terminal 1
ollama serve                                        # Terminal 2
celery -A config worker --loglevel=info --pool=solo # Terminal 3 (--pool=solo on Windows)
python manage.py runserver                          # Terminal 4
```

ChromaDB runs embedded in dev — creates a `./chromadb/` folder automatically. No separate server needed.

---

## Quick Start — Option B: Docker Compose

In this setup, PostgreSQL, Redis, and ChromaDB run in Docker. **Ollama runs on your host machine** (not in Docker) so it can access your GPU and avoid re-downloading models on every container rebuild.

### Step 1 — Configure Ollama to listen on all interfaces

By default Ollama only listens on `127.0.0.1`, which Docker containers cannot reach. You need to bind it to `0.0.0.0` so containers can connect via `host.docker.internal`.

**Windows:**
```bash
# Set environment variable (Command Prompt or PowerShell)
setx OLLAMA_HOST "0.0.0.0:11434"

# Restart Ollama (kill and reopen from Start Menu, or:)
taskkill /IM ollama.exe /F
ollama serve
```

**macOS / Linux:**
```bash
# Add to ~/.bashrc or ~/.zshrc
export OLLAMA_HOST="0.0.0.0:11434"

# Restart Ollama
pkill ollama
ollama serve
```

**Verify Ollama is listening on all interfaces:**
```bash
# Windows
netstat -ano | findstr 11434
# Expected output:
# TCP    0.0.0.0:11434    LISTENING
# TCP    [::]:11434       LISTENING

# macOS/Linux
lsof -i :11434
```

**Verify Ollama is reachable:**
```bash
curl http://localhost:11434/api/tags
# Should return JSON with your available models
```

### Step 2 — Pull Ollama models (on host)

```bash
ollama pull llama3:latest
ollama pull nomic-embed-text:latest
ollama list   # verify both models appear
```

### Step 3 — Start Docker services

```bash
cp .env.example .env

docker-compose up --build
```

The `docker-compose.yml` already configures both `web` and `celery` services with:
```yaml
OLLAMA_BASE_URL: http://host.docker.internal:11434
```

This tells the Django app and Celery worker to reach the Ollama instance running on your host machine through Docker's built-in host gateway.

### Step 4 — Create superuser

```bash
docker-compose exec web python manage.py createsuperuser
```

API available at **http://localhost:8000**.

---

## How Ollama + Docker Works

```
┌─────────────────────────────────────────────┐
│           Host Machine (Windows/Mac/Linux)  │
│                                             │
│   Ollama listening on 0.0.0.0:11434    ◄───┼── models cached locally
│         ▲                  ▲               │
└─────────┼──────────────────┼───────────────┘
          │                  │
          │  host.docker.internal:11434
          │                  │
┌─────────┼──────────────────┼───────────────┐
│         │    Docker Network                │
│    ┌────┴────┐        ┌────┴────┐          │
│    │   web   │        │ celery  │          │
│    │ Django  │        │ worker  │          │
│    └─────────┘        └─────────┘          │
│                                            │
│  ┌──────────┐  ┌────────┐  ┌──────────┐   │
│  │ chromadb │  │ redis  │  │    db    │   │
│  └──────────┘  └────────┘  └──────────┘   │
└────────────────────────────────────────────┘
```

---

## How Ingestion Works (Step by Step)

```
1. POST /api/agents/{id}/upload/  with PDF files
         │
         ├── Saves PDF to disk (media/pdfs/...)
         ├── Creates Document row (ingested=False)
         ├── Sets agent.indexing_status = "indexing"
         ├── Queues ingest_document_task in Redis
         └── Returns 202 immediately  

2. Celery worker picks up ingest_document_task
         │
         ├── Loads PDF via SimpleDirectoryReader
         ├── Splits into 512-token chunks (64 overlap, configurable)
         ├── Sends chunks to nomic-embed-text → vectors
         ├── Appends ONLY new vectors to agent's ChromaDB collection
         ├── Sets Document.ingested = True
         └── If all docs ingested → agent.indexing_status = "ready"

3. Frontend polls GET /api/agents/{id}/status/
         └── {"indexing_status": "ready", "is_indexed": true}  
```

---

## API Reference

All protected endpoints require: `Authorization: Bearer <access_token>`

### Auth

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| POST | `/api/auth/register/` | ❌ | Register |
| POST | `/api/auth/login/` | ❌ | Login → tokens |
| POST | `/api/auth/refresh/` | ❌ | Refresh token (rotates) |
| POST | `/api/auth/logout/` | ❌ | Blacklist a refresh token |
| GET/PUT | `/api/auth/profile/` |   | View / update profile |

### Agents

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| GET/POST | `/api/agents/` |   | List / create |
| GET/PUT/DELETE | `/api/agents/{id}/` |   | Retrieve / update / delete |
| GET | `/api/agents/my/` |   | Your agents only |
| GET | `/api/agents/public/` |   | All public agents |
| POST | `/api/agents/{id}/upload/` |   Owner | Upload PDFs (async) |
| POST | `/api/agents/{id}/reindex/` |   Owner | Rebuild index |
| GET | `/api/agents/{id}/status/` |   | Poll indexing status + document progress |

All list endpoints are paginated: `{count, next, previous, results}` (20 per page, `?page=N`).
Interactive docs: **`/api/docs/`** · OpenAPI schema: **`/api/schema/`**

### Chat

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| POST | `/api/agents/{id}/chat/` |   | Send message |
| GET | `/api/agents/{id}/conversations/` |   | List conversations |
| GET/DELETE | `/api/conversations/{id}/` |   | View / delete conversation |

---

### Example: Full Workflow

```bash
# 1. Register
curl -X POST http://localhost:8000/api/auth/register/ \
  -H "Content-Type: application/json" \
  -d '{"username": "testuser", "email": "h@test.com", "password": "pass1234", "password2": "pass1234"}'

# 2. Login — save the access token
curl -X POST http://localhost:8000/api/auth/login/ \
  -H "Content-Type: application/json" \
  -d '{"username": "testuser", "password": "pass1234"}'

# 3. Create agent
curl -X POST http://localhost:8000/api/agents/ \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"name": "Research Bot", "description": "My papers", "is_public": false}'

# 4. Upload PDF — returns 202 immediately
curl -X POST http://localhost:8000/api/agents/<agent-id>/upload/ \
  -H "Authorization: Bearer <token>" \
  -F "files=@paper.pdf"

# 5. Poll until ready
curl http://localhost:8000/api/agents/<agent-id>/status/ \
  -H "Authorization: Bearer <token>"
# {"indexing_status": "ready", "is_indexed": true, "documents_total": 1, "documents_ingested": 1}

# 6. Chat
curl -X POST http://localhost:8000/api/agents/<agent-id>/chat/ \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"message": "What are the main findings?"}'

# 7. Continue conversation
curl -X POST http://localhost:8000/api/agents/<agent-id>/chat/ \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"message": "Elaborate on point 2.", "conversation_id": "<conv-id>"}'

# 8. Make agent public
curl -X PATCH http://localhost:8000/api/agents/<agent-id>/ \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"is_public": true}'
```

---

## Docker Commands Reference

```bash
# Start all services
docker-compose up --build

# Run in background
docker-compose up -d --build

# View logs
docker-compose logs -f web
docker-compose logs -f celery

# Run migrations
docker-compose exec web python manage.py migrate

# Create superuser
docker-compose exec web python manage.py createsuperuser

# Stop all services
docker-compose down

# Stop and remove volumes (wipes all data)
docker-compose down -v
```

---

## Troubleshooting

**Celery worker not processing tasks**
```bash
redis-cli ping          # should return PONG
celery -A config worker --loglevel=info
```

**Docker containers can't reach Ollama**
```bash
# Windows — bind Ollama to all interfaces and restart
setx OLLAMA_HOST "0.0.0.0:11434"
taskkill /IM ollama.exe /F && ollama serve

# Verify
netstat -ano | findstr 11434
# Must show: TCP 0.0.0.0:11434 LISTENING
```

**Reached max iterations error in chat**

The chat endpoint returns **503** when Ollama is unreachable or fails. For max-iteration loops, increase `max_iterations` in `rag/agents/base_agent.py`:
```python
ReActAgent.from_tools(tools=[rag_tool], verbose=True, max_iterations=10)
```

**ChromaDB collection empty / stale after chunk size change**
```bash
curl -X POST http://localhost:8000/api/agents/<agent-id>/reindex/ \
  -H "Authorization: Bearer <token>"
```

---

## Running Tests

```bash
python manage.py test accounts
python manage.py test agents
python manage.py test chat
```

---

## License

MIT — see [LICENSE](LICENSE).

---

## Author

Built by **Hassaan** — Python Backend & AI Engineer

**Stack:** Django · DRF · LlamaIndex · ChromaDB · Celery · Redis · Ollama · PostgreSQL · Docker
