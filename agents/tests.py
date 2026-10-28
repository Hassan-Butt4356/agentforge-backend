from unittest.mock import patch, MagicMock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.contrib.auth.models import User
from rest_framework.test import APIClient
from rest_framework import status
from .models import Agent, Document

PDF_BYTES = b"%PDF-1.4\n%fake minimal pdf\n%%EOF"


class AgentTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(username="owner", password="pass1234")
        self.other = User.objects.create_user(username="other", password="pass1234")
        self.client.force_authenticate(user=self.owner)

    def _create_agent(self, name="Test Agent", is_public=False):
        res = self.client.post("/api/agents/", {
            "name": name,
            "description": "Test",
            "is_public": is_public,
        })
        return res

    def test_create_agent(self):
        res = self._create_agent()
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data["name"], "Test Agent")
        self.assertIn("id", res.data)
        self.assertEqual(res.data["indexing_status"], "idle")

    def test_list_own_agents(self):
        self._create_agent("A1")
        self._create_agent("A2")
        res = self.client.get("/api/agents/my/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["count"], 2)
        self.assertNotIn("documents", res.data["results"][0])

    def test_public_agents_visible_to_others(self):
        self._create_agent("Public Agent", is_public=True)
        self.client.force_authenticate(user=self.other)
        res = self.client.get("/api/agents/public/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["count"], 1)

    def test_private_agent_not_visible_to_others(self):
        agent_res = self._create_agent("Private Agent", is_public=False)
        agent_id = agent_res.data["id"]
        self.client.force_authenticate(user=self.other)
        res = self.client.get(f"/api/agents/{agent_id}/")
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_other_user_cannot_delete_agent(self):
        agent_res = self._create_agent()
        agent_id = agent_res.data["id"]
        self.client.force_authenticate(user=self.other)
        res = self.client.delete(f"/api/agents/{agent_id}/")
        self.assertIn(res.status_code, [status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND])

    @patch("rag.indexes.chroma_store.delete_collection")
    def test_owner_can_delete_agent(self, mock_delete_collection):
        agent_res = self._create_agent()
        agent_id = agent_res.data["id"]
        collection = Agent.objects.get(id=agent_id).chroma_collection_name
        res = self.client.delete(f"/api/agents/{agent_id}/")
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)
        mock_delete_collection.assert_called_once_with(collection)

    def test_upload_without_files_returns_400(self):
        agent_res = self._create_agent()
        agent_id = agent_res.data["id"]
        res = self.client.post(
            f"/api/agents/{agent_id}/upload/",
            {},
            format="multipart",
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_upload_rejects_non_pdf(self):
        agent_id = self._create_agent().data["id"]
        fake = SimpleUploadedFile("notes.pdf", b"just text", content_type="application/pdf")
        res = self.client.post(f"/api/agents/{agent_id}/upload/", {"files": [fake]}, format="multipart")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Document.objects.exists())

    def test_upload_rejects_wrong_extension(self):
        agent_id = self._create_agent().data["id"]
        fake = SimpleUploadedFile("notes.txt", PDF_BYTES, content_type="text/plain")
        res = self.client.post(f"/api/agents/{agent_id}/upload/", {"files": [fake]}, format="multipart")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    @patch("rag.tasks.ingestion_tasks.ingest_document_task.delay")
    def test_upload_pdf_queues_task(self, mock_delay):
        mock_delay.return_value = MagicMock(id="task-123")
        agent_id = self._create_agent().data["id"]
        pdf = SimpleUploadedFile("paper.pdf", PDF_BYTES, content_type="application/pdf")
        with override_settings(MEDIA_ROOT=self._tmp_media()):
            res = self.client.post(f"/api/agents/{agent_id}/upload/", {"files": [pdf]}, format="multipart")
        self.assertEqual(res.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(res.data["queued"][0]["task_id"], "task-123")
        self.assertEqual(Agent.objects.get(id=agent_id).indexing_status, "indexing")

    def test_status_reports_document_progress(self):
        agent = Agent.objects.get(id=self._create_agent().data["id"])
        Document.objects.create(agent=agent, file="pdfs/a.pdf", filename="a.pdf", ingested=True)
        Document.objects.create(agent=agent, file="pdfs/b.pdf", filename="b.pdf", ingested=False)
        res = self.client.get(f"/api/agents/{agent.id}/status/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["documents_total"], 2)
        self.assertEqual(res.data["documents_ingested"], 1)

    def _tmp_media(self):
        import tempfile, shutil
        path = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, path, ignore_errors=True)
        return path


class DocumentDeleteTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(username="owner", password="pass1234")
        self.other = User.objects.create_user(username="other", password="pass1234")
        self.agent = Agent.objects.create(name="A", owner=self.owner, is_public=True, is_indexed=True, indexing_status="ready")
        self.doc = Document.objects.create(agent=self.agent, file="pdfs/a.pdf", filename="a.pdf", ingested=True)

    def _url(self, doc_id=None):
        return f"/api/agents/{self.agent.id}/documents/{doc_id or self.doc.id}/"

    @patch("rag.services.ingestion_service.delete_document_vectors", return_value=3)
    def test_owner_deletes_last_document_and_agent_goes_idle(self, mock_vectors):
        self.client.force_authenticate(user=self.owner)
        res = self.client.delete(self._url())
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Document.objects.filter(id=self.doc.id).exists())
        mock_vectors.assert_called_once()
        self.assertEqual(mock_vectors.call_args.args[1], str(self.doc.id))
        self.agent.refresh_from_db()
        self.assertEqual((self.agent.indexing_status, self.agent.is_indexed), ("idle", False))

    @patch("rag.services.ingestion_service.delete_document_vectors", return_value=2)
    def test_deleting_one_of_several_keeps_agent_ready(self, _):
        Document.objects.create(agent=self.agent, file="pdfs/b.pdf", filename="b.pdf", ingested=True)
        self.client.force_authenticate(user=self.owner)
        self.assertEqual(self.client.delete(self._url()).status_code, status.HTTP_204_NO_CONTENT)
        self.agent.refresh_from_db()
        self.assertEqual((self.agent.indexing_status, self.agent.is_indexed), ("ready", True))

    @patch("rag.services.ingestion_service.delete_document_vectors", return_value=0)
    def test_deleting_the_broken_document_clears_error(self, _):
        self.agent.indexing_status = "error"
        self.agent.save()
        broken = Document.objects.create(agent=self.agent, file="pdfs/bad.pdf", filename="bad.pdf", ingested=False)
        self.client.force_authenticate(user=self.owner)
        self.assertEqual(self.client.delete(self._url(broken.id)).status_code, status.HTTP_204_NO_CONTENT)
        self.agent.refresh_from_db()
        self.assertEqual(self.agent.indexing_status, "ready")

    def test_cannot_delete_while_embedding(self):
        self.agent.indexing_status = "indexing"
        self.agent.save()
        pending = Document.objects.create(agent=self.agent, file="pdfs/p.pdf", filename="p.pdf", ingested=False)
        self.client.force_authenticate(user=self.owner)
        self.assertEqual(self.client.delete(self._url(pending.id)).status_code, status.HTTP_409_CONFLICT)
        self.assertTrue(Document.objects.filter(id=pending.id).exists())

    def test_non_owner_cannot_delete(self):
        self.client.force_authenticate(user=self.other)
        self.assertEqual(self.client.delete(self._url()).status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(Document.objects.filter(id=self.doc.id).exists())

    def test_unknown_document_is_404(self):
        self.client.force_authenticate(user=self.owner)
        res = self.client.delete(self._url("00000000-0000-0000-0000-000000000000"))
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
