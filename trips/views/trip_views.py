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

from trips.models import Notification, Trip, TripCancellation, Vehicle
from trips.serializers import (
    TripSerializer,
    UserOptionSerializer,
    VehicleOptionSerializer,
    AssignedCabGroupSerializer,
)

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

    def _cancel_route_run_if_possible(self, trip):
        if not trip.route_run:
            return

        try:
            if hasattr(trip.route_run, "status"):
                trip.route_run.status = "CANCELLED"
                trip.route_run.save(update_fields=["status"])
        except Exception:
            pass

    def _notify_admins(self, message):
        admins = User.objects.filter(role="ADMIN", is_active=True)
        for admin in admins:
            self.send_notification(admin, message)

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
            trip = (
                base_qs.filter(
                    employee=user,
                    status__in=self._active_statuses(),
                )
                .order_by("-created_at")
                .first()
            )
        elif user.role == "DRIVER":
            trip = (
                base_qs.filter(
                    driver=user,
                    status__in=self._active_statuses(),
                )
                .order_by("-created_at")
                .first()
            )
        elif user.role == "ADMIN":
            trip = (
                base_qs.filter(
                    status__in=self._active_statuses(),
                )
                .order_by("-created_at")
                .first()
            )
        else:
            trip = None

        if not trip:
            return Response(
                {"detail": "No active trip"},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(
            self.get_serializer(trip).data,
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["get"], url_path="assigned-cabs")
    def assigned_cabs(self, request):
        if request.user.role != "ADMIN":
            raise PermissionDenied("Only admin can view assigned cabs.")

        trips = Trip.objects.select_related(
            "employee",
            "driver",
            "vehicle",
            "route_run",
            "route_run__route_template",
        ).prefetch_related(
            "route_run__stops__employee",
        ).filter(
            status__in=self._active_statuses()
        ).order_by("-pickup_time", "route_run_id", "employee__username")

        grouped = {}

        for trip in trips:
            if trip.route_run_id:
                group_key = f"route_run_{trip.route_run_id}"
            else:
                group_key = f"single_trip_{trip.id}"

            if group_key not in grouped:
                grouped[group_key] = {
                    "route_run_id": trip.route_run_id,
                    "route_name": (
                        trip.route_run.route_template.name
                        if trip.route_run and trip.route_run.route_template
                        else "Manual Trip"
                    ),
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

            grouped[group_key]["employees"].append(
                {
                    "trip_id": trip.id,
                    "employee_id": trip.employee.id,
                    "employee_name": trip.employee.username,
                    "pickup_location": trip.pickup_location,
                    "drop_location": trip.drop_location,
                    "status": trip.status,
                }
            )

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
            return Response(
                {"error": "route_run_id is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        trips = Trip.objects.select_related("employee", "driver").filter(
            route_run_id=route_run_id,
            status__in=self._active_statuses(),
        )

        if not trips.exists():
            return Response(
                {"error": "No active trips found for this assigned route."},
                status=status.HTTP_404_NOT_FOUND,
            )

        cancelled_count = 0

        with transaction.atomic():
            for trip in trips:
                trip.status = Trip.STATUS_CANCELLED
                trip.save(update_fields=["status"])

                TripCancellation.objects.create(
                    trip=trip,
                    cancelled_by=request.user,
                    reason="Cancelled by admin from assigned cab group",
                )

                self.send_notification(
                    trip.employee,
                    f"Your trip #{trip.id} has been cancelled by admin.",
                )

                if trip.driver:
                    self.send_notification(
                        trip.driver,
                        f"Trip #{trip.id} has been cancelled by admin.",
                    )

                cancelled_count += 1

        return Response(
            {"message": f"{cancelled_count} trip(s) cancelled successfully."},
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"], url_path="cancel-trip")
    def cancel_trip_by_admin(self, request, pk=None):
        if request.user.role != "ADMIN":
            raise PermissionDenied("Only admin can cancel trips.")

        trip = self.get_object()

        if trip.status in self._closed_statuses():
            return Response(
                {"detail": f"Trip already {trip.status.lower()}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            trip.status = Trip.STATUS_CANCELLED
            trip.save(update_fields=["status"])
            self._cancel_route_run_if_possible(trip)

            TripCancellation.objects.create(
                trip=trip,
                cancelled_by=request.user,
                reason="Cancelled by admin",
            )

            self.send_notification(
                trip.employee,
                f"Your trip #{trip.id} has been cancelled by admin.",
            )

            if trip.driver:
                self.send_notification(
                    trip.driver,
                    f"Trip #{trip.id} has been cancelled by admin.",
                )

        return Response(
            {"detail": "Trip cancelled successfully."},
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="reset-active-trips")
    def reset_active_trips(self, request):
        if request.user.role != "ADMIN":
            raise PermissionDenied("Only admin can reset active trips.")

        active_trips = Trip.objects.select_related(
            "employee",
            "driver",
            "route_run",
        ).filter(
            status__in=self._active_statuses()
        )

        if not active_trips.exists():
            return Response(
                {"detail": "No active trips found."},
                status=status.HTTP_200_OK,
            )

        total = active_trips.count()

        with transaction.atomic():
            for trip in active_trips:
                trip.status = Trip.STATUS_CANCELLED
                trip.save(update_fields=["status"])
                self._cancel_route_run_if_possible(trip)

                TripCancellation.objects.create(
                    trip=trip,
                    cancelled_by=request.user,
                    reason="Cancelled by admin reset",
                )

                self.send_notification(
                    trip.employee,
                    f"Your trip #{trip.id} has been cancelled by admin reset.",
                )

                if trip.driver:
                    self.send_notification(
                        trip.driver,
                        f"Trip #{trip.id} has been cancelled by admin reset.",
                    )

        return Response(
            {"detail": f"{total} active trips reset successfully."},
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["get"], url_path="upcoming")
    def upcoming_trips(self, request):
        user = request.user

        if user.role != "EMPLOYEE":
            return Response(
                {"error": "Only employee can view upcoming trips."},
                status=status.HTTP_403_FORBIDDEN,
            )

        today = timezone.localdate()

        qs = (
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
                status__in=self._active_statuses(),
                pickup_time__date__gte=today,
            )
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

        return Response(
            self.get_serializer(qs, many=True).data,
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["get"], url_path="create_form_data")
    def create_form_data(self, request):
        if request.user.role != "ADMIN":
            return Response(
                {"error": "Only admin can access create form data."},
                status=status.HTTP_403_FORBIDDEN,
            )

        employees = User.objects.filter(
            role="EMPLOYEE",
            is_active=True,
        ).order_by("username")

        drivers = User.objects.filter(
            role="DRIVER",
            is_active=True,
        ).order_by("username")

        vehicles = Vehicle.objects.select_related("driver").all().order_by("vehicle_number")

        return Response(
            {
                "employees": UserOptionSerializer(employees, many=True).data,
                "drivers": UserOptionSerializer(drivers, many=True).data,
                "vehicles": VehicleOptionSerializer(vehicles, many=True).data,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="send_notifications_by_date")
    def send_notifications_by_date(self, request):
        if request.user.role != "ADMIN":
            return Response(
                {"error": "Only admin can send notifications."},
                status=status.HTTP_403_FORBIDDEN,
            )

        date_str = request.data.get("date")

        if not date_str:
            return Response(
                {"error": "Date is required in YYYY-MM-DD format."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        trips = Trip.objects.filter(
            pickup_time__date=date_str,
            notification_sent=False,
        ).select_related("employee", "driver", "vehicle")

        sent_count = 0

        for trip in trips:
            if trip.trip_type == Trip.TRIP_TYPE_PICKUP:
                employee_message = (
                    f"Your pickup trip is scheduled on "
                    f"{trip.pickup_time.strftime('%d-%m-%Y %H:%M')} "
                    f"from {trip.pickup_location} to {trip.drop_location}."
                )
                driver_message = (
                    f"You have a pickup trip on "
                    f"{trip.pickup_time.strftime('%d-%m-%Y %H:%M')} "
                    f"for {trip.employee.username} from "
                    f"{trip.pickup_location} to {trip.drop_location}."
                )
            else:
                employee_message = (
                    f"Your drop trip is scheduled on "
                    f"{trip.pickup_time.strftime('%d-%m-%Y %H:%M')} "
                    f"from {trip.pickup_location} to {trip.drop_location}."
                )
                driver_message = (
                    f"You have a drop trip on "
                    f"{trip.pickup_time.strftime('%d-%m-%Y %H:%M')} "
                    f"for {trip.employee.username} from "
                    f"{trip.pickup_location} to {trip.drop_location}."
                )

            self.send_notification(trip.employee, employee_message)
            self.send_notification(trip.driver, driver_message)

            trip.notification_sent = True
            trip.save(update_fields=["notification_sent"])
            sent_count += 1

        return Response(
            {
                "message": f"Notifications sent successfully for {sent_count} trip(s).",
                "sent_count": sent_count,
            },
            status=status.HTTP_200_OK,
        )

    def perform_create(self, serializer):
        if self.request.user.role != "ADMIN":
            raise PermissionDenied("Only Admin can create trips.")

        trip = serializer.save()

        if trip.driver:
            self.send_notification(
                trip.driver,
                f"You have been assigned trip {trip.id}.",
            )

        if trip.employee:
            self.send_notification(
                trip.employee,
                "Your trip has been assigned successfully.",
            )

    def perform_update(self, serializer):
        if self.request.user.role != "ADMIN":
            raise PermissionDenied("Only Admin can update trips.")

        serializer.save()

    def perform_destroy(self, instance):
        if self.request.user.role != "ADMIN":
            raise PermissionDenied("Only Admin can delete trips.")

        instance.delete()

    @action(detail=True, methods=["post"], url_path="employee-cancel")
    def cancel_trip(self, request, pk=None):
        trip = self.get_object()

        if request.user.role != "EMPLOYEE":
            return Response(
                {"error": "Only employee can cancel trip"},
                status=status.HTTP_403_FORBIDDEN,
            )

        if trip.employee != request.user:
            return Response(
                {"error": "You can cancel only your own trip"},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            trip.cancel()
            TripCancellation.objects.create(
                trip=trip,
                cancelled_by=request.user,
                reason=request.data.get("reason", ""),
            )
        except ValueError as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if trip.driver:
            self.send_notification(
                trip.driver,
                f"Trip has been cancelled by {trip.employee.username}.",
            )

        self._notify_admins(f"Trip cancelled by {trip.employee.username}.")

        return Response(
            {"message": "Trip cancelled successfully"},
            status=status.HTTP_200_OK,
        )

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

        try:
            trip.start()
        except ValueError as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if trip.trip_type == Trip.TRIP_TYPE_PICKUP:
            self.send_notification(trip.employee, "Your pickup trip has started.")
        else:
            self.send_notification(trip.employee, "Your drop trip has started.")

        self._notify_admins(
            f"Trip {trip.id} has started by driver {trip.driver.username}."
        )

        return Response(
            {"message": "Trip started successfully"},
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"])
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

        try:
            trip.complete()
        except ValueError as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        self.send_notification(
            trip.employee,
            "Trip completed. Please submit your review.",
        )

        self._notify_admins(
            f"Trip {trip.id} has been completed by driver {trip.driver.username}."
        )

        return Response(
            {"message": "Trip completed successfully"},
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["get"], url_path="history")
    def history(self, request):
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
            qs = base_qs.filter(
                employee=user,
                status__in=self._closed_statuses(),
            ).order_by("-created_at")
        elif user.role == "DRIVER":
            qs = base_qs.filter(
                driver=user,
                status__in=self._closed_statuses(),
            ).order_by("-created_at")
        elif user.role == "ADMIN":
            qs = base_qs.filter(
                status__in=self._closed_statuses(),
            ).order_by("-created_at")
        else:
            qs = Trip.objects.none()

        return Response(
            self.get_serializer(qs, many=True).data,
            status=status.HTTP_200_OK,
        )

    def send_notification(self, user, message):
        if not user:
            return

        Notification.objects.create(
            user=user,
            title="Trip Update",
            message=message,
        )
