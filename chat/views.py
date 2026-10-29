import json
import logging

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from django.http import StreamingHttpResponse
from django.db.models import Count, Q
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


def _prepare_chat(request, agent_id):
    """
    Validate a chat request: input, agent access and readiness, conversation.
    Returns (agent, conversation, user_message, history), or a Response to send back.
    """
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

    return agent, conversation, user_message, history


def _save_turn(request, agent, conversation, user_message, answer, sources):
    """Persist both turns, creating the Conversation if this was its first message."""
    if conversation is None:
        conversation = Conversation.objects.create(
            agent=agent, user=request.user, title=user_message[:60]
        )
    Message.objects.create(conversation=conversation, role="user", content=user_message)
    Message.objects.create(conversation=conversation, role="assistant", content=answer, sources=sources)
    return conversation


class ChatView(APIView):
    """
    POST /api/agents/{agent_id}/chat/

    Flow:
      1. Validate input, agent access/readiness and the conversation (_prepare_chat).
      2. Pass message + history to ChatService (RAG agent).
         If the LLM fails → 503, nothing is saved (the client can retry).
      3. Save both the user message and assistant reply to DB.
      4. Return the answer and its sources.

    This variant is synchronous — the user waits for the whole answer.
    ChatStreamView is the streaming counterpart.
    """

    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(request=ChatInputSerializer, responses=ChatResponseSerializer)
    def post(self, request, agent_id):
        prepared = _prepare_chat(request, agent_id)
        if isinstance(prepared, Response):
            return prepared
        agent, conversation, user_message, history = prepared

        chat_service = ChatService(agent)
        try:
            answer, sources = chat_service.chat(user_message, history)
        except ChatServiceError:
            return Response(
                {"detail": "The AI model is unavailable right now. Please try again."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        conversation = _save_turn(request, agent, conversation, user_message, answer, sources)
        return Response(
            {"conversation_id": str(conversation.id), "answer": answer, "sources": sources},
            status=200,
        )


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


class ChatStreamView(APIView):
    """
    POST /api/agents/{agent_id}/chat/stream/

    Same request as ChatView, answered as Server-Sent Events:
      status  {"message": str}                         progress note
      token   {"text": str}                            next piece of the answer
      done    {"conversation_id", "answer", "sources"} complete answer, already saved
      error   {"detail": str}                          the model failed; nothing saved

    Validation failures are ordinary JSON error responses (they happen before
    the stream starts). If the client disconnects (Stop), the generator is closed
    before "done" and nothing is saved, matching the 503 behaviour of ChatView.
    """

    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        request=ChatInputSerializer,
        responses={(200, "text/event-stream"): OpenApiTypes.STR},
    )
    def post(self, request, agent_id):
        prepared = _prepare_chat(request, agent_id)
        if isinstance(prepared, Response):
            return prepared
        agent, conversation, user_message, history = prepared

        def events():
            try:
                for kind, payload in ChatService(agent).stream_chat(user_message, history):
                    if kind == "status":
                        yield _sse("status", {"message": payload})
                    elif kind == "token":
                        yield _sse("token", {"text": payload})
                    else:
                        answer, sources = payload
                        saved = _save_turn(request, agent, conversation, user_message, answer, sources)
                        yield _sse(
                            "done",
                            {"conversation_id": str(saved.id), "answer": answer, "sources": sources},
                        )
            except ChatServiceError:
                yield _sse("error", {"detail": "The AI model is unavailable right now. Please try again."})

        response = StreamingHttpResponse(events(), content_type="text/event-stream")
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"  # tell nginx not to buffer the stream
        return response


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

        conversations = (
            Conversation.objects.filter(agent_id=agent_id, user=request.user)
            .select_related("agent")
            .annotate(n_messages=Count("messages"))
        )
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(conversations, request, view=self)
        return paginator.get_paginated_response(ConversationListSerializer(page, many=True).data)


class AllConversationsView(APIView):
    """
    GET /api/conversations/
    Every conversation of the current user across all agents, newest first.
    Skips agents the user can no longer open (a shared agent made private again).
    """

    permission_classes = [permissions.IsAuthenticated]
    pagination_class = PageNumberPagination

    @extend_schema(responses=ConversationListSerializer(many=True))
    def get(self, request):
        conversations = (
            Conversation.objects.filter(user=request.user)
            .filter(Q(agent__owner=request.user) | Q(agent__is_public=True))
            .select_related("agent")
            .annotate(n_messages=Count("messages"))
        )
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
