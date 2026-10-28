from django.test import TestCase
from django.contrib.auth.models import User
from rest_framework.test import APIClient
from rest_framework import status
from unittest.mock import patch, MagicMock
from agents.models import Agent
from rag.services.chat_service import ChatServiceError
from .models import Conversation, Message


class ChatTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="chatuser", password="pass1234")
        self.client.force_authenticate(user=self.user)
        self.agent = Agent.objects.create(
            name="Test Agent",
            owner=self.user,
            is_indexed=True,
        )

    @patch("chat.views.ChatService")
    def test_chat_creates_conversation(self, MockChatService):
        mock_service = MagicMock()
        mock_service.chat.return_value = "This is the answer."
        MockChatService.return_value = mock_service

        res = self.client.post(
            f"/api/agents/{self.agent.id}/chat/",
            {"message": "What is in the document?"},
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn("answer", res.data)
        self.assertIn("conversation_id", res.data)
        self.assertEqual(res.data["answer"], "This is the answer.")

    @patch("chat.views.ChatService")
    def test_chat_continues_conversation(self, MockChatService):
        mock_service = MagicMock()
        mock_service.chat.return_value = "Follow-up answer."
        MockChatService.return_value = mock_service

        conv = Conversation.objects.create(
            agent=self.agent, user=self.user, title="Test conv"
        )
        res = self.client.post(
            f"/api/agents/{self.agent.id}/chat/",
            {"message": "Follow-up question", "conversation_id": str(conv.id)},
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(str(res.data["conversation_id"]), str(conv.id))

    @patch("chat.views.ChatService")
    def test_chat_llm_failure_returns_503_and_saves_nothing(self, MockChatService):
        MockChatService.return_value.chat.side_effect = ChatServiceError("ollama down")
        res = self.client.post(
            f"/api/agents/{self.agent.id}/chat/",
            {"message": "Hello?"},
        )
        self.assertEqual(res.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertFalse(Conversation.objects.exists())
        self.assertFalse(Message.objects.exists())

    def test_chat_agent_not_indexed_returns_400(self):
        unindexed = Agent.objects.create(
            name="Unindexed", owner=self.user, is_indexed=False
        )
        res = self.client.post(
            f"/api/agents/{unindexed.id}/chat/",
            {"message": "Hello?"},
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_list_conversations(self):
        Conversation.objects.create(agent=self.agent, user=self.user, title="Conv 1")
        Conversation.objects.create(agent=self.agent, user=self.user, title="Conv 2")
        res = self.client.get(f"/api/agents/{self.agent.id}/conversations/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["count"], 2)

    def test_delete_conversation(self):
        conv = Conversation.objects.create(
            agent=self.agent, user=self.user, title="To delete"
        )
        res = self.client.delete(f"/api/conversations/{conv.id}/")
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Conversation.objects.filter(id=conv.id).exists())

    def test_all_conversations_lists_only_mine_across_agents(self):
        other_agent = Agent.objects.create(name="Other", owner=self.user, is_indexed=True)
        stranger = User.objects.create_user(username="stranger", password="pass1234")
        Conversation.objects.create(agent=self.agent, user=self.user, title="Mine 1")
        c2 = Conversation.objects.create(agent=other_agent, user=self.user, title="Mine 2")
        Message.objects.create(conversation=c2, role="user", content="hi")
        Message.objects.create(conversation=c2, role="assistant", content="hello")
        Conversation.objects.create(agent=self.agent, user=stranger, title="Not mine")

        res = self.client.get("/api/conversations/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["count"], 2)
        by_title = {c["title"]: c for c in res.data["results"]}
        self.assertEqual(set(by_title), {"Mine 1", "Mine 2"})
        self.assertEqual(by_title["Mine 2"]["message_count"], 2)
        self.assertEqual(str(by_title["Mine 2"]["agent_id"]), str(other_agent.id))

    def test_all_conversations_hides_agents_no_longer_shared(self):
        owner = User.objects.create_user(username="sharer", password="pass1234")
        shared = Agent.objects.create(name="Was public", owner=owner, is_public=False)
        Conversation.objects.create(agent=shared, user=self.user, title="Lost access")
        res = self.client.get("/api/conversations/")
        self.assertEqual(res.data["count"], 0)
