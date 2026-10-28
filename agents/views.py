import logging

from django.db.models import Q
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.response import Response

from .models import Agent, Document
from .serializers import (
    AgentSerializer,
    AgentListSerializer,
    AgentCreateSerializer,
    AgentStatusSerializer,
)
from .permissions import IsOwnerOrPublicReadOnly
from .validators import validate_pdf

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
        if self.action in ("list", "my_agents", "public_agents"):
            return AgentListSerializer
        if self.action == "indexing_status":
            return AgentStatusSerializer
        return AgentSerializer

    def get_queryset(self):
        # Schema generation runs without a real user; give spectacular the model
        # so it can type the {id} path parameter as a UUID.
        if getattr(self, "swagger_fake_view", False):
            return Agent.objects.none()
        user = self.request.user
        return Agent.objects.filter(Q(owner=user) | Q(is_public=True)).distinct()

    @extend_schema(request=AgentCreateSerializer, responses={201: AgentSerializer})
    def create(self, request, *args, **kwargs):
        # Validate with the slim create serializer, but respond with the full
        # agent (id, status, owner...) so the client can navigate to it.
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        agent = serializer.save(owner=request.user)
        return Response(AgentSerializer(agent).data, status=status.HTTP_201_CREATED)

    def perform_destroy(self, instance):
        """Remove the agent's vectors and PDFs too — otherwise they are orphaned."""
        from rag.indexes.chroma_store import delete_collection

        collection_name = instance.chroma_collection_name
        files = [doc.file for doc in instance.documents.all()]
        instance.delete()

        delete_collection(collection_name)
        for f in files:
            f.delete(save=False)

    # ── Custom list actions (paginated like the default list) ──
    def _paginated(self, qs):
        page = self.paginate_queryset(qs)
        serializer = self.get_serializer(page, many=True)
        return self.get_paginated_response(serializer.data)

    @action(detail=False, methods=["get"], url_path="my")
    def my_agents(self, request):
        return self._paginated(Agent.objects.filter(owner=request.user))

    @action(detail=False, methods=["get"], url_path="public")
    def public_agents(self, request):
        return self._paginated(Agent.objects.filter(is_public=True))

    # ── Indexing status poll ───────────────────────────────────
    @action(detail=True, methods=["get"], url_path="status")
    def indexing_status(self, request, pk=None):
        """
        GET /api/agents/{id}/status/
        Frontend polls this endpoint after upload to know when ingestion is done.
        Returns: { "indexing_status", "is_indexed", "documents_total", "documents_ingested" }
        """
        agent = self.get_object()
        return Response({
            "indexing_status": agent.indexing_status,
            "is_indexed": agent.is_indexed,
            "documents_total": agent.documents.count(),
            "documents_ingested": agent.documents.filter(ingested=True).count(),
        })

    # ── PDF upload (triggers background Celery task) ───────────
    @extend_schema(
        request={"multipart/form-data": inline_serializer(
            "AgentUploadRequest",
            {"files": serializers.ListField(child=serializers.FileField())},
        )},
        responses={202: inline_serializer("AgentUploadResponse", {
            "message": serializers.CharField(),
            "queued": inline_serializer("QueuedDocument", {
                "filename": serializers.CharField(),
                "task_id": serializers.CharField(),
                "document_id": serializers.UUIDField(),
            }, many=True),
        })},
    )
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
          1. Validate the request has files and every file is a real PDF.
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

        # Validate ALL files first so a bad file in the batch saves nothing
        for f in files:
            validate_pdf(f)

        # Mark agent as "indexing" so status endpoint reflects it
        agent.indexing_status = "indexing"
        agent.save(update_fields=["indexing_status"])

        queued = []
        for f in files:
            # 1. Save the file to disk and create a DB row immediately
            doc = Document.objects.create(agent=agent, file=f, filename=f.name)

            # 2. Queue background task — passes the document PK, NOT the file object
            #    (file objects can't be serialised into Redis)
            task = ingest_document_task.delay(str(doc.id))

            # 3. Store task_id on the document so the frontend can track it
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

    # ── Delete one document ───────────────────────────────────
    @extend_schema(
        parameters=[OpenApiParameter("doc_id", OpenApiTypes.UUID, OpenApiParameter.PATH)],
        responses={204: None},
    )
    @action(detail=True, methods=["delete"], url_path=r"documents/(?P<doc_id>[0-9a-fA-F-]{36})")
    def delete_document(self, request, pk=None, doc_id=None):
        """
        DELETE /api/agents/{id}/documents/{doc_id}/
        Removes one PDF, its chunks in ChromaDB and its row; the agent stays.
        Refused (409) while that document is still being embedded, so the worker
        can't write its vectors back after the delete.
        """
        from rag.services.ingestion_service import IngestionService

        agent = self.get_object()
        if agent.owner != request.user:
            return Response({"detail": "Only the owner can delete documents."}, status=403)

        document = agent.documents.filter(id=doc_id).first()
        if document is None:
            return Response({"detail": "Document not found."}, status=404)
        if not document.ingested and agent.indexing_status == "indexing":
            return Response(
                {"detail": "This document is still being embedded. Delete it once indexing finishes."},
                status=status.HTTP_409_CONFLICT,
            )

        IngestionService(agent).delete_document(document)
        return Response(status=status.HTTP_204_NO_CONTENT)

    # ── Reindex (wipe + rebuild from scratch) ─────────────────
    @extend_schema(request=None, responses={202: inline_serializer("ReindexResponse", {
        "message": serializers.CharField(),
        "task_id": serializers.CharField(),
    })})
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
