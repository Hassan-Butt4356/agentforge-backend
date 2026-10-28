# CLAUDE.md — AgentForge workspace

This repo holds two separate projects:

```
agentforge/
├── agentforge-backend/    Django REST + Celery + LlamaIndex/ChromaDB/Ollama RAG API → see agentforge-backend/CLAUDE.md
└── agentforge-frontend/   Next.js (App Router) + React client (not created yet; waiting on design approval)
```

- Run each project's commands from inside its own folder. Each has its own dependencies (`venv/` for the backend, `node_modules/` for the frontend).
- The backend's OpenAPI schema (`/api/schema/`) is the contract between the two. Generate the frontend's TS types from it, and don't hand-write API shapes.
- The frontend design is a canvas artifact (dashboard, agent detail, chat, sign-in): https://claude.ai/artifact/SJ1PDH7mb6uaLMEjuKZgbS. Its source lives in `.design/` (git-excluded locally). Get the user's approval on design changes before implementing them.
