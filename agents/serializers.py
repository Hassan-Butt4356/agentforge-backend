from rest_framework import serializers
from .models import Agent, Document


class DocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Document
        fields = ["id", "filename", "ingested", "task_id", "uploaded_at"]
        read_only_fields = ["id", "filename", "ingested", "task_id", "uploaded_at"]


class AgentSerializer(serializers.ModelSerializer):
    owner = serializers.StringRelatedField(read_only=True)
    documents = DocumentSerializer(many=True, read_only=True)
    document_count = serializers.SerializerMethodField()

    class Meta:
        model = Agent
        fields = [
            "id", "name", "description", "owner",
            "is_public", "is_indexed", "indexing_status",
            "document_count", "documents", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "owner", "is_indexed", "indexing_status", "created_at", "updated_at"]

    def get_document_count(self, obj) -> int:
        return obj.documents.count()


class AgentListSerializer(AgentSerializer):
    """Same as AgentSerializer minus the nested documents — used for list endpoints."""

    class Meta(AgentSerializer.Meta):
        fields = [f for f in AgentSerializer.Meta.fields if f != "documents"]


class AgentStatusSerializer(serializers.Serializer):
    indexing_status = serializers.ChoiceField(choices=Agent.STATUS_CHOICES)
    is_indexed = serializers.BooleanField()
    documents_total = serializers.IntegerField()
    documents_ingested = serializers.IntegerField()


class AgentCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Agent
        fields = ["name", "description", "is_public"]
