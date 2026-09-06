from django.urls import path
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from .views import (
    SignupAPIView,
    MeAPIView,
    UpdatePickupLocationAPIView,
    MyPickupLocationChangeRequestAPIView,
)

urlpatterns = [
    path(
        "signup/",
        SignupAPIView.as_view(),
        name="signup",
    ),

    path(
        "update_pickup_location/",
        UpdatePickupLocationAPIView.as_view(),
        name="update-pickup-location",
    ),

    path(
        "pickup-location-request/",
        MyPickupLocationChangeRequestAPIView.as_view(),
        name="my-pickup-location-request",
    ),

    path(
        "login/",
        TokenObtainPairView.as_view(),
        name="login",
    ),

    path(
        "token/refresh/",
        TokenRefreshView.as_view(),
        name="token_refresh",
    ),

    path(
        "me/",
        MeAPIView.as_view(),
        name="me",
    ),
]