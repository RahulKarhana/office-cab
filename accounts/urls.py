from django.urls import path
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from .views import SignupAPIView, MeAPIView, UpdatePickupLocationAPIView

urlpatterns = [
    path("signup/", SignupAPIView.as_view(), name="signup"),
    path("update_pickup_location/", UpdatePickupLocationAPIView.as_view(), name="update-pickup-location"),
    path("login/", TokenObtainPairView.as_view(), name="login"),          # returns access + refresh
    path("token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("me/", MeAPIView.as_view(), name="me"),
]