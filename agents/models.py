import uuid
from django.db import models
from django.contrib.auth.models import User


class Agent(models.Model):
    """
    One AI chatbot belonging to a user.
    Each agent has its own isolated ChromaDB collection
    named 'agent_<uuid>' so documents never mix between agents.

    is_indexed: False = no documents ingested yet, True = ready to chat.
    indexing_status: human-readable status shown to the frontend
    so the user knows if ingestion is still running in the background.
    """

    STATUS_CHOICES = [
        ("idle", "Idle"),
        ("indexing", "Indexing"),
        ("ready", "Ready"),
        ("error", "Error"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name="agents")
    is_public = models.BooleanField(default=False)
    is_indexed = models.BooleanField(default=False)
    indexing_status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="idle")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} (owner: {self.owner.username})"

    @property
    def chroma_collection_name(self):
        """Each agent gets its own ChromaDB collection — prevents cross-agent contamination."""
        return f"agent_{str(self.id).replace('-', '_')}"


class Document(models.Model):
    """
    A single PDF belonging to an agent.
    task_id stores the Celery task ID so the frontend can poll task status.
    ingested: False = still being processed, True = successfully added to ChromaDB.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name="documents")
    file = models.FileField(upload_to="pdfs/%Y/%m/")
    filename = models.CharField(max_length=255)
    ingested = models.BooleanField(default=False)   # True once Celery task finishes
    task_id = models.CharField(max_length=255, blank=True, default="")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at"]

    def __str__(self):
        return f"{self.filename} → {self.agent.name}"
