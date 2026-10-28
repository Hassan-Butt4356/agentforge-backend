"""
Celery tasks for background PDF ingestion.

Why Celery?
  Embedding a PDF with Ollama can take 30-120 seconds depending on size.
  If this ran synchronously in the Django view, the HTTP request would hang
  for that entire time. Celery moves the work to a background worker process,
  so the upload endpoint returns in milliseconds.

Task flow:
  1. User uploads PDF → view saves it to disk → queues ingest_document_task.
  2. Celery worker picks up the task from Redis.
  3. Worker calls IngestionService.ingest_single_document().
  4. IngestionService loads the PDF, embeds it, appends to ChromaDB.
  5. Worker updates Document.ingested and Agent.indexing_status in PostgreSQL.
  6. Frontend polls GET /api/agents/{id}/status/ until status == 'ready'.
"""
import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,   # retry after 60 seconds on failure
    name="rag.tasks.ingest_document",
)
def ingest_document_task(self, document_id: str):
    """
    Celery task: embed a single document and append it to ChromaDB.

    Args:
        document_id: UUID string of the Document DB row.

    The task fetches the Document from PostgreSQL (not from Redis) because
    file objects cannot be serialised. Only the ID travels through Redis.
    """
    # Import inside the task to avoid Django app registry issues at import time
    from agents.models import Document
    from rag.services.ingestion_service import IngestionService

    logger.info("Task started: ingest_document_task | document_id=%s", document_id)

    try:
        document = Document.objects.select_related("agent").get(id=document_id)
    except Document.DoesNotExist:
        logger.error("Document %s not found — task aborted.", document_id)
        return {"status": "error", "reason": "document not found"}

    service = IngestionService(document.agent)

    try:
        success = service.ingest_single_document(document)
    except Exception as exc:
        logger.error(
            "Ingestion failed for document %s: %s", document_id, exc, exc_info=True
        )
        # Retry the task up to max_retries times before giving up
        raise self.retry(exc=exc)

    status = "success" if success else "error"
    logger.info(
        "Task finished: ingest_document_task | document_id=%s | status=%s",
        document_id, status,
    )
    return {"status": status, "document_id": document_id}


@shared_task(
    bind=True,
    max_retries=1,
    name="rag.tasks.reindex_agent",
)
def reindex_agent_task(self, agent_id: str):
    """
    Celery task: wipe the agent's ChromaDB collection and re-ingest
    all documents from scratch.

    Args:
        agent_id: UUID string of the Agent DB row.
    """
    from agents.models import Agent
    from rag.services.ingestion_service import IngestionService

    logger.info("Task started: reindex_agent_task | agent_id=%s", agent_id)

    try:
        agent = Agent.objects.get(id=agent_id)
    except Agent.DoesNotExist:
        logger.error("Agent %s not found — reindex aborted.", agent_id)
        return {"status": "error", "reason": "agent not found"}

    service = IngestionService(agent)

    try:
        success = service.reindex_all()
    except Exception as exc:
        logger.error("Reindex failed for agent %s: %s", agent_id, exc, exc_info=True)
        raise self.retry(exc=exc)

    status = "success" if success else "error"
    logger.info(
        "Task finished: reindex_agent_task | agent_id=%s | status=%s",
        agent_id, status,
    )
    return {"status": status, "agent_id": agent_id}
