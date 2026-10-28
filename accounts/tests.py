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

    def test_logout_blacklists_refresh_token(self):
        User.objects.create_user(username="u", password="pass1234")
        tokens = self.client.post(self.login_url, {"username": "u", "password": "pass1234"}).data
        res = self.client.post("/api/auth/logout/", {"refresh": tokens["refresh"]})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        res = self.client.post("/api/auth/refresh/", {"refresh": tokens["refresh"]})
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)


    def test_access_token_lives_15_minutes(self):
        from rest_framework_simplejwt.tokens import AccessToken
        User.objects.create_user(username="ttl", password="pass1234")
        access = AccessToken(self.client.post(self.login_url, {"username": "ttl", "password": "pass1234"}).data["access"])
        self.assertEqual(access["exp"] - access["iat"], 15 * 60)

class PasswordChangeTests(TestCase):
    url = "/api/auth/password/"

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username="pw", password="old-pass-123")
        self.tokens = self.client.post("/api/auth/login/", {"username": "pw", "password": "old-pass-123"}).data
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.tokens['access']}")

    def _change(self, current="old-pass-123", new="new-pass-456", new2=None):
        return self.client.post(self.url, {"current_password": current, "new_password": new, "new_password2": new2 or new})

    def test_change_password_returns_new_tokens_and_blacklists_old(self):
        res = self._change()
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn("access", res.data)
        self.assertIn("refresh", res.data)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("new-pass-456"))
        # The old refresh token (other sessions) no longer works; the new one does.
        anon = APIClient()
        self.assertEqual(anon.post("/api/auth/refresh/", {"refresh": self.tokens["refresh"]}).status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(anon.post("/api/auth/refresh/", {"refresh": res.data["refresh"]}).status_code, status.HTTP_200_OK)

    def test_wrong_current_password(self):
        res = self._change(current="nope")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("current_password", res.data)

    def test_mismatched_confirmation(self):
        res = self._change(new2="something-else-789")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("new_password2", res.data)

    def test_django_password_validators_apply(self):
        res = self._change(new="12345678")  # entirely numeric and common
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_requires_auth(self):
        self.assertEqual(APIClient().post(self.url, {}).status_code, status.HTTP_401_UNAUTHORIZED)
