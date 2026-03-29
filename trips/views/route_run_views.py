from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from trips.models import Notification, RouteRun, RouteRunStop, Trip
from trips.serializers import RouteRunSerializer


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