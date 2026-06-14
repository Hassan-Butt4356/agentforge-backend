from django.test import TestCase
from django.contrib.auth.models import User
from rest_framework.test import APIClient
from rest_framework import status


class AuthTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.register_url = "/api/auth/register/"
        self.login_url = "/api/auth/login/"
        self.profile_url = "/api/auth/profile/"

    def test_register_success(self):
        res = self.client.post(self.register_url, {
            "username": "testuser",
            "email": "test@example.com",
            "password": "strongpass1",
            "password2": "strongpass1",
        })
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertIn("username", res.data)

    def test_register_password_mismatch(self):
        res = self.client.post(self.register_url, {
            "username": "testuser",
            "email": "test@example.com",
            "password": "strongpass1",
            "password2": "wrongpass",
        })
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_login_returns_tokens(self):
        User.objects.create_user(username="u", password="pass1234")
        res = self.client.post(self.login_url, {"username": "u", "password": "pass1234"})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn("access", res.data)
        self.assertIn("refresh", res.data)

    def test_profile_requires_auth(self):
        res = self.client.get(self.profile_url)
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_profile_authenticated(self):
        user = User.objects.create_user(username="me", password="pass1234")
        self.client.force_authenticate(user=user)
        res = self.client.get(self.profile_url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["username"], "me")
