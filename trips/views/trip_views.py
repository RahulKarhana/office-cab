from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Case, When, Value, IntegerField
from django.utils import timezone
from math import radians, cos, sin, asin, sqrt
from trips.utils.notification import send_push_notification
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet
from datetime import datetime, time
from trips.utils.smart_notifications import create_smart_notification

from trips.models import (
    DriverLocation,
    Notification,
    Trip,
    TripCancellation,
    Vehicle,
    RouteRunStop,
    RouteRun,
    EmployeeLeave,
    Notification,
)

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

    def _get_ordered_stops(self, route_run):
        stops = route_run.stops.select_related("employee")

        if route_run.trip_type == Trip.TRIP_TYPE_DROP:
            return stops.order_by("-stop_order")

        return stops.order_by("stop_order")

    def _get_current_stop(self, route_run):
        return (
            self._get_ordered_stops(route_run)
            .filter(
                is_picked=False,
                is_no_show=False,
            )
            .first()
        )

    def _get_next_stop_after_current(self, route_run, current_stop):
        stops = list(self._get_ordered_stops(route_run))

        found_current = False

        for stop in stops:
            if stop.id == current_stop.id:
                found_current = True
                continue

            if found_current and not stop.is_picked and not stop.is_no_show:
                return stop

        return None

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

        self.send_notification(
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
            self.send_notification(
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

    def _notify_admins(self,
        message,
        title="Admin Alert",
        push_data=None,
        notification_type=Notification.TYPE_INFO,
        priority=Notification.PRIORITY_LOW,
        trip=None,
        route_run=None,
        driver=None,
        employee=None,
    ):
        admins = User.objects.filter(role="ADMIN", is_active=True)

        for admin in admins:
            create_smart_notification(
                user=admin,
                title=title,
                message=message,
                notification_type=notification_type,
                priority=priority,
                trip=trip,
                route_run=route_run,
                driver=driver,
                employee=employee,
                push_data=push_data or {},
            )

    def _handle_trip_start_notifications(self, trip):
        route_run = trip.route_run

        if not route_run:
            if trip.trip_type == Trip.TRIP_TYPE_PICKUP:
                msg = "Your pickup cab has started 🚖"
            else:
                msg = "Your drop cab has started 🚖"

            self.send_notification(
                trip.employee,
                msg,
                title="🚖 Trip Started",
                push_data={
                    "type": "TRIP_STARTED",
                    "trip_id": str(trip.id),
                    "screen": "active_trip",
                },
            )
            return

        if not route_run.started_at:
            now = timezone.now()

            route_run.started_at = now
            route_run.save(update_fields=["started_at"])

            Trip.objects.filter(
                route_run=route_run,
                status=Trip.STATUS_ASSIGNED,
            ).update(
                status=Trip.STATUS_STARTED,
                start_time=now,
            )

        stops = self._get_ordered_stops(route_run)

        for stop in stops:
            if route_run.trip_type == Trip.TRIP_TYPE_DROP:
                msg = "Your drop cab has started 🚖"
            else:
                msg = "Your pickup cab has started 🚖"

            self.send_notification(
                stop.employee,
                msg,
                title="Cab Started 🚗",
                push_data={
                    "type": "CAB_STARTED",
                    "trip_id": str(trip.id),
                    "route_run_id": str(route_run.id),
                    "trip_type": route_run.trip_type,
                    "screen": "active_trip",
                },
            )

        first_stop = stops.first()

        if first_stop:
            if route_run.trip_type == Trip.TRIP_TYPE_DROP:
                first_msg = "You are first for drop. Please be ready."
                first_type = "NEXT_DROP"
            else:
                first_msg = "Next turn is yours. Be ready!"
                first_type = "NEXT_PICKUP"

            self.send_notification(
                first_stop.employee,
                first_msg,
                title="Driver is coming 🚗",
                push_data={
                    "type": first_type,
                    "trip_id": str(trip.id),
                    "route_run_id": str(route_run.id),
                    "stop_id": str(first_stop.id),
                    "screen": "active_trip",
                },
            )

    def _handle_stop_done(self, route_run, current_stop):
        current_stop.is_picked = True
        current_stop.picked_at = timezone.now()
        current_stop.save(update_fields=["is_picked", "picked_at"])

        next_stop = self._get_next_stop_after_current(route_run, current_stop)

        if next_stop:
            if route_run.trip_type == Trip.TRIP_TYPE_DROP:
                msg = "Driver is coming to drop you 🚗"
                notification_type = "NEXT_DROP"
            else:
                msg = "Driver is coming to pick you 🚗"
                notification_type = "NEXT_PICKUP"

            self.send_notification(
                next_stop.employee,
                msg,
                title="Your turn 🚗",
                push_data={
                    "type": notification_type,
                    "route_run_id": str(route_run.id),
                    "stop_id": str(next_stop.id),
                    "screen": "active_trip",
                },
            )

        return next_stop

    def _complete_route_if_finished(self, route_run):
        remaining_stops = route_run.stops.filter(
            is_picked=False,
            is_no_show=False,
        ).count()

        route_completed = False

        if remaining_stops == 0:
            now = timezone.now()

            if hasattr(route_run, "completed_at") and not route_run.completed_at:
                route_run.completed_at = now
                route_run.save(update_fields=["completed_at"])

            Trip.objects.filter(
                route_run=route_run,
                status=Trip.STATUS_STARTED,
            ).update(
                status=Trip.STATUS_COMPLETED,
                end_time=now,
            )

            route_completed = True
            self._notify_route_completed(route_run)

        return remaining_stops, route_completed

    def _notify_route_completed(self, route_run):
        trips = Trip.objects.select_related("employee").filter(
            route_run=route_run,
            status=Trip.STATUS_COMPLETED,
        )

        for trip in trips:
            self.send_notification(
                trip.employee,
                "Your trip has been completed. Please submit your review.",
                title="✅ Trip Completed",
                push_data={
                    "type": "TRIP_COMPLETED",
                    "trip_id": str(trip.id),
                    "route_run_id": str(route_run.id),
                    "screen": "review",
                },
            )

        self._notify_admins(
            f"{route_run.trip_type.capitalize()} route #{route_run.id} has been completed.",
            title="✅ Route Completed",
            notification_type=Notification.TYPE_ROUTE_COMPLETED,
            priority=Notification.PRIORITY_MEDIUM,
            route_run=route_run,
            push_data={
                "type": "ROUTE_COMPLETED",
                "route_run_id": str(route_run.id),
                "screen": "admin_dashboard",
            },
        )

    def _calculate_distance_km(self, lat1, lon1, lat2, lon2):
        lon1, lat1, lon2, lat2 = map(float, [lon1, lat1, lon2, lat2])
        lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])

        dlon = lon2 - lon1
        dlat = lat2 - lat1

        a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
        c = 2 * asin(sqrt(a))
        r = 6371

        return c * r

    def _estimate_eta_minutes(self, distance_km):
        if distance_km is None:
            return None

        eta = int((distance_km / 25) * 60)
        return max(1, eta)

    def _format_eta_text(self, eta_minutes):
        if eta_minutes is None:
            return None

        if eta_minutes < 60:
            return f"{eta_minutes} min"

        hours = eta_minutes // 60
        minutes = eta_minutes % 60

        if minutes == 0:
            return f"{hours} hr"

        return f"{hours} hr {minutes} min"

    def _format_distance_text(self, distance_km):
        if distance_km is None:
            return None

        if distance_km < 1:
            return f"{int(distance_km * 1000)} m"

        return f"{round(distance_km, 1)} km"

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
            .prefetch_related("route_run_stops_employee")
            .filter(
                employee=user,
                status=Trip.STATUS_STARTED,
                route_run_started_at_isnull=False,
                route_run_completed_at_isnull=True,
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

        stops = list(self._get_ordered_stops(route_run))

        if not stops:
            return Response(
                {"detail": "No stops found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        driver_location = DriverLocation.objects.filter(
            driver=trip.driver
        ).order_by("-updated_at").first()

        driver_latitude = driver_location.latitude if driver_location else None
        driver_longitude = driver_location.longitude if driver_location else None
        last_updated = driver_location.updated_at if driver_location else None

        current_stop = next(
            (s for s in stops if not s.is_picked and not s.is_no_show),
            None,
        )

        my_stop = next(
            (s for s in stops if s.employee_id == user.id),
            None,
        )

        if not current_stop:
            return Response(
                {"detail": "Route already completed."},
                status=status.HTTP_404_NOT_FOUND,
            )

        next_stop = None
        found_current = False

        for stop in stops:
            if stop.id == current_stop.id:
                found_current = True
                continue

            if found_current and not stop.is_picked and not stop.is_no_show:
                next_stop = stop
                break

        total_stops = len(stops)
        completed_stops = len(
            [s for s in stops if s.is_picked or s.is_no_show]
        )
        remaining_stops = len(
            [s for s in stops if not s.is_picked and not s.is_no_show]
        )

        live_stops = []
        cumulative_eta = 0

        for index, stop in enumerate(stops):
            stop_status = "UPCOMING"

            if stop.is_no_show:
                stop_status = "NO_SHOW"
            elif stop.is_picked:
                stop_status = "COMPLETED"
            elif stop.id == current_stop.id:
                stop_status = "CURRENT"
            elif my_stop and stop.id == my_stop.id:
                stop_status = "YOUR_STOP"
            elif next_stop and stop.id == next_stop.id:
                stop_status = "NEXT"

            distance_km = None
            eta_minutes = None

            if driver_latitude is not None and driver_longitude is not None:
                if stop.id == current_stop.id:
                    if (
                        stop.pickup_latitude is not None and
                        stop.pickup_longitude is not None
                    ):
                        distance_km = self._calculate_distance_km(
                            driver_latitude,
                            driver_longitude,
                            stop.pickup_latitude,
                            stop.pickup_longitude,
                        )

                        eta_minutes = self._estimate_eta_minutes(distance_km)
                        cumulative_eta = eta_minutes or 0

                elif not stop.is_picked and not stop.is_no_show:
                    current_index = stops.index(current_stop)

                    if index > current_index:
                        cumulative_eta += 7
                        eta_minutes = cumulative_eta

            live_stops.append({
                "id": stop.id,
                "stop_order": stop.stop_order,
                "display_order": index + 1,
                "employee_name": stop.employee.username,
                "pickup_location": stop.pickup_location,
                "pickup_latitude": stop.pickup_latitude,
                "pickup_longitude": stop.pickup_longitude,
                "is_current_stop": stop.id == current_stop.id,
                "show_chat_option": stop.id == current_stop.id,
                "is_picked": stop.is_picked,
                "is_no_show": stop.is_no_show,
                "status": stop_status,
                "distance_km": round(distance_km, 2)
                if distance_km is not None else None,
                "distance_text": self._format_distance_text(distance_km),
                "eta_minutes": eta_minutes,
                "eta_text": self._format_eta_text(eta_minutes),
            })

        current_stop_data = next(
            (x for x in live_stops if x["id"] == current_stop.id),
            None,
        )

        next_stop_data = next(
            (x for x in live_stops if next_stop and x["id"] == next_stop.id),
            None,
        )

        my_stop_data = next(
            (x for x in live_stops if my_stop and x["id"] == my_stop.id),
            None,
        )

        route_word = (
            "drop"
            if route_run.trip_type == Trip.TRIP_TYPE_DROP
            else "pickup"
        )

        if route_run.completed_at is not None:
            status_text = "Route completed successfully."

        elif my_stop and my_stop.is_picked:
            status_text = (
                "You have been dropped successfully."
                if route_run.trip_type == Trip.TRIP_TYPE_DROP
                else "You have already been picked up. Cab is continuing on route."
            )

        elif current_stop and my_stop and current_stop.id == my_stop.id:
            status_text = f"Cab is currently coming for your {route_word}."

        elif next_stop and my_stop and next_stop.id == my_stop.id:
            status_text = (
                f"Current {route_word} is "
                f"{current_stop.employee.username}. You are next."
            )

        elif my_stop:
            status_text = (
                f"Current {route_word} is "
                f"{current_stop.employee.username}. "
                f"Your {route_word} will come later in route."
            )

        else:
            status_text = f"Live {route_word} route is active."

        return Response(
            {
                "trip_id": trip.id,
                "route_run_id": route_run.id,
                "route_name": (
                    route_run.route_template.name
                    if route_run.route_template
                    else f"{route_word.capitalize()} Route"
                ),
                "driver_name": (
                    trip.driver.username if trip.driver else None
                ),
                "vehicle_number": (
                    trip.vehicle.vehicle_number if trip.vehicle else None
                ),
                "trip_type": trip.trip_type,
                "trip_status": trip.status,
                "current_stop_order": (
                    current_stop.stop_order if current_stop else None
                ),
                "remaining_stops": remaining_stops,
                "completed_stops": completed_stops,
                "total_stops": total_stops,
                "status_text": status_text,
                "driver_latitude": driver_latitude,
                "driver_longitude": driver_longitude,
                "last_updated": last_updated,
                "current_stop": current_stop_data,
                "next_stop": next_stop_data,
                "my_stop": my_stop_data,
                "stops": live_stops,
            },
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

                self._send_admin_cancel_notification_if_needed(trip)

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

            self._send_admin_cancel_notification_if_needed(trip)

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

                self._send_admin_cancel_notification_if_needed(trip)

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
    @action(detail=False, methods=["post"], url_path="mark-leave")
    def mark_leave(self, request):
        if request.user.role != "EMPLOYEE":
            return Response(
                {"error": "Only employee can mark leave."},
                status=status.HTTP_403_FORBIDDEN,
            )

        leave_date = request.data.get("leave_date")
        reason = request.data.get("reason", "").strip()

        if not leave_date:
            return Response(
                {"error": "leave_date is required in YYYY-MM-DD format."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            leave, created = EmployeeLeave.objects.get_or_create(
                employee=request.user,
                leave_date=leave_date,
                defaults={"reason": reason},
            )

            if not created:
                leave.reason = reason
                leave.save(update_fields=["reason"])

            trips = Trip.objects.select_related(
                "driver",
                "employee",
                "route_run",
            ).filter(
                employee=request.user,
                trip_date=leave_date,
                status=Trip.STATUS_ASSIGNED,
            )

            cancelled_count = 0

            for trip in trips:
                trip.status = Trip.STATUS_CANCELLED
                trip.save(update_fields=["status"])

                if trip.route_run_id:
                    RouteRunStop.objects.filter(
                        route_run=trip.route_run,
                        employee=trip.employee,
                    ).update(
                        is_no_show=True,
                        no_show_at=timezone.now(),
                    )

                TripCancellation.objects.create(
                    trip=trip,
                    cancelled_by=request.user,
                    reason=reason or "Marked leave by employee",
                    declaration_accepted=False,
                    declaration_text="",
                    cancelled_by_role=request.user.role,
                )

                cancelled_count += 1

            started_trips_count = Trip.objects.filter(
                employee=request.user,
                trip_date=leave_date,
                status=Trip.STATUS_STARTED,
            ).count()

        self._notify_admins(
            f"{request.user.username} marked leave for {leave_date}. "
            f"{cancelled_count} assigned trip(s) auto-cancelled.",
            title="Employee Leave Marked",
            push_data={
                "type": "EMPLOYEE_LEAVE",
                "employee_id": str(request.user.id),
                "leave_date": str(leave_date),
            },
        )

        return Response(
            {
                "message": "Leave marked successfully.",
                "leave_date": leave_date,
                "cancelled_trips": cancelled_count,
                "started_trips_not_cancelled": started_trips_count,
                "driver_fcm_sent": False,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"], url_path="employee-cancel")
    def cancel_trip(self, request, pk=None):
        trip = self.get_object()

        SELF_TRAVEL_REASON = "I will come office by self"

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

        if trip.status == Trip.STATUS_STARTED:
            return Response(
                {"error": "You can't cancel the cab after trip started."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if trip.status == Trip.STATUS_COMPLETED:
            return Response(
                {"error": "Completed trip cannot be cancelled."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if trip.status == Trip.STATUS_CANCELLED:
            return Response(
                {"error": "Trip is already cancelled."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        reason = request.data.get("reason", "").strip()
        declaration_accepted = request.data.get("declaration_accepted", False)

        if not reason:
            return Response(
                {"error": "Cancellation reason is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        declaration_required = False

        if trip.trip_type == Trip.TRIP_TYPE_DROP:
            pickup_trip = Trip.objects.filter(
                employee=request.user,
                trip_type=Trip.TRIP_TYPE_PICKUP,
                trip_date=trip.trip_date,
            ).order_by("-created_at").first()

            if pickup_trip and pickup_trip.status != Trip.STATUS_CANCELLED:
                return Response(
                    {"error": "First cancel your today's pickup."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if pickup_trip and pickup_trip.status == Trip.STATUS_CANCELLED:
                pickup_cancellation = TripCancellation.objects.filter(
                    trip=pickup_trip,
                    cancelled_by=request.user,
                ).order_by("-cancelled_at").first()

                if (
                    pickup_cancellation
                    and pickup_cancellation.reason.lower() == SELF_TRAVEL_REASON.lower()
                ):
                    declaration_required = True

        if declaration_required and not declaration_accepted:
            return Response(
                {
                    "error": "Self declaration is required for drop cancellation.",
                    "declaration_required": True,
                    "declaration_text": (
                        "I confirm that I came to office by myself and I am cancelling "
                        "my drop cab by my own choice. I will manage my return travel myself."
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        declaration_text = ""

        if declaration_required:
            declaration_text = (
                "I confirm that I came to office by myself and I am cancelling "
                "my drop cab by my own choice. I will manage my return travel myself."
            )

        with transaction.atomic():
            trip.cancel()

            # ✅ Remove this employee from active driver pickup/drop list
            if trip.route_run_id:
                RouteRunStop.objects.filter(
                    route_run=trip.route_run,
                    employee=trip.employee,
                ).update(
                    is_no_show=True,
                    no_show_at=timezone.now(),
                )

            TripCancellation.objects.create(
                trip=trip,
                cancelled_by=request.user,
                reason=reason,
                declaration_accepted=bool(declaration_accepted),
                declaration_text=declaration_text,
                cancelled_by_role=request.user.role,
            )

        if trip.driver:
            self.send_notification(
                trip.driver,
                f"{trip.trip_type.capitalize()} trip cancelled by {trip.employee.username}. Reason: {reason}",
                title="❌ Trip Cancelled",
                push_data={
                    "type": "TRIP_CANCELLED",
                    "trip_id": str(trip.id),
                    "trip_type": trip.trip_type,
                    "reason": reason,
                    "screen": "driver_route",
                },
            )

        self._notify_admins(
            f"{trip.trip_type.capitalize()} trip cancelled by {trip.employee.username}. Reason: {reason}.",
            title="❌ Employee Trip Cancelled",
            push_data={
                "type": "TRIP_CANCELLED",
                "trip_id": str(trip.id),
                "trip_type": trip.trip_type,
                "reason": reason,
                "screen": "assigned_cabs",
            },
        )

        return Response(
            {
                "message": "Trip cancelled successfully.",
                "trip_id": trip.id,
                "trip_type": trip.trip_type,
                "reason": reason,
                "declaration_required": declaration_required,
                "declaration_accepted": bool(declaration_accepted),
            },
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

        self._handle_trip_start_notifications(trip)

        self._notify_admins(
            f"Trip {trip.id} has started by driver {trip.driver.username}.",
            title="🚖 Trip Started",
            push_data={
                "type": "TRIP_STARTED",
                "trip_id": str(trip.id),
                "route_run_id": str(trip.route_run_id or ""),
                "trip_type": trip.trip_type,
            },
        )

        return Response(
            {"message": "Trip started successfully"},
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

        assigned_trip = Trip.objects.filter(
            route_run=route_run,
            driver=request.user,
            status=Trip.STATUS_STARTED,
        ).first()

        if not assigned_trip:
            return Response(
                {"error": "You are not assigned to this active route."},
                status=status.HTTP_403_FORBIDDEN,
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
            next_stop = self._handle_stop_done(route_run, current_stop)
            remaining_stops, route_completed = self._complete_route_if_finished(route_run)

        action_word = "Drop" if route_run.trip_type == Trip.TRIP_TYPE_DROP else "Pickup"

        return Response(
            {
                "message": f"{action_word} marked successfully.",
                "current_stop_id": current_stop.id,
                "next_stop_id": next_stop.id if next_stop else None,
                "next_employee": next_stop.employee.username if next_stop else None,
                "remaining_stops": remaining_stops,
                "route_completed": route_completed,
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

        current_stop = self._get_current_stop(route_run)

        if not current_stop:
            return Response(
                {"error": "No active stop found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        now = timezone.now()

        current_stop.arrival_time = now
        current_stop.waiting_started_at = now
        current_stop.save(update_fields=["arrival_time", "waiting_started_at"])

        route_word = "drop" if route_run.trip_type == Trip.TRIP_TYPE_DROP else "pickup"

        self.send_notification(
            current_stop.employee,
            f"Driver has arrived at your {route_word} location 🚗",
            title="Arrived",
            push_data={
                "type": "ARRIVED",
                "route_run_id": str(route_run.id),
                "stop_id": str(current_stop.id),
                "trip_type": route_run.trip_type,
            },
        )

        return Response(
            {
                "message": "Arrival marked.",
                "stop_id": current_stop.id,
                "employee": current_stop.employee.username,
                "waiting_started_at": current_stop.waiting_started_at,
                "trip_type": route_run.trip_type,
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

        current_stop = self._get_current_stop(route_run)

        if not current_stop:
            return Response(
                {"error": "No active stop found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        current_stop.waiting_started_at = timezone.now()
        current_stop.save(update_fields=["waiting_started_at"])

        route_word = "drop" if route_run.trip_type == Trip.TRIP_TYPE_DROP else "pickup"

        self.send_notification(
            current_stop.employee,
            f"Driver is still waiting at your {route_word} location.",
            title="Driver Waiting",
            push_data={
                "type": "KEEP_WAITING",
                "route_run_id": str(route_run.id),
                "stop_id": str(current_stop.id),
                "trip_type": route_run.trip_type,
            },
        )

        return Response(
            {
                "message": "Waiting continued.",
                "stop_id": current_stop.id,
                "employee": current_stop.employee.username,
                "waiting_started_at": current_stop.waiting_started_at,
                "trip_type": route_run.trip_type,
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

        current_stop = self._get_current_stop(route_run)

        if not current_stop:
            return Response(
                {"error": "No active stop found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        current_stop.is_no_show = True
        current_stop.no_show_at = timezone.now()
        current_stop.save(update_fields=["is_no_show", "no_show_at"])

        route_word = "drop" if route_run.trip_type == Trip.TRIP_TYPE_DROP else "pickup"

        self._notify_admins(
            f"{current_stop.employee.username} marked as no-show for route #{route_run.id}.",
            title="🚫 Employee No-show",
            notification_type=Notification.TYPE_NO_SHOW,
            priority=Notification.PRIORITY_HIGH,
            route_run=route_run,
            driver=request.user,
            employee=current_stop.employee,
            push_data={
                "type": "NO_SHOW",
                "route_run_id": str(route_run.id),
                "employee_id": str(current_stop.employee.id),
                "screen": "admin_dashboard",
            },
        )

        next_stop = self._get_next_stop_after_current(route_run, current_stop)

        if next_stop:
            if route_run.trip_type == Trip.TRIP_TYPE_DROP:
                msg = "Driver is coming to drop you 🚗"
                notification_type = "NEXT_DROP"
            else:
                msg = "Driver is coming to pick you 🚗"
                notification_type = "NEXT_PICKUP"

            self.send_notification(
                next_stop.employee,
                msg,
                title="Your turn 🚗",
                push_data={
                    "type": notification_type,
                    "route_run_id": str(route_run.id),
                    "stop_id": str(next_stop.id),
                    "screen": "active_trip",
                    "trip_type": route_run.trip_type,
                },
            )

        remaining_stops, route_completed = self._complete_route_if_finished(route_run)

        return Response(
            {
                "message": "Marked no show.",
                "current_stop_id": current_stop.id,
                "next_stop_id": next_stop.id if next_stop else None,
                "next_employee": next_stop.employee.username if next_stop else None,
                "remaining_stops": remaining_stops,
                "route_completed": route_completed,
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
            title="✅ Trip Completed",
            push_data={
                "type": "TRIP_COMPLETED",
                "trip_id": str(trip.id),
                "screen": "review",
            },
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

    def send_notification(self, user, message, title="Trip Update", push_data=None):
        if not user:
            return

        Notification.objects.create(
            user=user,
            title=title,
            message=message,
        )

        try:
            send_push_notification(
                user=user,
                title=title,
                body=message,
                data=push_data or {},
            )
        except Exception as e:
            print("FCM ERROR:", e)