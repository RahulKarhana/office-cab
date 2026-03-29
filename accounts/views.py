from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework import status

from .serializers import (
    SignupSerializer,
    MeSerializer,
    UpdatePickupLocationSerializer,
)


class SignupAPIView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = SignupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        return Response(
            {
                "message": "User created successfully",
                "user": MeSerializer(user).data,
            },
            status=status.HTTP_201_CREATED,
        )


class MeAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(MeSerializer(request.user).data)


class UpdatePickupLocationAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if request.user.role != "EMPLOYEE":
            return Response(
                {"error": "Only employees can update pickup location."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = UpdatePickupLocationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        request.user.pickup_location = serializer.validated_data.get("pickup_location", "")
        request.user.pickup_latitude = serializer.validated_data["latitude"]
        request.user.pickup_longitude = serializer.validated_data["longitude"]
        request.user.save(
            update_fields=[
                "pickup_location",
                "pickup_latitude",
                "pickup_longitude",
            ]
        )

        return Response(
            {
                "message": "Pickup location updated successfully.",
                "pickup_location": request.user.pickup_location,
                "pickup_latitude": request.user.pickup_latitude,
                "pickup_longitude": request.user.pickup_longitude,
            },
            status=status.HTTP_200_OK,
        )