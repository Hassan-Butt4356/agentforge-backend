import logging

from drf_spectacular.utils import extend_schema
from rest_framework import status, permissions
from rest_framework.pagination import PageNumberPagination
from rest_framework.views import APIView
from rest_framework.response import Response

from agents.models import Agent
from .models import Conversation, Message
from .serializers import (
    ChatInputSerializer,
    ChatResponseSerializer,
    ConversationSerializer,
    ConversationListSerializer,
)
from rag.services.chat_service import ChatService, ChatServiceError

logger = logging.getLogger(__name__)


class ChatView(APIView):
    """
    POST /api/agents/{agent_id}/chat/

    Flow:
      1. Validate message input.
      2. Check the agent exists, is accessible, and is_indexed=True.
      3. Load the Conversation + its history if conversation_id was given.
      4. (A new Conversation is only created after a successful answer.)
      5. Pass message + history to ChatService (RAG agent).
         If the LLM fails → 503, nothing is saved (the client can retry).
      6. Save both the user message and assistant reply to DB.
      7. Return the answer.

    The chat is synchronous — the user waits for the LLM to respond.
    This is intentional: streaming or async chat is a future enhancement.
    """

    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(request=ChatInputSerializer, responses=ChatResponseSerializer)
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

        # Load existing conversation (created only after a successful answer)
        conversation = None
        history = []
        if conversation_id:
            try:
                conversation = Conversation.objects.get(id=conversation_id, user=request.user, agent=agent)
            except Conversation.DoesNotExist:
                return Response({"detail": "Conversation not found."}, status=404)
            history = list(conversation.messages.order_by("created_at").values("role", "content"))

        # Run RAG
        chat_service = ChatService(agent)
        try:
            answer = chat_service.chat(user_message, history)
        except ChatServiceError:
            return Response(
                {"detail": "The AI model is unavailable right now. Please try again."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        if conversation is None:
            conversation = Conversation.objects.create(
                agent=agent, user=request.user, title=user_message[:60]
            )

        # Persist both turns
        Message.objects.create(conversation=conversation, role="user", content=user_message)
        Message.objects.create(conversation=conversation, role="assistant", content=answer)

        return Response(
            {"conversation_id": str(conversation.id), "answer": answer},
            status=200,
        )


class ConversationListView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = PageNumberPagination

    @extend_schema(responses=ConversationListSerializer(many=True))
    def get(self, request, agent_id):
        try:
            agent = Agent.objects.get(pk=agent_id)
        except Agent.DoesNotExist:
            return Response({"detail": "Agent not found."}, status=404)

        if not agent.is_public and agent.owner != request.user:
            return Response({"detail": "Access denied."}, status=403)

        conversations = Conversation.objects.filter(agent_id=agent_id, user=request.user)
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(conversations, request, view=self)
        return paginator.get_paginated_response(ConversationListSerializer(page, many=True).data)


class ConversationDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def _get_conversation(self, request, pk):
        try:
            return Conversation.objects.get(pk=pk, user=request.user)
        except Conversation.DoesNotExist:
            return None

    @extend_schema(responses=ConversationSerializer)
    def get(self, request, pk):
        conv = self._get_conversation(request, pk)
        if not conv:
            return Response({"detail": "Not found."}, status=404)
        return Response(ConversationSerializer(conv).data)

    @extend_schema(responses={204: None})
    def delete(self, request, pk):
        conv = self._get_conversation(request, pk)
        if not conv:
            return Response({"detail": "Not found."}, status=404)
        conv.delete()
        return Response(status=204)
