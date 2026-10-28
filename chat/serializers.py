from rest_framework import serializers
from .models import Conversation, Message


class MessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Message
        fields = ["id", "role", "content", "created_at"]


class ConversationSerializer(serializers.ModelSerializer):
    messages = MessageSerializer(many=True, read_only=True)
    agent_name = serializers.CharField(source="agent.name", read_only=True)
    message_count = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = ["id", "agent_name", "title", "message_count", "messages", "created_at"]

    def get_message_count(self, obj) -> int:
        return obj.messages.count()


class ConversationListSerializer(serializers.ModelSerializer):
    agent_id = serializers.UUIDField(source="agent.id", read_only=True)
    agent_name = serializers.CharField(source="agent.name", read_only=True)
    message_count = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = ["id", "agent_id", "agent_name", "title", "message_count", "created_at"]

    def get_message_count(self, obj) -> int:
        # List views annotate the count (one query); fall back for unannotated objects.
        annotated = getattr(obj, "n_messages", None)
        return annotated if annotated is not None else obj.messages.count()


class ChatInputSerializer(serializers.Serializer):
    message = serializers.CharField(max_length=4000)
    conversation_id = serializers.UUIDField(required=False, allow_null=True, default=None)


class ChatResponseSerializer(serializers.Serializer):
    conversation_id = serializers.UUIDField()
    answer = serializers.CharField()
