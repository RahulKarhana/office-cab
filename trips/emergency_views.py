from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from trips.models import EmergencyAlert, Trip
from trips.serializers import EmergencyAlertSerializer
from trips.utils.notification import send_push_notification

User = get_user_model()


class EmergencyAlertViewSet(viewsets.ModelViewSet):
    serializer_class = EmergencyAlertSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        user = self.request.user
        qs = EmergencyAlert.objects.select_related(
            "employee", "trip", "route_run", "route_run__route_template",
        ).order_by("-created_at")
        if user.role == "ADMIN":
            return qs
        if user.role == "EMPLOYEE":
            return qs.filter(employee=user)
        return qs.none()

    def create(self, request, *args, **kwargs):
        user = request.user
        if user.role != "EMPLOYEE":
            return Response({"error": "Only employee can send emergency alert."}, status=403)

        active_trip = Trip.objects.select_related(
            "route_run", "route_run__route_template", "vehicle",
        ).filter(
            employee=user, status__in=[Trip.STATUS_ASSIGNED, Trip.STATUS_STARTED],
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
            vehicle_number = active_trip.vehicle.vehicle_number if active_trip.vehicle else "--"
            if active_trip.route_run and active_trip.route_run.route_template:
                route_name = active_trip.route_run.route_template.name

        message = (
            f"EMERGENCY ALERT from {user.display_name}. "
            f"Trip Type: {trip_type}. Route: {route_name}. "
            f"Vehicle: {vehicle_number}. Pickup: {pickup_location}. "
            f"Drop: {drop_location}."
        )
        alert = EmergencyAlert.objects.create(
            employee=user, trip=active_trip, route_run=route_run,
            title="Emergency SOS", message=message,
            latitude=latitude, longitude=longitude,
            pickup_location=pickup_location, drop_location=drop_location,
        )
        admins = User.objects.filter(role="ADMIN", is_active=True)
        for admin in admins:
            try:
                send_push_notification(
                    user=admin,
                    title="🚨 Emergency SOS Alert",
                    body=f"{user.display_name} needs urgent help. Open emergency alerts now.",
                    data={
                        "type": "SOS_ALERT", "alert_id": str(alert.id),
                        "employee_id": str(user.id),
                        "trip_id": str(active_trip.id) if active_trip else "",
                        "route_run_id": str(route_run.id) if route_run else "",
                        "screen": "emergency_alerts",
                    },
                )
            except Exception as exc:
                print("SOS ADMIN FCM ERROR:", exc)
        return Response(EmergencyAlertSerializer(alert, context={"request": request}).data, status=201)

    @action(detail=True, methods=["post"])
    def mark_as_read(self, request, pk=None):
        alert = self.get_object()
        now = timezone.now()
        if request.user.role == "EMPLOYEE":
            if alert.employee_acknowledged_at is None:
                alert.employee_acknowledged_at = now
                alert.save(update_fields=["employee_acknowledged_at"])
        elif request.user.role == "ADMIN":
            if alert.admin_acknowledged_at is None:
                alert.admin_acknowledged_at = now
                alert.save(update_fields=["admin_acknowledged_at"])
        else:
            return Response({"error": "Permission denied."}, status=403)
        return Response({"status": "marked as read", "alert_id": alert.pk, "is_read": True})

    @action(detail=False, methods=["post"])
    def mark_all_as_read(self, request):
        qs = self.get_queryset()
        now = timezone.now()
        if request.user.role == "EMPLOYEE":
            count = qs.filter(employee_acknowledged_at__isnull=True).update(employee_acknowledged_at=now)
        elif request.user.role == "ADMIN":
            count = qs.filter(admin_acknowledged_at__isnull=True).update(admin_acknowledged_at=now)
        else:
            return Response({"error": "Permission denied."}, status=403)
        return Response({"status": "all marked as read", "updated_count": count})

    @action(detail=False, methods=["get"])
    def unread_count(self, request):
        qs = self.get_queryset()
        if request.user.role == "EMPLOYEE":
            count = qs.filter(employee_acknowledged_at__isnull=True).count()
        elif request.user.role == "ADMIN":
            count = qs.filter(admin_acknowledged_at__isnull=True).count()
        else:
            count = 0
        return Response({"count": count})
