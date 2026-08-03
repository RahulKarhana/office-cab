from datetime import datetime, time

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Case, When, Value, IntegerField
from django.utils import timezone

from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from trips.models import (
    Notification,
    Trip,
    TripCancellation,
    Vehicle,
    RouteRunStop,
    RouteRun,
    EmployeeLeave,
)
from trips.serializers import (
    TripSerializer,
    UserOptionSerializer,
    VehicleOptionSerializer,
    AssignedCabGroupSerializer,
)
from trips.services.notification_service import NotificationService
from trips.services.route_service import RouteService
from trips.services.eta_service import ETAService
from trips.services.chat_service import ChatService

User = get_user_model()


class TripViewSet(ModelViewSet):
    serializer_class = TripSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user

        base_qs = Trip.objects.select_related(
            "employee",
            "driver",
            "vehicle",
            "route_run",
            "route_run__route_template",
        ).prefetch_related(
            "route_run__stops__employee",
        )

        if user.role == "ADMIN":
            return base_qs.order_by("-created_at")

        if user.role == "DRIVER":
            return base_qs.filter(driver=user).order_by("-created_at")

        if user.role == "EMPLOYEE":
            return base_qs.filter(employee=user).order_by("-created_at")

        return Trip.objects.none()

    def _active_statuses(self):
        return [Trip.STATUS_ASSIGNED, Trip.STATUS_STARTED]

    def _closed_statuses(self):
        return [Trip.STATUS_COMPLETED, Trip.STATUS_CANCELLED]

    def _is_after_10am_today(self):
        now = timezone.localtime()
        today = timezone.localdate()

        ten_am_today = timezone.make_aware(
            datetime.combine(today, time(10, 0)),
            timezone.get_current_timezone(),
        )

        return now >= ten_am_today

    def _should_send_cancel_notification(self, trip):
        return bool(trip.notification_sent and self._is_after_10am_today())

    def _send_admin_cancel_notification_if_needed(self, trip):
        if not self._should_send_cancel_notification(trip):
            return False

        NotificationService.send_notification(
            trip.employee,
            f"Your {trip.trip_type.lower()} cab has been cancelled by admin.",
            title="❌ Cab Cancelled",
            push_data={
                "type": "TRIP_CANCELLED",
                "trip_id": str(trip.id),
                "trip_type": trip.trip_type,
                "route_run_id": str(trip.route_run_id or ""),
            },
        )

        if trip.driver:
            NotificationService.send_notification(
                trip.driver,
                f"{trip.trip_type.capitalize()} trip for {trip.employee.username} has been cancelled by admin.",
                title="❌ Trip Cancelled",
                push_data={
                    "type": "TRIP_CANCELLED",
                    "trip_id": str(trip.id),
                    "trip_type": trip.trip_type,
                    "route_run_id": str(trip.route_run_id or ""),
                },
            )

        return True

    def _cancel_route_run_if_possible(self, trip):
        if not trip.route_run:
            return

        try:
            if hasattr(trip.route_run, "status"):
                trip.route_run.status = "CANCELLED"
                trip.route_run.save(update_fields=["status"])
        except Exception:
            pass

    @action(detail=False, methods=["get"], url_path="active")
    def active_trip(self, request):
        user = request.user

        base_qs = Trip.objects.select_related(
            "employee",
            "driver",
            "vehicle",
            "route_run",
            "route_run__route_template",
        ).prefetch_related(
            "route_run__stops__employee",
        )

        if user.role == "EMPLOYEE":
            trip = base_qs.filter(employee=user, status__in=self._active_statuses()).order_by("-created_at").first()
        elif user.role == "DRIVER":
            trip = base_qs.filter(driver=user, status__in=self._active_statuses()).order_by("-created_at").first()
        elif user.role == "ADMIN":
            trip = base_qs.filter(status__in=self._active_statuses()).order_by("-created_at").first()
        else:
            trip = None

        if not trip:
            return Response({"detail": "No active trip"}, status=status.HTTP_404_NOT_FOUND)

        return Response(self.get_serializer(trip).data, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], url_path="my-live-pickup-status")
    def my_live_pickup_status(self, request):
        user = request.user

        if user.role != "EMPLOYEE":
            return Response(
                {"error": "Only employee can view live route status."},
                status=status.HTTP_403_FORBIDDEN,
            )

        trip = (
            Trip.objects.select_related(
                "employee",
                "driver",
                "vehicle",
                "route_run",
                "route_run__route_template",
            )
            .prefetch_related("route_run__stops__employee")
            .filter(
                employee=user,
                status=Trip.STATUS_STARTED,
                route_run__started_at__isnull=False,
                route_run__completed_at__isnull=True,
            )
            .order_by("-created_at")
            .first()
        )

        if not trip:
            return Response(
                {"detail": "No live route running right now."},
                status=status.HTTP_404_NOT_FOUND,
            )

        route_run = trip.route_run

        if not route_run:
            return Response(
                {"detail": "No route found for this trip."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if route_run.started_at is None:
            return Response(
                {"detail": "Route has not started yet."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if route_run.completed_at is not None:
            return Response(
                {"detail": "Route already completed."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if not RouteService.get_ordered_stops(route_run).exists():
            return Response(
                {"detail": "No stops found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        data = ETAService.build_employee_live_pickup_status(
            trip,
            user,
        )

        return Response(data, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], url_path="assigned-cabs")
    def assigned_cabs(self, request):
        if request.user.role != "ADMIN":
            raise PermissionDenied("Only admin can view assigned cabs.")

        trips = Trip.objects.select_related(
            "employee", "driver", "vehicle", "route_run", "route_run__route_template"
        ).prefetch_related(
            "route_run__stops__employee"
        ).filter(
            status__in=self._active_statuses()
        ).order_by("-pickup_time", "route_run_id", "employee__username")

        grouped = {}

        for trip in trips:
            group_key = f"route_run_{trip.route_run_id}" if trip.route_run_id else f"single_trip_{trip.id}"

            if group_key not in grouped:
                grouped[group_key] = {
                    "route_run_id": trip.route_run_id,
                    "route_name": trip.route_run.route_template.name if trip.route_run and trip.route_run.route_template else "Manual Trip",
                    "trip_type": trip.trip_type,
                    "driver_id": trip.driver.id if trip.driver else None,
                    "driver_name": trip.driver.username if trip.driver else None,
                    "vehicle_id": trip.vehicle.id if trip.vehicle else None,
                    "vehicle_number": trip.vehicle.vehicle_number if trip.vehicle else None,
                    "pickup_time": trip.pickup_time,
                    "status": trip.status,
                    "total_employees": 0,
                    "employees": [],
                }

            grouped[group_key]["employees"].append({
                "trip_id": trip.id,
                "employee_id": trip.employee.id,
                "employee_name": trip.employee.username,
                "pickup_location": trip.pickup_location,
                "drop_location": trip.drop_location,
                "status": trip.status,
            })
            grouped[group_key]["total_employees"] += 1

            if trip.status == Trip.STATUS_STARTED:
                grouped[group_key]["status"] = Trip.STATUS_STARTED

        serializer = AssignedCabGroupSerializer(list(grouped.values()), many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"], url_path="cancel-route-run")
    def cancel_route_run(self, request):
        if request.user.role != "ADMIN":
            raise PermissionDenied("Only admin can cancel assigned routes.")

        route_run_id = request.data.get("route_run_id")
        if not route_run_id:
            return Response({"error": "route_run_id is required."}, status=status.HTTP_400_BAD_REQUEST)

        trips = Trip.objects.select_related("employee", "driver").filter(
            route_run_id=route_run_id,
            status__in=self._active_statuses(),
        )

        if not trips.exists():
            return Response({"error": "No active trips found for this assigned route."}, status=status.HTTP_404_NOT_FOUND)

        cancelled_count = 0
        with transaction.atomic():
            for trip in trips:
                trip.status = Trip.STATUS_CANCELLED
                trip.save(update_fields=["status"])
                TripCancellation.objects.create(trip=trip, cancelled_by=request.user, reason="Cancelled by admin from assigned cab group")
                self._send_admin_cancel_notification_if_needed(trip)
                cancelled_count += 1

        return Response({"message": f"{cancelled_count} trip(s) cancelled successfully."}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="cancel-trip")
    def cancel_trip_by_admin(self, request, pk=None):
        if request.user.role != "ADMIN":
            raise PermissionDenied("Only admin can cancel trips.")

        trip = self.get_object()
        if trip.status in self._closed_statuses():
            return Response({"detail": f"Trip already {trip.status.lower()}."}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            trip.status = Trip.STATUS_CANCELLED
            trip.save(update_fields=["status"])
            self._cancel_route_run_if_possible(trip)
            TripCancellation.objects.create(trip=trip, cancelled_by=request.user, reason="Cancelled by admin")
            self._send_admin_cancel_notification_if_needed(trip)

        return Response({"detail": "Trip cancelled successfully."}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"], url_path="reset-active-trips")
    def reset_active_trips(self, request):
        if request.user.role != "ADMIN":
            raise PermissionDenied("Only admin can reset active trips.")

        active_trips = Trip.objects.select_related("employee", "driver", "route_run").filter(status__in=self._active_statuses())

        if not active_trips.exists():
            return Response({"detail": "No active trips found."}, status=status.HTTP_200_OK)

        total = active_trips.count()
        with transaction.atomic():
            for trip in active_trips:
                trip.status = Trip.STATUS_CANCELLED
                trip.save(update_fields=["status"])
                self._cancel_route_run_if_possible(trip)
                TripCancellation.objects.create(trip=trip, cancelled_by=request.user, reason="Cancelled by admin reset")
                self._send_admin_cancel_notification_if_needed(trip)

        return Response({"detail": f"{total} active trips reset successfully."}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], url_path="upcoming")
    def upcoming_trips(self, request):
        user = request.user
        if user.role != "EMPLOYEE":
            return Response({"error": "Only employee can view upcoming trips."}, status=status.HTTP_403_FORBIDDEN)

        today = timezone.localdate()
        qs = (
            Trip.objects.select_related("employee", "driver", "vehicle", "route_run", "route_run__route_template")
            .prefetch_related("route_run__stops__employee")
            .filter(employee=user, status__in=self._active_statuses(), pickup_time__date__gte=today)
            .annotate(
                status_priority=Case(
                    When(status=Trip.STATUS_STARTED, then=Value(0)),
                    When(status=Trip.STATUS_ASSIGNED, then=Value(1)),
                    default=Value(2),
                    output_field=IntegerField(),
                )
            )
            .order_by("status_priority", "pickup_time", "-created_at")
        )

        return Response(self.get_serializer(qs, many=True).data, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], url_path="create_form_data")
    def create_form_data(self, request):
        if request.user.role != "ADMIN":
            return Response({"error": "Only admin can access create form data."}, status=status.HTTP_403_FORBIDDEN)

        employees = User.objects.filter(role="EMPLOYEE", is_active=True).order_by("username")
        drivers = User.objects.filter(role="DRIVER", is_active=True).order_by("username")
        vehicles = Vehicle.objects.select_related("driver").all().order_by("vehicle_number")

        return Response({
            "employees": UserOptionSerializer(employees, many=True).data,
            "drivers": UserOptionSerializer(drivers, many=True).data,
            "vehicles": VehicleOptionSerializer(vehicles, many=True).data,
        }, status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"], url_path="send_notifications_by_date")
    def send_notifications_by_date(self, request):
        if request.user.role != "ADMIN":
            return Response({"error": "Only admin can send notifications."}, status=status.HTTP_403_FORBIDDEN)

        date_str = request.data.get("date")
        if not date_str:
            return Response({"error": "Date is required in YYYY-MM-DD format."}, status=status.HTTP_400_BAD_REQUEST)

        trips = Trip.objects.filter(pickup_time__date=date_str, notification_sent=False).select_related("employee", "driver", "vehicle")
        sent_count = 0

        for trip in trips:
            if trip.trip_type == Trip.TRIP_TYPE_PICKUP:
                employee_message = f"Your pickup trip is scheduled on {trip.pickup_time.strftime('%d-%m-%Y %H:%M')} from {trip.pickup_location} to {trip.drop_location}."
                driver_message = f"You have a pickup trip on {trip.pickup_time.strftime('%d-%m-%Y %H:%M')} for {trip.employee.username} from {trip.pickup_location} to {trip.drop_location}."
            else:
                employee_message = f"Your drop trip is scheduled on {trip.pickup_time.strftime('%d-%m-%Y %H:%M')} from {trip.pickup_location} to {trip.drop_location}."
                driver_message = f"You have a drop trip on {trip.pickup_time.strftime('%d-%m-%Y %H:%M')} for {trip.employee.username} from {trip.pickup_location} to {trip.drop_location}."

            NotificationService.send_notification(trip.employee, employee_message)
            NotificationService.send_notification(trip.driver, driver_message)
            trip.notification_sent = True
            trip.save(update_fields=["notification_sent"])
            sent_count += 1

        return Response({"message": f"Notifications sent successfully for {sent_count} trip(s).", "sent_count": sent_count}, status=status.HTTP_200_OK)

    def perform_create(self, serializer):
        if self.request.user.role != "ADMIN":
            raise PermissionDenied("Only Admin can create trips.")

        trip = serializer.save()
        if trip.driver:
            NotificationService.send_notification(trip.driver, f"You have been assigned trip {trip.id}.")
        if trip.employee:
            NotificationService.send_notification(trip.employee, "Your trip has been assigned successfully.")

    def perform_update(self, serializer):
        if self.request.user.role != "ADMIN":
            raise PermissionDenied("Only Admin can update trips.")
        serializer.save()

    def perform_destroy(self, instance):
        if self.request.user.role != "ADMIN":
            raise PermissionDenied("Only Admin can delete trips.")
        instance.delete()

    @action(detail=False, methods=["post"], url_path="mark-leave")
    def mark_leave(self, request):
        if request.user.role != "EMPLOYEE":
            return Response({"error": "Only employee can mark leave."}, status=status.HTTP_403_FORBIDDEN)

        leave_date = request.data.get("leave_date")
        reason = request.data.get("reason", "").strip()
        if not leave_date:
            return Response({"error": "leave_date is required in YYYY-MM-DD format."}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            leave, created = EmployeeLeave.objects.get_or_create(employee=request.user, leave_date=leave_date, defaults={"reason": reason})
            if not created:
                leave.reason = reason
                leave.save(update_fields=["reason"])

            trips = Trip.objects.select_related("driver", "employee", "route_run").filter(employee=request.user, trip_date=leave_date, status=Trip.STATUS_ASSIGNED)
            cancelled_count = 0

            for trip in trips:
                trip.status = Trip.STATUS_CANCELLED
                trip.save(update_fields=["status"])

                if trip.route_run_id:
                    RouteRunStop.objects.filter(route_run=trip.route_run, employee=trip.employee).update(is_no_show=True, no_show_at=timezone.now())

                TripCancellation.objects.create(
                    trip=trip,
                    cancelled_by=request.user,
                    reason=reason or "Marked leave by employee",
                    declaration_accepted=False,
                    declaration_text="",
                    cancelled_by_role=request.user.role,
                )
                cancelled_count += 1

            started_trips_count = Trip.objects.filter(employee=request.user, trip_date=leave_date, status=Trip.STATUS_STARTED).count()

        NotificationService.notify_admins(
            f"{request.user.username} marked leave for {leave_date}. {cancelled_count} assigned trip(s) auto-cancelled.",
            title="Employee Leave Marked",
            push_data={"type": "EMPLOYEE_LEAVE", "employee_id": str(request.user.id), "leave_date": str(leave_date)},
        )

        return Response({"message": "Leave marked successfully.", "leave_date": leave_date, "cancelled_trips": cancelled_count, "started_trips_not_cancelled": started_trips_count, "driver_fcm_sent": False}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="employee-cancel")
    def cancel_trip(self, request, pk=None):
        trip = self.get_object()
        SELF_TRAVEL_REASON = "I will come office by self"

        if request.user.role != "EMPLOYEE":
            return Response({"error": "Only employee can cancel trip"}, status=status.HTTP_403_FORBIDDEN)
        if trip.employee != request.user:
            return Response({"error": "You can cancel only your own trip"}, status=status.HTTP_403_FORBIDDEN)
        if trip.status == Trip.STATUS_STARTED:
            return Response({"error": "You can't cancel the cab after trip started."}, status=status.HTTP_400_BAD_REQUEST)
        if trip.status == Trip.STATUS_COMPLETED:
            return Response({"error": "Completed trip cannot be cancelled."}, status=status.HTTP_400_BAD_REQUEST)
        if trip.status == Trip.STATUS_CANCELLED:
            return Response({"error": "Trip is already cancelled."}, status=status.HTTP_400_BAD_REQUEST)

        reason = request.data.get("reason", "").strip()
        declaration_accepted = request.data.get("declaration_accepted", False)
        if not reason:
            return Response({"error": "Cancellation reason is required."}, status=status.HTTP_400_BAD_REQUEST)

        declaration_required = False
        if trip.trip_type == Trip.TRIP_TYPE_DROP:
            pickup_trip = Trip.objects.filter(employee=request.user, trip_type=Trip.TRIP_TYPE_PICKUP, trip_date=trip.trip_date).order_by("-created_at").first()
            if pickup_trip and pickup_trip.status != Trip.STATUS_CANCELLED:
                return Response({"error": "First cancel your today's pickup."}, status=status.HTTP_400_BAD_REQUEST)
            if pickup_trip and pickup_trip.status == Trip.STATUS_CANCELLED:
                pickup_cancellation = TripCancellation.objects.filter(trip=pickup_trip, cancelled_by=request.user).order_by("-cancelled_at").first()
                if pickup_cancellation and pickup_cancellation.reason.lower() == SELF_TRAVEL_REASON.lower():
                    declaration_required = True

        if declaration_required and not declaration_accepted:
            return Response({
                "error": "Self declaration is required for drop cancellation.",
                "declaration_required": True,
                "declaration_text": "I confirm that I came to office by myself and I am cancelling my drop cab by my own choice. I will manage my return travel myself.",
            }, status=status.HTTP_400_BAD_REQUEST)

        declaration_text = ""
        if declaration_required:
            declaration_text = "I confirm that I came to office by myself and I am cancelling my drop cab by my own choice. I will manage my return travel myself."

        with transaction.atomic():
            trip.cancel()
            if trip.route_run_id:
                RouteRunStop.objects.filter(route_run=trip.route_run, employee=trip.employee).update(is_no_show=True, no_show_at=timezone.now())
            TripCancellation.objects.create(
                trip=trip,
                cancelled_by=request.user,
                reason=reason,
                declaration_accepted=bool(declaration_accepted),
                declaration_text=declaration_text,
                cancelled_by_role=request.user.role,
            )

        if trip.driver:
            NotificationService.send_notification(
                trip.driver,
                f"{trip.trip_type.capitalize()} trip cancelled by {trip.employee.username}. Reason: {reason}",
                title="❌ Trip Cancelled",
                push_data={"type": "TRIP_CANCELLED", "trip_id": str(trip.id), "trip_type": trip.trip_type, "reason": reason, "screen": "driver_route"},
            )

        NotificationService.notify_admins(
            f"{trip.trip_type.capitalize()} trip cancelled by {trip.employee.username}. Reason: {reason}.",
            title="❌ Employee Trip Cancelled",
            push_data={"type": "TRIP_CANCELLED", "trip_id": str(trip.id), "trip_type": trip.trip_type, "reason": reason, "screen": "assigned_cabs"},
        )

        return Response({"message": "Trip cancelled successfully.", "trip_id": trip.id, "trip_type": trip.trip_type, "reason": reason, "declaration_required": declaration_required, "declaration_accepted": bool(declaration_accepted)}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"])
    def start_trip(self, request, pk=None):
        trip = self.get_object()

        if request.user.role != "DRIVER":
            return Response(
                {"error": "Only driver can start trip"},
                status=status.HTTP_403_FORBIDDEN,
            )

        if trip.driver != request.user:
            return Response(
                {"error": "You are not assigned to this trip"},
                status=status.HTTP_403_FORBIDDEN,
            )

        route_run = trip.route_run

        # Route-based trip: start the complete route only once.
        if route_run:
            if route_run.completed_at is not None:
                return Response(
                    {"error": "Route run is already completed."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if route_run.started_at is not None:
                return Response(
                    {
                        "message": "Route run was already started.",
                        "already_started": True,
                        "route_run_id": route_run.id,
                        "started_at": route_run.started_at,
                    },
                    status=status.HTTP_200_OK,
                )

            with transaction.atomic():
                started_time = timezone.now()

                route_run.started_at = started_time
                route_run.save(update_fields=["started_at"])

                Trip.objects.filter(
                    route_run=route_run,
                    status=Trip.STATUS_ASSIGNED,
                ).update(
                    status=Trip.STATUS_STARTED,
                    start_time=started_time,
                    driver=route_run.driver,
                    vehicle=route_run.vehicle,
                )

                trip.refresh_from_db()

                RouteService.handle_trip_start_notifications(
                    trip,
                )

            return Response(
                {
                    "message": "Route trip started successfully.",
                    "already_started": False,
                    "trip_id": trip.id,
                    "route_run_id": route_run.id,
                    "trip_type": route_run.trip_type,
                    "started_at": route_run.started_at,
                },
                status=status.HTTP_200_OK,
            )

        # Standalone trip without RouteRun.
        try:
            trip.start()
        except ValueError as exc:
            return Response(
                {"error": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        RouteService.handle_trip_start_notifications(trip)

        NotificationService.notify_admins(
            (
                f"{trip.trip_type.capitalize()} trip "
                f"{trip.id} has started by "
                f"driver {trip.driver.username}."
            ),
            title="🚖 Trip Started",
            trip=trip,
            driver=trip.driver,
            employee=trip.employee,
            push_data={
                "type": "TRIP_STARTED",
                "trip_id": str(trip.id),
                "trip_type": trip.trip_type,
                "screen": "admin_dashboard",
            },
        )

        return Response(
            {
                "message": "Trip started successfully",
                "trip_id": trip.id,
                "trip_type": trip.trip_type,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="pickup-done")
    def pickup_done(self, request):
        if request.user.role != "DRIVER":
            return Response(
                {"error": "Only driver can mark stop done."},
                status=status.HTTP_403_FORBIDDEN,
            )

        stop_id = request.data.get("stop_id")

        if not stop_id:
            return Response(
                {"error": "stop_id is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        current_stop = (
            RouteRunStop.objects.select_related(
                "route_run",
                "employee",
                "route_run__driver",
                "route_run__vehicle",
                "route_run__route_template",
            )
            .filter(id=stop_id)
            .first()
        )

        if not current_stop:
            return Response(
                {"error": "Stop not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        route_run = current_stop.route_run

        if route_run.driver != request.user:
            return Response(
                {
                    "error": (
                        "You are not assigned to this route."
                    )
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        if route_run.started_at is None:
            return Response(
                {"error": "Start the route before completing a stop."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if route_run.completed_at is not None:
            return Response(
                {"error": "This route is already completed."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        active_trip_exists = Trip.objects.filter(
            route_run=route_run,
            driver=request.user,
            status=Trip.STATUS_STARTED,
        ).exists()

        if not active_trip_exists:
            return Response(
                {
                    "error": (
                        "No active trip was found for this route."
                    )
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        expected_stop = RouteService.get_current_stop(route_run)

        if not expected_stop:
            return Response(
                {"detail": "All route stops are already finished."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if expected_stop.id != current_stop.id:
            return Response(
                {
                    "error": (
                        "This is not the current route stop."
                    ),
                    "current_stop_id": expected_stop.id,
                    "current_employee": (
                        expected_stop.employee.username
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if current_stop.is_picked:
            return Response(
                {"detail": "This stop is already marked done."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if current_stop.is_no_show:
            return Response(
                {"detail": "This stop is already marked no-show."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            next_stop = RouteService.handle_stop_done(
                route_run,
                current_stop,
            )

            remaining_stops, route_ready_to_complete = (
                RouteService.complete_route_if_finished(
                    route_run,
                )
            )

            ChatService.close_chat_for_stop(
                route_run,
                current_stop,
            )

        action_word = (
            "Drop"
            if route_run.trip_type == Trip.TRIP_TYPE_DROP
            else "Pickup"
        )

        return Response(
            {
                "message": f"{action_word} marked successfully.",
                "current_stop_id": current_stop.id,
                "next_stop_id": (
                    next_stop.id if next_stop else None
                ),
                "next_employee": (
                    next_stop.employee.username
                    if next_stop
                    else None
                ),
                "remaining_stops": remaining_stops,
                "route_completed": False,
                "ready_to_complete": route_ready_to_complete,
                "trip_type": route_run.trip_type,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="mark-arrived")
    def mark_arrived(self, request):
        if request.user.role != "DRIVER":
            return Response(
                {"error": "Only driver can perform this action."},
                status=status.HTTP_403_FORBIDDEN,
            )

        route_run_id = request.data.get("route_run_id")

        if not route_run_id:
            return Response(
                {"error": "route_run_id is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        route_run = (
            RouteRun.objects.select_related(
                "driver",
                "vehicle",
                "route_template",
            )
            .prefetch_related("stops__employee")
            .filter(
                id=route_run_id,
                driver=request.user,
                started_at__isnull=False,
                completed_at__isnull=True,
            )
            .first()
        )

        if not route_run:
            return Response(
                {"error": "Active route run not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if route_run.trip_type != Trip.TRIP_TYPE_PICKUP:
            return Response(
                {
                    "error": (
                        "Driver Has Arrived is available "
                        "only for pickup routes."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        current_stop = RouteService.mark_arrived(route_run)

        if not current_stop:
            return Response(
                {"error": "No active stop found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        chat = ChatService.ensure_chat_for_stop(
            route_run,
            current_stop,
        )

        waiting_minutes = (
            getattr(current_stop, "waiting_minutes", 10)
            or 10
        )

        return Response(
            {
                "message": (
                    "Driver had already marked arrival."
                    if current_stop.arrival_time
                    else "Arrival marked."
                ),
                "stop_id": current_stop.id,
                "employee": current_stop.employee.username,
                "waiting_started_at": (
                    current_stop.waiting_started_at
                ),
                "trip_type": route_run.trip_type,
                "driver_has_arrived": True,
                "chat_enabled": True,
                "chat_id": chat.id if chat else None,
                "countdown_seconds": waiting_minutes * 60,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="keep-waiting")
    def keep_waiting(self, request):
        if request.user.role != "DRIVER":
            return Response(
                {"error": "Only driver can perform this action."},
                status=status.HTTP_403_FORBIDDEN,
            )

        route_run_id = request.data.get("route_run_id")

        if not route_run_id:
            return Response(
                {"error": "route_run_id is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        route_run = RouteRun.objects.filter(
            id=route_run_id,
            driver=request.user,
            started_at__isnull=False,
            completed_at__isnull=True,
        ).first()

        if not route_run:
            return Response(
                {"error": "Active route run not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if route_run.trip_type != Trip.TRIP_TYPE_PICKUP:
            return Response(
                {
                    "error": (
                        "Keep Waiting is available only "
                        "for pickup routes."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        current_stop = RouteService.get_current_stop(route_run)

        if not current_stop:
            return Response(
                {"error": "No active stop found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if current_stop.arrival_time is None:
            return Response(
                {
                    "error": (
                        "Mark Driver Has Arrived before "
                        "using Keep Waiting."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        current_stop = RouteService.keep_waiting(route_run)

        waiting_minutes = (
            getattr(current_stop, "waiting_minutes", 10)
            or 10
        )

        return Response(
            {
                "message": "Waiting continued.",
                "stop_id": current_stop.id,
                "employee": current_stop.employee.username,
                "waiting_started_at": (
                    current_stop.waiting_started_at
                ),
                "trip_type": route_run.trip_type,
                "driver_has_arrived": True,
                "chat_enabled": True,
                "countdown_seconds": waiting_minutes * 60,
                "keep_waiting_count": getattr(
                    current_stop,
                    "keep_waiting_count",
                    0,
                ),
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="mark-no-show")
    def mark_no_show(self, request):
        if request.user.role != "DRIVER":
            return Response(
                {"error": "Only driver can perform this action."},
                status=status.HTTP_403_FORBIDDEN,
            )

        route_run_id = request.data.get("route_run_id")

        if not route_run_id:
            return Response(
                {"error": "route_run_id is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        route_run = RouteRun.objects.filter(
            id=route_run_id,
            driver=request.user,
            started_at__isnull=False,
            completed_at__isnull=True,
        ).first()

        if not route_run:
            return Response(
                {"error": "Active route run not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if route_run.trip_type != Trip.TRIP_TYPE_PICKUP:
            return Response(
                {
                    "error": (
                        "No Show is available only for "
                        "pickup routes."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        current_stop = RouteService.get_current_stop(route_run)

        if not current_stop:
            return Response(
                {"error": "No active stop found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if current_stop.arrival_time is None:
            return Response(
                {
                    "error": (
                        "Mark Driver Has Arrived before "
                        "marking No Show."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            (
                completed_stop,
                next_stop,
                remaining_stops,
                route_ready_to_complete,
            ) = RouteService.mark_no_show(
                route_run,
                request.user,
            )

            if completed_stop:
                ChatService.close_chat_for_stop(
                    route_run,
                    completed_stop,
                )

        if not completed_stop:
            return Response(
                {"error": "No active stop found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(
            {
                "message": "Marked No Show.",
                "current_stop_id": completed_stop.id,
                "next_stop_id": (
                    next_stop.id if next_stop else None
                ),
                "next_employee": (
                    next_stop.employee.username
                    if next_stop
                    else None
                ),
                "remaining_stops": remaining_stops,
                "route_completed": False,
                "ready_to_complete": route_ready_to_complete,
                "trip_type": route_run.trip_type,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"], url_path="complete-trip")
    def complete_trip(self, request, pk=None):
        trip = self.get_object()

        if request.user.role != "DRIVER":
            return Response(
                {"error": "Only driver can complete trip"},
                status=status.HTTP_403_FORBIDDEN,
            )

        if trip.driver != request.user:
            return Response(
                {"error": "You are not assigned to this trip"},
                status=status.HTTP_403_FORBIDDEN,
            )

        route_run = trip.route_run

        if route_run:
            if route_run.driver != request.user:
                return Response(
                    {
                        "error": (
                            "You are not assigned to "
                            "this route run."
                        )
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )

            if route_run.completed_at is not None:
                return Response(
                    {
                        "message": "Route was already completed.",
                        "route_completed": True,
                        "route_run_id": route_run.id,
                    },
                    status=status.HTTP_200_OK,
                )

            remaining_stops, ready_to_complete = (
                RouteService.complete_route_if_finished(
                    route_run,
                )
            )

            if not ready_to_complete:
                return Response(
                    {
                        "error": (
                            "Complete all remaining route "
                            "stops before finishing the trip."
                        ),
                        "remaining_stops": remaining_stops,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            with transaction.atomic():
                completed_time = timezone.now()

                route_run.completed_at = completed_time
                route_run.save(
                    update_fields=["completed_at"],
                )

                Trip.objects.filter(
                    route_run=route_run,
                ).exclude(
                    status=Trip.STATUS_CANCELLED,
                ).update(
                    status=Trip.STATUS_COMPLETED,
                    end_time=completed_time,
                )

                RouteService.notify_route_completed(
                    route_run,
                )

            late_minutes = (
                RouteService.calculate_late_minutes(
                    route_run,
                    completed_at=completed_time,
                )
            )

            return Response(
                {
                    "message": "Route trip completed successfully.",
                    "route_completed": True,
                    "route_run_id": route_run.id,
                    "trip_type": route_run.trip_type,
                    "completed_at": completed_time,
                    "late_minutes": late_minutes,
                },
                status=status.HTTP_200_OK,
            )

        # Standalone trip without RouteRun.
        try:
            trip.complete()
        except ValueError as exc:
            return Response(
                {"error": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        NotificationService.send_notification(
            trip.employee,
            (
                "Today's trip has been completed successfully. "
                "Please submit your review."
            ),
            title="✅ Trip Completed",
            push_data={
                "type": "TRIP_COMPLETED",
                "trip_id": str(trip.id),
                "trip_type": trip.trip_type,
                "screen": "review",
            },
        )

        NotificationService.notify_admins(
            (
                f"Trip {trip.id} has been completed by "
                f"driver {trip.driver.username}."
            ),
            title="✅ Trip Completed",
            trip=trip,
            driver=trip.driver,
            employee=trip.employee,
            push_data={
                "type": "TRIP_COMPLETED",
                "trip_id": str(trip.id),
                "trip_type": trip.trip_type,
                "screen": "admin_dashboard",
            },
        )

        return Response(
            {
                "message": "Trip completed successfully",
                "trip_id": trip.id,
                "trip_type": trip.trip_type,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["get"], url_path="history")
    def history(self, request):
        user = request.user
        base_qs = Trip.objects.select_related("employee", "driver", "vehicle", "route_run", "route_run__route_template").prefetch_related("route_run__stops__employee")

        if user.role == "EMPLOYEE":
            qs = base_qs.filter(employee=user, status__in=self._closed_statuses()).order_by("-created_at")
        elif user.role == "DRIVER":
            qs = base_qs.filter(driver=user, status__in=self._closed_statuses()).order_by("-created_at")
        elif user.role == "ADMIN":
            qs = base_qs.filter(status__in=self._closed_statuses()).order_by("-created_at")
        else:
            qs = Trip.objects.none()

        return Response(self.get_serializer(qs, many=True).data, status=status.HTTP_200_OK)
