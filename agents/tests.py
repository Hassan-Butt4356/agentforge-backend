from django.test import TestCase
from django.contrib.auth.models import User
from rest_framework.test import APIClient
from rest_framework import status
from .models import Agent


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

    def test_list_own_agents(self):
        self._create_agent("A1")
        self._create_agent("A2")
        res = self.client.get("/api/agents/my/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data), 2)

    def test_public_agents_visible_to_others(self):
        self._create_agent("Public Agent", is_public=True)
        self.client.force_authenticate(user=self.other)
        res = self.client.get("/api/agents/public/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data), 1)

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

    def test_owner_can_delete_agent(self):
        agent_res = self._create_agent()
        agent_id = agent_res.data["id"]
        res = self.client.delete(f"/api/agents/{agent_id}/")
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)

    def test_upload_without_files_returns_400(self):
        agent_res = self._create_agent()
        agent_id = agent_res.data["id"]
        res = self.client.post(
            f"/api/agents/{agent_id}/upload/",
            {},
            format="multipart",
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
