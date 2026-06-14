from django.urls import path
from .views import ChatView, ConversationListView, ConversationDetailView

urlpatterns = [
    path("agents/<uuid:agent_id>/chat/", ChatView.as_view(), name="agent-chat"),
    path("agents/<uuid:agent_id>/conversations/", ConversationListView.as_view(), name="agent-conversations"),
    path("conversations/<uuid:pk>/", ConversationDetailView.as_view(), name="conversation-detail"),
]
