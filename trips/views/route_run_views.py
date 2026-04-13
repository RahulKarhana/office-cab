from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from trips.models import DriverLocation, Notification, RouteRun, RouteRunStop, Trip
from trips.serializers import (
    RouteRunLiveStatusSerializer,
    RouteRunSerializer,
    calculate_distance_km,
    estimate_eta_minutes,
    format_distance_text,
    format_eta_text,
)


class RouteRunViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = RouteRunSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user

        if user.role == "ADMIN":
            return RouteRun.objects.prefetch_related(
                "stops__employee"
            ).select_related(
                "route_template",
                "driver",
                "vehicle",
            ).order_by("-created_at")

        if user.role == "DRIVER":
            return RouteRun.objects.filter(
                driver=user
            ).prefetch_related(
                "stops__employee"
            ).select_related(
                "route_template",
                "driver",
                "vehicle",
            ).order_by("-created_at")

        return RouteRun.objects.none()

    @action(detail=False, methods=["get"], url_path="today_active")
    def today_active(self, request):
        user = request.user

        if user.role != "DRIVER":
            return Response(
                {"error": "Only driver can view active route run."},
                status=status.HTTP_403_FORBIDDEN,
            )

        today = timezone.localdate()

        route_run = RouteRun.objects.filter(
            driver=user,
            run_date=today,
        ).prefetch_related(
            "stops__employee"
        ).select_related(
            "route_template",
            "driver",
            "vehicle",
        ).order_by("-created_at").first()

        if not route_run:
            return Response(
                {"detail": "No active route run for today."},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(
            self.get_serializer(route_run).data,
            status=status.HTTP_200_OK,
        )

    def _get_route_employee_ids(self, route_run):
        return list(route_run.stops.values_list("employee_id", flat=True))

    def _get_related_trip_queryset(self, route_run):
        stop_employee_ids = self._get_route_employee_ids(route_run)

        return Trip.objects.filter(
            employee_id__in=stop_employee_ids,
            pickup_time__date=route_run.run_date,
            trip_type=route_run.trip_type,
        ).exclude(
            status__in=[Trip.STATUS_COMPLETED, Trip.STATUS_CANCELLED]
        )

    @action(detail=True, methods=["get"], url_path="live_status")
    def live_status(self, request, pk=None):
        route_run = self.get_object()

        if request.user.role not in ["ADMIN", "DRIVER"]:
            return Response(
                {"error": "Only admin or driver can view live route status."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if request.user.role == "DRIVER" and route_run.driver != request.user:
            return Response(
                {"error": "You are not assigned to this route run."},
                status=status.HTTP_403_FORBIDDEN,
            )

        driver_location = DriverLocation.objects.filter(
            driver=route_run.driver
        ).order_by("-updated_at").first()

        stops_qs = route_run.stops.select_related("employee").order_by("stop_order")
        stops = list(stops_qs)

        total_stops = len(stops)
        completed_stops = len([s for s in stops if s.is_picked])
        remaining_stops = len([s for s in stops if not s.is_picked])

        current_stop = next((s for s in stops if not s.is_picked), None)
        next_stop = None

        if current_stop:
            next_stop = next(
                (
                    s for s in stops
                    if not s.is_picked and s.stop_order > current_stop.stop_order
                ),
                None,
            )

        driver_lat = driver_location.latitude if driver_location else None
        driver_lng = driver_location.longitude if driver_location else None
        last_updated = driver_location.updated_at if driver_location else None

        live_stops = []
        cumulative_eta = 0

        for stop in stops:
            stop_status = "UPCOMING"

            if stop.is_picked:
                stop_status = "COMPLETED"
            elif current_stop and stop.id == current_stop.id:
                stop_status = "CURRENT"
            elif next_stop and stop.id == next_stop.id:
                stop_status = "NEXT"

            distance_km = None
            eta_minutes = None

            if driver_lat is not None and driver_lng is not None:
                if stop_status == "CURRENT":
                    if (
                        stop.pickup_latitude is not None
                        and stop.pickup_longitude is not None
                    ):
                        distance_km = calculate_distance_km(
                            driver_lat,
                            driver_lng,
                            stop.pickup_latitude,
                            stop.pickup_longitude,
                        )
                        eta_minutes = estimate_eta_minutes(distance_km)
                        cumulative_eta = eta_minutes or 0

                elif (
                    not stop.is_picked
                    and current_stop
                    and stop.stop_order > current_stop.stop_order
                ):
                    cumulative_eta += 7
                    eta_minutes = cumulative_eta

            live_stops.append(
                {
                    "id": stop.id,
                    "stop_order": stop.stop_order,
                    "employee_name": stop.employee.username,
                    "pickup_location": stop.pickup_location,
                    "pickup_latitude": stop.pickup_latitude,
                    "pickup_longitude": stop.pickup_longitude,
                    "is_picked": stop.is_picked,
                    "status": stop_status,
                    "distance_km": round(distance_km, 2)
                    if distance_km is not None
                    else None,
                    "distance_text": format_distance_text(distance_km),
                    "eta_minutes": eta_minutes,
                    "eta_text": format_eta_text(eta_minutes),
                }
            )

        current_stop_data = None
        if (
            current_stop
            and driver_lat is not None
            and driver_lng is not None
            and current_stop.pickup_latitude is not None
            and current_stop.pickup_longitude is not None
        ):
            current_distance = calculate_distance_km(
                driver_lat,
                driver_lng,
                current_stop.pickup_latitude,
                current_stop.pickup_longitude,
            )
            current_eta = estimate_eta_minutes(current_distance)

            current_stop_data = {
                "id": current_stop.id,
                "employee_name": current_stop.employee.username,
                "pickup_location": current_stop.pickup_location,
                "stop_order": current_stop.stop_order,
                "distance_km": round(current_distance, 2),
                "distance_text": format_distance_text(current_distance),
                "eta_minutes": current_eta,
                "eta_text": format_eta_text(current_eta),
            }
        elif current_stop:
            current_stop_data = {
                "id": current_stop.id,
                "employee_name": current_stop.employee.username,
                "pickup_location": current_stop.pickup_location,
                "stop_order": current_stop.stop_order,
                "distance_km": None,
                "distance_text": None,
                "eta_minutes": None,
                "eta_text": None,
            }

        next_stop_data = None
        if next_stop:
            next_stop_data = next(
                (item for item in live_stops if item["id"] == next_stop.id),
                None,
            )

        if route_run.completed_at is not None:
            status_text = "All pickups completed. Route has been completed."
        elif current_stop:
            if next_stop:
                status_text = (
                    f"Currently heading to {current_stop.employee.username}. "
                    f"After that, next pickup is {next_stop.employee.username}."
                )
            else:
                status_text = (
                    f"Currently heading to {current_stop.employee.username}. "
                    f"This is the final pickup."
                )
        else:
            status_text = "All pickups completed. Cab is now going to office."

        payload = {
            "route_run_id": route_run.id,
            "route_name": route_run.route_template.name
            if route_run.route_template
            else "Route",
            "driver_name": route_run.driver.username if route_run.driver else None,
            "vehicle_number": route_run.vehicle.vehicle_number
            if route_run.vehicle
            else None,
            "trip_type": route_run.trip_type,
            "current_stop_order": current_stop.stop_order if current_stop else None,
            "remaining_stops": remaining_stops,
            "completed_stops": completed_stops,
            "total_stops": total_stops,
            "status_text": status_text,
            "driver_latitude": driver_lat,
            "driver_longitude": driver_lng,
            "last_updated": last_updated,
            "current_stop": current_stop_data,
            "next_stop": next_stop_data,
            "stops": live_stops,
        }

        serializer = RouteRunLiveStatusSerializer(payload)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="start_run")
    def start_run(self, request, pk=None):
        route_run = self.get_object()

        if request.user.role != "DRIVER":
            return Response(
                {"error": "Only driver can start route run."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if route_run.driver != request.user:
            return Response(
                {"error": "You are not assigned to this route run."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if route_run.completed_at is not None:
            return Response(
                {"error": "Route run already completed."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if route_run.started_at is None:
            route_run.started_at = timezone.now()
            route_run.save(update_fields=["started_at"])

        trip_qs = self._get_related_trip_queryset(route_run)
        trip_qs.update(
            route_run=route_run,
            driver=route_run.driver,
            vehicle=route_run.vehicle,
            status=Trip.STATUS_STARTED,
            start_time=timezone.now(),
        )

        for stop in route_run.stops.all():
            self.send_notification(
                stop.employee,
                (
                    f"Your cab trip has started. "
                    f"Driver: {route_run.driver.username}, "
                    f"Vehicle: {route_run.vehicle.vehicle_number}."
                ),
            )

        return Response(
            {"message": "Route run started successfully."},
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"], url_path="complete_stop")
    def complete_stop(self, request, pk=None):
        route_run = self.get_object()

        if request.user.role != "DRIVER":
            return Response(
                {"error": "Only driver can complete stop."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if route_run.driver != request.user:
            return Response(
                {"error": "You are not assigned to this route run."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if route_run.completed_at is not None:
            return Response(
                {"error": "Route run already completed."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        current_stop = route_run.stops.filter(
            stop_order=route_run.current_stop_order
        ).first()

        if not current_stop:
            return Response(
                {"error": "No current stop found."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if current_stop.is_picked:
            return Response(
                {"error": "Current stop already completed."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        current_stop.is_picked = True
        current_stop.picked_at = timezone.now()
        current_stop.save(update_fields=["is_picked", "picked_at"])

        self.send_notification(
            current_stop.employee,
            (
                f"Your pickup has been completed. "
                f"Driver: {route_run.driver.username}, "
                f"Vehicle: {route_run.vehicle.vehicle_number}."
            ),
        )

        next_stop = route_run.stops.filter(
            stop_order=route_run.current_stop_order + 1
        ).first()

        if next_stop:
            route_run.current_stop_order += 1
            route_run.save(update_fields=["current_stop_order"])

            self.send_notification(
                next_stop.employee,
                (
                    f"Your cab is coming next. "
                    f"Pickup location: {next_stop.pickup_location}. "
                    f"Driver: {route_run.driver.username}, "
                    f"Vehicle: {route_run.vehicle.vehicle_number}."
                ),
            )

            remaining_stops = route_run.stops.filter(
                stop_order__gt=next_stop.stop_order,
                is_picked=False,
            )

            for stop in remaining_stops:
                self.send_notification(
                    stop.employee,
                    (
                        f"Cab is on the way. "
                        f"Next pickup is {next_stop.employee.username}. "
                        f"Driver: {route_run.driver.username}, "
                        f"Vehicle: {route_run.vehicle.vehicle_number}."
                    ),
                )

            return Response(
                {
                    "message": (
                        f"Stop completed successfully. "
                        f"Next pickup: {next_stop.employee.username}."
                    ),
                    "current_stop_order": route_run.current_stop_order,
                    "next_employee": next_stop.employee.username,
                    "all_pickups_completed": False,
                    "route_completed": False,
                },
                status=status.HTTP_200_OK,
            )

        for stop in route_run.stops.all():
            self.send_notification(
                stop.employee,
                (
                    f"All pickups completed. "
                    f"Cab is now going to office. "
                    f"Driver: {route_run.driver.username}, "
                    f"Vehicle: {route_run.vehicle.vehicle_number}."
                ),
            )

        return Response(
            {
                "message": "Last pickup completed. Cab is now going to office.",
                "all_pickups_completed": True,
                "route_completed": False,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"], url_path="complete_run")
    def complete_run(self, request, pk=None):
        route_run = self.get_object()

        if request.user.role != "DRIVER":
            return Response(
                {"error": "Only driver can complete route run."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if route_run.driver != request.user:
            return Response(
                {"error": "You are not assigned to this route run."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if route_run.completed_at is not None:
            return Response(
                {"error": "Route run already completed."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        pending_stops = route_run.stops.filter(is_picked=False).exists()
        if pending_stops:
            return Response(
                {"error": "Complete all pickup stops before finishing the trip."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        route_run.completed_at = timezone.now()
        route_run.save(update_fields=["completed_at"])

        stop_employee_ids = self._get_route_employee_ids(route_run)
        completed_time = timezone.now()

        Trip.objects.filter(
            employee_id__in=stop_employee_ids,
            pickup_time__date=route_run.run_date,
            trip_type=route_run.trip_type,
        ).exclude(
            status=Trip.STATUS_CANCELLED,
        ).update(
            route_run=route_run,
            driver=route_run.driver,
            vehicle=route_run.vehicle,
            status=Trip.STATUS_COMPLETED,
            end_time=completed_time,
        )

        for stop in route_run.stops.all():
            self.send_notification(
                stop.employee,
                (
                    f"Trip completed. Please submit your review. "
                    f"Driver: {route_run.driver.username}, "
                    f"Vehicle: {route_run.vehicle.vehicle_number}."
                ),
            )

        return Response(
            {
                "message": "Trip completed successfully. Cab has reached office.",
                "route_completed": True,
            },
            status=status.HTTP_200_OK,
        )

    def send_notification(self, user, message):
        if not user:
            return

        Notification.objects.create(
            user=user,
            title="Route Update",
            message=message,
        )