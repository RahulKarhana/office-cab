from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from trips.models import EmergencyAlert, Notification, Trip
from trips.serializers import EmergencyAlertSerializer

User = get_user_model()


class EmergencyAlertViewSet(viewsets.ModelViewSet):
    serializer_class = EmergencyAlertSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user

        qs = EmergencyAlert.objects.select_related(
            "employee",
            "trip",
            "route_run",
            "route_run__route_template",
        ).order_by("-created_at")

        if user.role == "ADMIN":
            return qs

        if user.role == "EMPLOYEE":
            return qs.filter(employee=user)

        return EmergencyAlert.objects.none()

    def create(self, request, *args, **kwargs):
        user = request.user

        if user.role != "EMPLOYEE":
            return Response(
                {"error": "Only employee can send emergency alert."},
                status=status.HTTP_403_FORBIDDEN,
            )

        active_trip = Trip.objects.select_related(
            "route_run",
            "route_run__route_template",
            "vehicle",
        ).filter(
            employee=user,
            status__in=[Trip.STATUS_ASSIGNED, Trip.STATUS_STARTED],
        ).order_by("-created_at").first()

        latitude = request.data.get("latitude")
        longitude = request.data.get("longitude")

        pickup_location = ""
        drop_location = ""
        route_run = None
        trip_type = "TRIP"
        vehicle_number = "--"
        route_name = "Unknown Route"

        if active_trip:
            pickup_location = active_trip.pickup_location or ""
            drop_location = active_trip.drop_location or ""
            route_run = active_trip.route_run
            trip_type = active_trip.trip_type
            vehicle_number = (
                active_trip.vehicle.vehicle_number if active_trip.vehicle else "--"
            )

            if active_trip.route_run and active_trip.route_run.route_template:
                route_name = active_trip.route_run.route_template.name

        message = (
            f"EMERGENCY ALERT from {user.username}. "
            f"Trip Type: {trip_type}. "
            f"Route: {route_name}. "
            f"Vehicle: {vehicle_number}. "
            f"Pickup: {pickup_location}. "
            f"Drop: {drop_location}."
        )

        alert = EmergencyAlert.objects.create(
            employee=user,
            trip=active_trip,
            route_run=route_run,
            title="Emergency SOS",
            message=message,
            latitude=latitude,
            longitude=longitude,
            pickup_location=pickup_location,
            drop_location=drop_location,
        )

        admins = User.objects.filter(role="ADMIN", is_active=True)
        for admin in admins:
            Notification.objects.create(
                user=admin,
                title="🚨 Emergency Alert",
                message=message,
                is_read=False,
            )

        return Response(
            EmergencyAlertSerializer(alert).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"], url_path="mark_as_read")
    def mark_as_read(self, request, pk=None):
        if request.user.role != "ADMIN":
            return Response(
                {"error": "Only admin can mark emergency alert as read."},
                status=status.HTTP_403_FORBIDDEN,
            )

        alert = self.get_object()
        alert.mark_as_read()

        Notification.objects.filter(
            user=request.user,
            is_read=False,
            title__icontains="emergency",
            message=alert.message,
        ).update(is_read=True)

        return Response(
            {"message": "Emergency alert marked as read."},
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="mark_all_as_read")
    def mark_all_as_read(self, request):
        if request.user.role != "ADMIN":
            return Response(
                {"error": "Only admin can mark all emergency alerts as read."},
                status=status.HTTP_403_FORBIDDEN,
            )

        pending_alerts = EmergencyAlert.objects.filter(
            status=EmergencyAlert.STATUS_PENDING
        )

        messages = list(pending_alerts.values_list("message", flat=True))

        pending_alerts.update(
            status=EmergencyAlert.STATUS_READ,
            read_at=timezone.now(),
        )

        if messages:
            Notification.objects.filter(
                user=request.user,
                is_read=False,
                title__icontains="emergency",
                message__in=messages,
            ).update(is_read=True)

        return Response(
            {"message": "All emergency alerts marked as read."},
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["get"], url_path="unread_count")
    def unread_count(self, request):
        if request.user.role != "ADMIN":
            return Response(
                {"count": 0},
                status=status.HTTP_200_OK,
            )

        count = EmergencyAlert.objects.filter(
            status=EmergencyAlert.STATUS_PENDING
        ).count()

        return Response({"count": count}, status=status.HTTP_200_OK)