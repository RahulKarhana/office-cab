from django.contrib.auth import get_user_model
from django.db import transaction
from django.http import Http404
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from trips.models import EmergencyAlert, Notification, SafeArrivalConfirmation, Trip
from trips.services.notification_service import NotificationService

User = get_user_model()


class SafeArrivalViewSet(viewsets.GenericViewSet):
    permission_classes = [IsAuthenticated]
    queryset = SafeArrivalConfirmation.objects.all()

    def get_queryset(self):
        if getattr(self.request.user, "role", "") != "EMPLOYEE":
            return SafeArrivalConfirmation.objects.none()
        return SafeArrivalConfirmation.objects.filter(
            employee=self.request.user,
            trip__trip_type=Trip.TRIP_TYPE_DROP,
            stop__is_boarded=True,
            stop__is_no_show=False,
            stop__is_picked=True,
        ).select_related("trip", "stop", "sos_alert", "employee")

    @staticmethod
    def _serialize(item):
        return {
            "id": item.id,
            "trip_id": item.trip_id,
            "stop_id": item.stop_id,
            "status": item.status,
            "no_count": item.no_count,
            "confirmed_at": item.confirmed_at,
            "sos_alert_id": item.sos_alert_id,
            "review_allowed": item.status == SafeArrivalConfirmation.STATUS_CONFIRMED,
        }

    @action(detail=False, methods=["get"], url_path="pending")
    def pending(self, request):
        if getattr(request.user, "role", "") != "EMPLOYEE":
            return Response({"error": "Only employees can access confirmations."}, status=403)

        item = self.get_queryset().filter(
            status=SafeArrivalConfirmation.STATUS_PENDING
        ).order_by("-created_at").first()
        return Response({
            "pending": item is not None,
            "confirmation": self._serialize(item) if item else None,
        })

    @action(detail=True, methods=["post"], url_path="respond")
    def respond(self, request, pk=None):
        if getattr(request.user, "role", "") != "EMPLOYEE":
            return Response({"error": "Only employees can respond."}, status=403)

        answer = str(request.data.get("answer", "")).strip().upper()
        if answer not in {"YES", "NO", "SOS"}:
            return Response({"error": "Answer must be YES, NO or SOS."}, status=400)

        with transaction.atomic():
            try:
                item = self.get_queryset().select_for_update().get(pk=pk)
            except SafeArrivalConfirmation.DoesNotExist:
                raise Http404

            if item.status != SafeArrivalConfirmation.STATUS_PENDING:
                return Response(self._serialize(item))

            if answer == "YES":
                item.status = SafeArrivalConfirmation.STATUS_CONFIRMED
                item.confirmed_at = timezone.now()
                item.save(update_fields=["status", "confirmed_at", "updated_at"])
                trip_id = item.trip_id
                employee_id = item.employee_id
                route_run_id = item.trip.route_run_id
                transaction.on_commit(
                    lambda: self._send_review_prompt(employee_id, trip_id, route_run_id)
                )

            elif answer == "NO" and item.no_count < 2:
                item.no_count += 1
                item.save(update_fields=["no_count", "updated_at"])

            else:
                if answer == "NO":
                    item.no_count = 3
                alert = self._create_sos(item)
                alert_id = alert.pk
                transaction.on_commit(lambda: self._notify_admins(alert_id))

            return Response(self._serialize(item))

    @staticmethod
    def _send_review_prompt(employee_id, trip_id, route_run_id):
        try:
            employee = User.objects.get(pk=employee_id)
            NotificationService.send_notification(
                employee,
                "Thank you for confirming that you reached home safely. "
                "Please submit your trip review.",
                title="Safe Arrival Confirmed ✅",
                push_data={
                    "type": "TRIP_COMPLETED",
                    "trip_id": str(trip_id),
                    "route_run_id": str(route_run_id or ""),
                    "trip_type": "DROP",
                    "screen": "review",
                },
            )
        except Exception as exc:
            # Confirmation stays persisted; failed notification should be retried operationally.
            print("SAFE ARRIVAL REVIEW NOTIFICATION ERROR:", exc)

    @staticmethod
    def _create_sos(item):
        trip = item.trip
        alert = EmergencyAlert.objects.create(
            employee=item.employee,
            trip=trip,
            route_run=trip.route_run,
            title="Safe Arrival Emergency SOS",
            message=(
                f"{item.employee.display_name} reported not reaching home safely "
                f"after DROP. Trip #{trip.pk}."
            ),
            pickup_location=trip.pickup_location or "",
            drop_location=trip.drop_location or "",
        )
        item.status = SafeArrivalConfirmation.STATUS_SOS
        item.sos_alert = alert
        item.save(update_fields=["status", "sos_alert", "no_count", "updated_at"])
        return alert

    @staticmethod
    def _notify_admins(alert_id):
        try:
            alert = EmergencyAlert.objects.select_related("employee", "route_run").get(pk=alert_id)
            NotificationService.notify_admins(
                f"{alert.employee.display_name} needs urgent assistance after DROP. "
                f"Trip #{alert.trip_id}.",
                title="🚨 Safe Arrival SOS",
                notification_type=Notification.TYPE_SOS,
                priority=Notification.PRIORITY_CRITICAL,
                route_run=alert.route_run,
                employee=alert.employee,
                push_data={
                    "type": "SOS_ALERT",
                    "alert_id": str(alert.pk),
                    "employee_id": str(alert.employee_id),
                    "trip_id": str(alert.trip_id or ""),
                    "route_run_id": str(alert.route_run_id or ""),
                    "screen": "emergency_alerts",
                },
            )
        except Exception as exc:
            print("SAFE ARRIVAL SOS ADMIN NOTIFICATION ERROR:", exc)
