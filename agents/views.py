import logging

from django.db.models import Q
from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.response import Response

from .models import Agent, Document
from .serializers import AgentSerializer, AgentCreateSerializer
from .permissions import IsOwnerOrPublicReadOnly

logger = logging.getLogger(__name__)


class AgentViewSet(viewsets.ModelViewSet):
    """
    Full CRUD for agents plus two extra actions:

    POST /api/agents/{id}/upload/   — upload one or more PDFs.
         Each file is saved immediately and a Celery task is queued
         to embed it into ChromaDB in the background.
         Returns 202 Accepted instantly — the user doesn't wait for embedding.

    POST /api/agents/{id}/reindex/  — wipe the ChromaDB collection for this
         agent and re-ingest all documents from scratch. Owner only.

    GET  /api/agents/{id}/status/   — returns current indexing_status so
         the frontend can poll until it becomes 'ready'.
    """

    permission_classes = [permissions.IsAuthenticated, IsOwnerOrPublicReadOnly]

    def get_serializer_class(self):
        if self.action == "create":
            return AgentCreateSerializer
        return AgentSerializer

    def get_queryset(self):
        user = self.request.user
        return Agent.objects.filter(Q(owner=user) | Q(is_public=True)).distinct()

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)

    # ── Custom list actions ────────────────────────────────────
    @action(detail=False, methods=["get"], url_path="my")
    def my_agents(self, request):
        qs = Agent.objects.filter(owner=request.user)
        return Response(AgentSerializer(qs, many=True).data)

    @action(detail=False, methods=["get"], url_path="public")
    def public_agents(self, request):
        qs = Agent.objects.filter(is_public=True)
        return Response(AgentSerializer(qs, many=True).data)

    # ── Indexing status poll ───────────────────────────────────
    @action(detail=True, methods=["get"], url_path="status")
    def indexing_status(self, request, pk=None):
        """
        GET /api/agents/{id}/status/
        Frontend polls this endpoint after upload to know when ingestion is done.
        Returns: { "indexing_status": "indexing" | "ready" | "error", "is_indexed": bool }
        """
        agent = self.get_object()
        return Response({
            "indexing_status": agent.indexing_status,
            "is_indexed": agent.is_indexed,
        })

    # ── PDF upload (triggers background Celery task) ───────────
    @action(
        detail=True,
        methods=["post"],
        url_path="upload",
        parser_classes=[MultiPartParser, FormParser],
    )
    def upload_pdf(self, request, pk=None):
        """
        POST /api/agents/{id}/upload/

        Steps that happen here (synchronously, very fast):
          1. Validate the request has files.
          2. Save each PDF to disk as a Document row.
          3. Queue a Celery task for EACH document — returns task_id.
          4. Return 202 Accepted immediately.

        The actual embedding runs in the background worker.
        Frontend uses GET /api/agents/{id}/status/ to poll progress.
        """
        # Import here to avoid circular imports at module load time
        from rag.tasks.ingestion_tasks import ingest_document_task

        agent = self.get_object()

        if agent.owner != request.user:
            return Response({"detail": "Only the owner can upload documents."}, status=403)

        files = request.FILES.getlist("files")
        if not files:
            return Response({"detail": "No files provided. Send PDFs under the 'files' key."}, status=400)

        queued = []
        for f in files:
            # 1. Save the file to disk and create a DB row immediately
            doc = Document.objects.create(agent=agent, file=f, filename=f.name)

            # 2. Mark agent as "indexing" so status endpoint reflects it
            agent.indexing_status = "indexing"
            agent.save(update_fields=["indexing_status"])

            # 3. Queue background task — passes the document PK, NOT the file object
            #    (file objects can't be serialised into Redis)
            task = ingest_document_task.delay(str(doc.id))

            # 4. Store task_id on the document so the frontend can track it
            doc.task_id = task.id
            doc.save(update_fields=["task_id"])

            queued.append({"filename": doc.filename, "task_id": task.id, "document_id": str(doc.id)})
            logger.info("Queued ingestion task %s for document %s", task.id, doc.id)

        return Response(
            {
                "message": f"{len(queued)} file(s) uploaded. Ingestion running in background.",
                "queued": queued,
            },
            status=status.HTTP_202_ACCEPTED,
        )

    # ── Reindex (wipe + rebuild from scratch) ─────────────────
    @action(detail=True, methods=["post"], url_path="reindex")
    def reindex(self, request, pk=None):
        """
        POST /api/agents/{id}/reindex/
        Deletes the ChromaDB collection for this agent and re-queues
        ingestion for every document. Useful when the index is stale.
        """
        from rag.tasks.ingestion_tasks import reindex_agent_task

        agent = self.get_object()

        if agent.owner != request.user:
            return Response({"detail": "Only the owner can reindex."}, status=403)

        if not agent.documents.exists():
            return Response({"detail": "No documents to reindex."}, status=400)

        # Queue the full reindex as a single background task
        task = reindex_agent_task.delay(str(agent.id))

        agent.indexing_status = "indexing"
        agent.is_indexed = False
        agent.save(update_fields=["indexing_status", "is_indexed"])

        return Response(
            {"message": "Reindex started.", "task_id": task.id},
            status=status.HTTP_202_ACCEPTED,
        )
