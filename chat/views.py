import logging

from rest_framework import status, permissions
from rest_framework.views import APIView
from rest_framework.response import Response

from agents.models import Agent
from .models import Conversation, Message
from .serializers import ChatInputSerializer, ConversationSerializer, ConversationListSerializer
from rag.services.chat_service import ChatService

logger = logging.getLogger(__name__)


class ChatView(APIView):
    """
    POST /api/agents/{agent_id}/chat/

    Flow:
      1. Validate message input.
      2. Check the agent exists, is accessible, and is_indexed=True.
      3. Get or create a Conversation.
      4. Load full message history for this conversation.
      5. Pass message + history to ChatService (RAG agent).
      6. Save both the user message and assistant reply to DB.
      7. Return the answer.

    The chat is synchronous — the user waits for the LLM to respond.
    This is intentional: streaming or async chat is a future enhancement.
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, agent_id):
        serializer = ChatInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user_message = serializer.validated_data["message"]
        conversation_id = serializer.validated_data.get("conversation_id")

        try:
            agent = Agent.objects.get(pk=agent_id)
        except Agent.DoesNotExist:
            return Response({"detail": "Agent not found."}, status=404)

        if not agent.is_public and agent.owner != request.user:
            return Response({"detail": "Access denied."}, status=403)

        if not agent.is_indexed:
            return Response(
                {
                    "detail": (
                        f"Agent is not ready yet (status: {agent.indexing_status}). "
                        "Upload PDFs and wait for ingestion to complete."
                    )
                },
                status=400,
            )

        # Get or create conversation
        if conversation_id:
            try:
                conversation = Conversation.objects.get(id=conversation_id, user=request.user, agent=agent)
            except Conversation.DoesNotExist:
                return Response({"detail": "Conversation not found."}, status=404)
        else:
            conversation = Conversation.objects.create(
                agent=agent, user=request.user, title=user_message[:60]
            )

        # Build history from DB
        history = list(conversation.messages.order_by("created_at").values("role", "content"))

        # Run RAG
        chat_service = ChatService(agent)
        answer = chat_service.chat(user_message, history)

        # Persist both turns
        Message.objects.create(conversation=conversation, role="user", content=user_message)
        Message.objects.create(conversation=conversation, role="assistant", content=answer)

        return Response(
            {"conversation_id": str(conversation.id), "answer": answer},
            status=200,
        )


class ConversationListView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, agent_id):
        try:
            agent = Agent.objects.get(pk=agent_id)
        except Agent.DoesNotExist:
            return Response({"detail": "Agent not found."}, status=404)

        if not agent.is_public and agent.owner != request.user:
            return Response({"detail": "Access denied."}, status=403)

        conversations = Conversation.objects.filter(agent_id=agent_id, user=request.user)
        return Response(ConversationListSerializer(conversations, many=True).data)


class ConversationDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def _get_conversation(self, request, pk):
        try:
            return Conversation.objects.get(pk=pk, user=request.user)
        except Conversation.DoesNotExist:
            return None

    def get(self, request, pk):
        conv = self._get_conversation(request, pk)
        if not conv:
            return Response({"detail": "Not found."}, status=404)
        return Response(ConversationSerializer(conv).data)

    def delete(self, request, pk):
        conv = self._get_conversation(request, pk)
        if not conv:
            return Response({"detail": "Not found."}, status=404)
        conv.delete()
        return Response(status=204)
