from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework import status
from trips.services.notification_service import NotificationService
from .models import PickupLocationChangeRequest

from .serializers import (
    SignupSerializer,
    MeSerializer,
    UpdatePickupLocationSerializer,
    PickupLocationChangeRequestSerializer,
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
        user = request.user

        if user.role != "EMPLOYEE":
            return Response(
                {
                    "error": (
                        "Only employees can update pickup location."
                    )
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = UpdatePickupLocationSerializer(
            data=request.data
        )
        serializer.is_valid(raise_exception=True)

        pickup_location = (
            serializer.validated_data.get(
                "pickup_location",
                "",
            )
        )

        latitude = serializer.validated_data[
            "latitude"
        ]

        longitude = serializer.validated_data[
            "longitude"
        ]

        # =====================================================
        # FIRST LOCATION SETUP
        #
        # Employee has never saved a pickup location before.
        # Save directly without Admin approval.
        # =====================================================

        has_existing_location = (
            user.pickup_latitude is not None
            and user.pickup_longitude is not None
        )

        if not has_existing_location:

            user.pickup_location = pickup_location
            user.pickup_latitude = latitude
            user.pickup_longitude = longitude

            user.save(
                update_fields=[
                    "pickup_location",
                    "pickup_latitude",
                    "pickup_longitude",
                ]
            )

            return Response(
                {
                    "message": (
                        "Pickup location saved successfully."
                    ),
                    "location_saved": True,
                    "request_created": False,
                    "pickup_location": (
                        user.pickup_location
                    ),
                    "pickup_latitude": (
                        user.pickup_latitude
                    ),
                    "pickup_longitude": (
                        user.pickup_longitude
                    ),
                },
                status=status.HTTP_200_OK,
            )

        # =====================================================
        # EXISTING LOCATION
        #
        # Employee already has an approved location.
        # Do NOT overwrite it.
        # =====================================================

        pending_request = (
            PickupLocationChangeRequest.objects
            .filter(
                employee=user,
                status=(
                    PickupLocationChangeRequest
                    .STATUS_PENDING
                ),
            )
            .first()
        )

        if pending_request:
            return Response(
                {
                    "error": (
                        "You already have a pending "
                        "pickup location change request."
                    ),
                    "request_pending": True,
                    "request": (
                        PickupLocationChangeRequestSerializer(
                            pending_request
                        ).data
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # =====================================================
        # CREATE NEW CHANGE REQUEST
        # =====================================================

        location_request = (
            PickupLocationChangeRequest.objects.create(
                employee=user,

                old_pickup_location=(
                    user.pickup_location
                ),

                old_pickup_latitude=(
                    user.pickup_latitude
                ),

                old_pickup_longitude=(
                    user.pickup_longitude
                ),

                requested_pickup_location=(
                    pickup_location
                ),

                requested_pickup_latitude=(
                    latitude
                ),

                requested_pickup_longitude=(
                    longitude
                ),
            )
        )
        try:
            

            NotificationService.notify_admins(
                (
                    f"{user.username} requested a pickup location change "
                    f"to {pickup_location or 'a new map location'}."
                ),
                title="Pickup Location Change Request 📍",
                push_data={
                    "type": "PICKUP_LOCATION_CHANGE_REQUEST",
                    "request_id": str(location_request.id),
                    "employee_id": str(user.id),
                    "screen": "location_requests",
                },
            )

        except Exception as e:
            print(
                "LOCATION REQUEST ADMIN FCM ERROR:",
                e,
            )
        return Response(
            {
                "message": (
                    "Pickup location change request "
                    "submitted successfully. "
                    "Your current location will remain "
                    "active until Admin approves the request."
                ),
                "location_saved": False,
                "request_created": True,
                "request_pending": True,
                "request": (
                    PickupLocationChangeRequestSerializer(
                        location_request
                    ).data
                ),
            },
            status=status.HTTP_201_CREATED,
        )


class MyPickupLocationChangeRequestAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user

        if user.role != "EMPLOYEE":
            return Response(
                {
                    "error": (
                        "Only employees can access "
                        "pickup location requests."
                    )
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        pending_request = (
            PickupLocationChangeRequest.objects
            .filter(
                employee=user,
                status=PickupLocationChangeRequest.STATUS_PENDING,
            )
            .first()
        )

        if pending_request:
            return Response(
                {
                    "has_pending_request": True,
                    "request": (
                        PickupLocationChangeRequestSerializer(
                            pending_request
                        ).data
                    ),
                    "current_pickup_location": (
                        user.pickup_location
                    ),
                    "current_pickup_latitude": (
                        user.pickup_latitude
                    ),
                    "current_pickup_longitude": (
                        user.pickup_longitude
                    ),
                },
                status=status.HTTP_200_OK,
            )

        latest_request = (
            PickupLocationChangeRequest.objects
            .filter(employee=user)
            .order_by("-requested_at")
            .first()
        )

        return Response(
            {
                "has_pending_request": False,

                "latest_request": (
                    PickupLocationChangeRequestSerializer(
                        latest_request
                    ).data
                    if latest_request
                    else None
                ),

                "current_pickup_location": (
                    user.pickup_location
                ),

                "current_pickup_latitude": (
                    user.pickup_latitude
                ),

                "current_pickup_longitude": (
                    user.pickup_longitude
                ),
            },
            status=status.HTTP_200_OK,
        )