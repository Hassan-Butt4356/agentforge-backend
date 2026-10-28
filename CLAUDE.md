# CLAUDE.md — agentforge-backend

Django REST backend for AgentForge, a multi-tenant RAG chatbot platform. Users create "agents", upload PDFs to them, and chat with them. Everything runs locally: Ollama (llama3 + nomic-embed-text), ChromaDB for vectors, Celery + Redis for background ingestion. The Next.js client is a separate repo (github.com/Hassan-Butt4356/agentforge-frontend, usually checked out as the sibling folder `../agentforge-frontend`) and talks to this API over JWT.

## Commands

Run these from the repo root, with the venv active (`venv\Scripts\activate` on Windows).

```bash
python manage.py runserver                      # API on :8000 (defaults to config.settings.development)
celery -A config worker --loglevel=info --pool=solo   # --pool=solo is needed on Windows
python manage.py test                           # all tests
python manage.py test agents                    # one app
python manage.py test chat.tests.ChatTests.test_chat_creates_conversation   # one test
python manage.py makemigrations && python manage.py migrate
python manage.py spectacular --file schema.yml  # dump the OpenAPI schema (also served at /api/schema/)
docker-compose up --build                       # postgres, redis, chromadb, web, celery (Ollama stays on the host)
```

Ingestion and chat need `redis-server` and `ollama serve` running. Tests don't need either: the Celery `.delay`, `ChatService` and `delete_collection` calls are all mocked.

## Architecture

```
accounts/   register (AllowAny), SimpleJWT login/refresh/logout (blacklist), profile. Uses the built-in User model.
agents/     Agent + Document models. AgentViewSet: CRUD plus my/, public/, status/, upload/, reindex/. validators.py: PDF checks
chat/       Conversation + Message models. ChatView (sync), conversation list/detail
rag/        All LlamaIndex/Chroma/Ollama code. Django views only call into rag/services and rag/tasks
config/     settings/{base,development,production}.py, celery.py, urls.py (+ /api/schema/, /api/docs/)
```

Request flows:
- **Upload**: `upload_pdf` checks every file first (`.pdf` extension, `%PDF-` magic bytes, 50 MB limit), so one bad file rejects the whole batch. It then saves a `Document` row per file and queues `ingest_document_task(document_id)` for each, returning 202. The worker's `IngestionService.ingest_single_document` appends chunks to that agent's collection only. Once every document has `ingested=True`, the agent becomes `is_indexed=True, indexing_status="ready"`.
- **Chat**: `ChatView` → `ChatService(agent).chat(msg, history)` → `build_rag_agent` (a ReActAgent with a single `document_search` QueryEngineTool). If the LLM fails, `ChatService` raises `ChatServiceError` and the view returns **503 without saving anything**. A new `Conversation` is created only after a successful answer.
- **Isolation**: each agent has its own Chroma collection, `agent_<uuid with underscores>`. `perform_destroy` deletes that collection and the agent's PDF files.
- **Per-document vectors**: ingestion tags every chunk with `document_id` metadata (excluded from embedding/LLM text), so `delete_document_vectors()` removes exactly one file's chunks. Chunks indexed before that tag existed are matched on `file_path` instead.
- **Chunking**: the single source is `settings.RAG_CHUNK_SIZE` / `RAG_CHUNK_OVERLAP` / `RAG_SIMILARITY_TOP_K` (env, default 512/64/4), applied through `rag.indexes.vector_index.get_splitter()`. Changing them requires `POST /reindex/`.
- **Adding agent tools**: define them in `rag/agents/tools.py` and append them to `tools=[...]` in `rag/agents/base_agent.py`.

Conventions: views import Celery tasks inside the function (this avoids circular imports), and tasks import models inside the task body. Only IDs go through Redis. `rag/` reads config from `django.conf.settings`. Custom `APIView`s and `@action`s carry `@extend_schema` so the frontend's generated types stay accurate. Keep that up when adding endpoints.

## Settings and environment

- `DB_ENGINE=sqlite` (default) uses `db.sqlite3`. `DB_ENGINE=postgres` uses the `DB_*` vars. Both docker-compose files force postgres.
- `CHROMA_MODE` comes from env (default `local` = embedded `./chromadb/`). Docker sets `http`, and `production.py` always forces `http`.
- `production.py` sets `SECURE_PROXY_SSL_HEADER` because nginx terminates TLS. `SECURE_SSL_REDIRECT` can be turned off by env for HTTP-only testing.
- JWT: access token lasts 15 minutes (`JWT_ACCESS_MINUTES`), refresh 7 days. The frontend refreshes silently, so the short access token only limits how long a revoked device keeps working. Refresh tokens rotate, and old ones are blacklisted (`token_blacklist` app). Logout = `POST /api/auth/logout/ {refresh}`.
- `chromadb/chroma` image is pinned to `0.5.3` to match the Python client. Don't bump one without the other.

## Remaining gotchas

- Chat is synchronous and can take tens of seconds (Ollama timeout 120s, nginx `proxy_read_timeout 120s`). Streaming would be the next big improvement.
- Once an agent reaches `indexing_status="error"`, it stays there until a later successful ingest or a reindex. `is_indexed` is not reset on error.
- `ChatService` rebuilds the ReActAgent on every request. Nothing is cached.

## API surface

All endpoints except register, login, refresh, schema and docs need `Authorization: Bearer <access>`. **Every list endpoint is paginated**: `{count, next, previous, results}`, PAGE_SIZE=20, `?page=N`.

| Method | Path | Notes |
|---|---|---|
| POST | `/api/auth/register/` | `{username, email, password, password2}` (min 8 chars) |
| POST | `/api/auth/login/` | `{username, password}` → `{access, refresh}` |
| POST | `/api/auth/refresh/` | `{refresh}` → `{access, refresh}` (rotated; old one blacklisted) |
| POST | `/api/auth/logout/` | `{refresh}` → 200, blacklists it |
| POST | `/api/auth/password/` | `{current_password, new_password, new_password2}` → new `{access, refresh}`; blacklists every older refresh token (other devices signed out) |
| GET/PUT/PATCH | `/api/auth/profile/` | `{id, username, email, first_name, last_name, date_joined}` |
| GET/POST | `/api/agents/` | list items omit `documents`; create takes `{name, description, is_public}` and returns the full agent (with `id`) |
| GET/PUT/PATCH/DELETE | `/api/agents/{id}/` | detail includes `documents[]`; writes are owner-only; private agents return 404 to non-owners |
| GET | `/api/agents/my/`, `/api/agents/public/` | paginated, same shape as list |
| POST | `/api/agents/{id}/upload/` | multipart, repeated `files` → 202 `{message, queued:[{filename, task_id, document_id}]}`; 400 if any file isn't a PDF |
| DELETE | `/api/agents/{id}/documents/{doc_id}/` | owner only → 204; removes the PDF, its Chroma chunks and the row, then recomputes status (no docs left → `idle`); 409 while that doc is still embedding |
| POST | `/api/agents/{id}/reindex/` | owner only → 202 `{message, task_id}` |
| GET | `/api/agents/{id}/status/` | `{indexing_status: idle\|indexing\|ready\|error, is_indexed, documents_total, documents_ingested}` |
| POST | `/api/agents/{id}/chat/` | `{message, conversation_id?}` → 200 `{conversation_id, answer}`; 400 if not indexed; 503 if the LLM fails |
| GET | `/api/agents/{id}/conversations/` | paginated, the current user's conversations only |
| GET | `/api/conversations/` | paginated, all of the current user's conversations across agents (each has `agent_id`); hides agents no longer accessible |
| GET/DELETE | `/api/conversations/{id}/` | includes `messages: [{id, role, content, created_at}]` |
| GET | `/api/schema/`, `/api/docs/` | OpenAPI 3 schema and Swagger UI (no auth) |
