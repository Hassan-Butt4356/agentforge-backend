from django.urls import path
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView, TokenBlacklistView
from .views import RegisterView, UserProfileView

urlpatterns = [
    path("register/", RegisterView.as_view(), name="auth-register"),
    path("login/", TokenObtainPairView.as_view(), name="auth-login"),
    path("refresh/", TokenRefreshView.as_view(), name="auth-token-refresh"),
    path("logout/", TokenBlacklistView.as_view(), name="auth-logout"),   # body: {"refresh": "<token>"}
    path("profile/", UserProfileView.as_view(), name="auth-profile"),
]
