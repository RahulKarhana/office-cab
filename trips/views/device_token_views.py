from rest_framework import status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from trips.models import DeviceToken
from trips.serializers import DeviceTokenSerializer


class DeviceTokenViewSet(viewsets.ModelViewSet):
    serializer_class = DeviceTokenSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return DeviceToken.objects.filter(user=self.request.user)

    def create(self, request, *args, **kwargs):
        token = request.data.get("token")
        device_type = request.data.get("device_type", "ANDROID").upper()

        if not token:
            return Response(
                {"error": "Token is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Create or update token
        device_token, created = DeviceToken.objects.update_or_create(
            token=token,
            defaults={
                "user": request.user,
                "device_type": device_type,
                "is_active": True,
            },
        )

        # Return proper serialized response
        serializer = self.get_serializer(device_token)

        return Response(serializer.data, status=status.HTTP_200_OK)