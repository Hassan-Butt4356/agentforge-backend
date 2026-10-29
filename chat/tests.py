import json
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
        source = {
            "document_id": "6f1c1c1e-0000-4000-8000-000000000001",
            "filename": "report.pdf",
            "page": "4",
            "snippet": "Revenue grew 12%.",
            "score": 0.82,
        }
        mock_service.chat.return_value = ("This is the answer.", [source])
        MockChatService.return_value = mock_service

        res = self.client.post(
            f"/api/agents/{self.agent.id}/chat/",
            {"message": "What is in the document?"},
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn("answer", res.data)
        self.assertIn("conversation_id", res.data)
        self.assertEqual(res.data["answer"], "This is the answer.")
        self.assertEqual(res.data["sources"], [source])

        # Sources are persisted and come back when the conversation is reopened.
        detail = self.client.get(f"/api/conversations/{res.data['conversation_id']}/")
        user_msg, assistant_msg = detail.data["messages"]
        self.assertEqual(user_msg["sources"], [])
        self.assertEqual(assistant_msg["sources"][0]["filename"], "report.pdf")
        self.assertEqual(assistant_msg["sources"][0]["page"], "4")

    @patch("chat.views.ChatService")
    def test_chat_continues_conversation(self, MockChatService):
        mock_service = MagicMock()
        mock_service.chat.return_value = ("Follow-up answer.", [])
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


class ExtractSourcesTests(TestCase):
    def test_collapses_chunks_per_page_and_sorts_by_score(self):
        from types import SimpleNamespace
        from llama_index.core.schema import NodeWithScore, TextNode
        from rag.services.chat_service import _extract_sources

        def node(text, page, score, doc="a"):
            meta = {"file_name": "report.pdf", "page_label": page, "document_id": doc}
            return NodeWithScore(node=TextNode(text=text, metadata=meta), score=score)

        response = SimpleNamespace(
            source_nodes=[node("low", "2", 0.3), node("best", "2", 0.9), node("other", "5", 0.5)]
        )
        sources = _extract_sources(response)
        self.assertEqual([(s["page"], s["snippet"]) for s in sources], [("2", "best"), ("5", "other")])
        self.assertEqual(sources[0]["filename"], "report.pdf")
        self.assertEqual(_extract_sources(SimpleNamespace(source_nodes=[])), [])


def _parse_sse(response):
    body = b"".join(response.streaming_content).decode()
    events = []
    for block in filter(None, body.split("\n\n")):
        lines = dict(line.split(": ", 1) for line in block.split("\n"))
        events.append((lines["event"], json.loads(lines["data"])))
    return events


class ChatStreamTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="streamer", password="pass1234")
        self.client.force_authenticate(user=self.user)
        self.agent = Agent.objects.create(name="Streamy", owner=self.user, is_indexed=True)
        self.url = f"/api/agents/{self.agent.id}/chat/stream/"

    @patch("chat.views.ChatService")
    def test_streams_tokens_then_saves_on_done(self, MockChatService):
        source = {"document_id": None, "filename": "a.pdf", "page": "1", "snippet": "x", "score": 0.5}
        MockChatService.return_value.stream_chat.return_value = iter(
            [("status", "Searching…"), ("token", "Hello "), ("token", "world"), ("done", ("Hello world", [source]))]
        )
        res = self.client.post(self.url, {"message": "Hi"}, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["Content-Type"], "text/event-stream")
        self.assertEqual(res["X-Accel-Buffering"], "no")

        events = _parse_sse(res)
        self.assertEqual([e for e, _ in events], ["status", "token", "token", "done"])
        done = events[-1][1]
        self.assertEqual(done["answer"], "Hello world")
        self.assertEqual(done["sources"], [source])
        conv = Conversation.objects.get(id=done["conversation_id"])
        self.assertEqual(list(conv.messages.values_list("role", flat=True)), ["user", "assistant"])
        self.assertEqual(conv.messages.get(role="assistant").sources, [source])

    @patch("chat.views.ChatService")
    def test_model_failure_emits_error_and_saves_nothing(self, MockChatService):
        def broken():
            yield "status", "Searching…"
            raise ChatServiceError("ollama down")

        MockChatService.return_value.stream_chat.return_value = broken()
        events = _parse_sse(self.client.post(self.url, {"message": "Hi"}, format="json"))
        self.assertEqual([e for e, _ in events], ["status", "error"])
        self.assertFalse(Conversation.objects.exists())
        self.assertFalse(Message.objects.exists())

    @patch("chat.views.ChatService")
    def test_disconnect_before_done_saves_nothing(self, MockChatService):
        MockChatService.return_value.stream_chat.return_value = iter(
            [("status", "Searching…"), ("token", "Hel"), ("done", ("Hello", []))]
        )
        res = self.client.post(self.url, {"message": "Hi"}, format="json")
        next(iter(res.streaming_content))  # read the first event, then the client goes away (Stop)
        res.close()
        self.assertFalse(Message.objects.exists())

    def test_validation_errors_are_plain_json(self):
        unindexed = Agent.objects.create(name="Nope", owner=self.user, is_indexed=False)
        res = self.client.post(f"/api/agents/{unindexed.id}/chat/stream/", {"message": "Hi"}, format="json")
        self.assertEqual(res.status_code, 400)


class StripReactPreambleTests(TestCase):
    def run_strip(self, chunks):
        from rag.services.chat_service import _strip_react_preamble
        return "".join(_strip_react_preamble(iter(chunks)))

    def test_drops_marker_tail_from_first_chunk(self):
        self.assertEqual(self.run_strip(["wer: Rev", "enue up"]), "Revenue up")
        self.assertEqual(self.run_strip([" Hell", "o"]), "Hello")
        self.assertEqual(self.run_strip(["r: Hi"]), "Hi")

    def test_marker_fully_inside_first_chunk(self):
        self.assertEqual(self.run_strip(["Thought: ok\nAnswer: Hi", " there"]), "Hi there")

    def test_marker_tail_alone_in_first_chunk_is_skipped(self):
        self.assertEqual(self.run_strip(["wer: ", "Hello"]), "Hello")

    def test_plain_answer_is_untouched(self):
        self.assertEqual(self.run_strip(["Hello ", "there"]), "Hello there")
