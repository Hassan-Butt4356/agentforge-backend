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

    def get_document_count(self, obj):
        return obj.documents.count()


class AgentCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Agent
        fields = ["name", "description", "is_public"]
