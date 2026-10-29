from django.urls import path
from .views import AllConversationsView, ChatStreamView, ChatView, ConversationDetailView, ConversationListView

urlpatterns = [
    path("agents/<uuid:agent_id>/chat/", ChatView.as_view(), name="agent-chat"),
    path("agents/<uuid:agent_id>/chat/stream/", ChatStreamView.as_view(), name="agent-chat-stream"),
    path("agents/<uuid:agent_id>/conversations/", ConversationListView.as_view(), name="agent-conversations"),
    path("conversations/", AllConversationsView.as_view(), name="conversations"),
    path("conversations/<uuid:pk>/", ConversationDetailView.as_view(), name="conversation-detail"),
]
