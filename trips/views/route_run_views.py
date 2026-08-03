from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from trips.services.notification_service import NotificationService
from trips.models import Notification, RouteRun, Trip
from trips.serializers import RouteRunSerializer
from trips.services.eta_service import ETAService

class RouteRunViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = RouteRunSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user

        if getattr(user, "role", "") == "ADMIN" or user.is_superuser:
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

        pickup_run = RouteRun.objects.filter(
            driver=user,
            run_date=today,
            trip_type="PICKUP",
        ).prefetch_related(
            "stops__employee"
        ).select_related(
            "route_template",
            "driver",
            "vehicle",
        ).order_by("-created_at").first()

        drop_run = RouteRun.objects.filter(
            driver=user,
            run_date=today,
            trip_type="DROP",
        ).prefetch_related(
            "stops__employee"
        ).select_related(
            "route_template",
            "driver",
            "vehicle",
        ).order_by("-created_at").first()

        return Response({
            "pickup": self.get_serializer(pickup_run).data if pickup_run else None,
            "drop": self.get_serializer(drop_run).data if drop_run else None,
        })

    def _get_route_employee_ids(self, route_run):
        return list(route_run.stops.values_list("employee_id", flat=True))

    def _get_related_trip_queryset(self, route_run):
        stop_employee_ids = self._get_route_employee_ids(route_run)

        return Trip.objects.filter(
            employee_id__in=stop_employee_ids,
            trip_date=route_run.run_date,
            trip_type=route_run.trip_type,
        ).exclude(
            status=Trip.STATUS_CANCELLED,
        )
    def _send_pickup_start_notifications(self, route_run):
        ordered_stops = list(
            route_run.stops
            .select_related("employee")
            .filter(
                is_picked=False,
                is_no_show=False,
            )
            .order_by("stop_order")
        )

        total_stops = len(ordered_stops)

        driver_name = (
            route_run.driver.username
            if route_run.driver
            else "Driver"
        )

        vehicle_number = (
            route_run.vehicle.vehicle_number
            if route_run.vehicle
            else "Not assigned"
        )

        route_name = (
            route_run.route_template.name
            if route_run.route_template
            else "Pickup Route"
        )

        for index, stop in enumerate(ordered_stops):
            turn_number = index + 1

            if total_stops == 1:
                position_message = (
                    "You are the only pickup and the driver is coming to you."
                )
                position_type = "ONLY_PICKUP"

            elif turn_number == 1:
                position_message = (
                    "You are the next pickup. Please be ready."
                )
                position_type = "NEXT_PICKUP"

            elif turn_number == total_stops:
                position_message = (
                    "You are the last pickup in today's route."
                )
                position_type = "LAST_PICKUP"

            else:
                position_message = (
                    f"Your pickup turn number is {turn_number}."
                )
                position_type = "PICKUP_POSITION"

            message = (
                "Your pickup cab has started.\n"
                f"{position_message}\n"
                f"Driver: {driver_name}\n"
                f"Vehicle: {vehicle_number}"
            )

            NotificationService.send_notification(
                stop.employee,
                message,
                title="Pickup Cab Started 🚕",
                push_data={
                    "type": position_type,
                    "route_run_id": str(route_run.id),
                    "stop_id": str(stop.id),
                    "trip_type": route_run.trip_type,
                    "turn_number": str(turn_number),
                    "total_stops": str(total_stops),
                    "screen": "active_trip",
                },
            )

        admin_message = (
            "Pickup route has started.\n"
            f"Driver: {driver_name}\n"
            f"Vehicle: {vehicle_number}\n"
            f"Route: {route_name}\n"
            f"Employees assigned: {total_stops}"
        )

        NotificationService.notify_admins(
            admin_message,
            title="Pickup Route Started 🚕",
            notification_type=Notification.TYPE_INFO,
            priority=Notification.PRIORITY_MEDIUM,
            route_run=route_run,
            driver=route_run.driver,
            push_data={
                "type": "PICKUP_ROUTE_STARTED",
                "route_run_id": str(route_run.id),
                "trip_type": route_run.trip_type,
                "employee_count": str(total_stops),
                "screen": "admin_dashboard",
            },
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

        already_started = route_run.started_at is not None

        if not already_started:
            started_time = timezone.now()

            route_run.started_at = started_time
            route_run.save(
                update_fields=["started_at"],
            )

            trip_qs = self._get_related_trip_queryset(route_run)

            trip_qs.update(
                route_run=route_run,
                driver=route_run.driver,
                vehicle=route_run.vehicle,
                status=Trip.STATUS_STARTED,
                start_time=started_time,
            )

            if route_run.trip_type == Trip.TRIP_TYPE_PICKUP:
                self._send_pickup_start_notifications(route_run)

        return Response(
            {
                "message": (
                    "Route run was already started."
                    if already_started
                    else "Route run started successfully."
                ),
                "already_started": already_started,
                "route_run_id": route_run.id,
                "started_at": route_run.started_at,
            },
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

        NotificationService.send_notification(
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

            NotificationService.send_notification(
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
                NotificationService.send_notification(
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
            NotificationService.send_notification(
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


    @action(detail=True, methods=["get"], url_path="live_status")
    def live_status(self, request, pk=None):
        route_run = self.get_object()

        employee_user = None

        if getattr(request.user, "role", "") == "EMPLOYEE":
            employee_user = request.user

        data = ETAService.build_live_status(
            route_run,
            employee_user=employee_user,
        )
        return Response(data, status=status.HTTP_200_OK)
    
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

        if route_run.started_at is None:
            return Response(
                {"error": "Start the route before completing a stop."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if route_run.completed_at is not None:
            return Response(
                {"error": "Route run already completed."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        current_stop = RouteService.get_current_stop(route_run)

        if not current_stop:
            return Response(
                {
                    "error": "No current stop found.",
                    "ready_to_complete": True,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            next_stop = RouteService.handle_stop_done(
                route_run,
                current_stop,
            )

            remaining_stops, ready_to_complete = (
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
                "message": f"{action_word} completed successfully.",
                "completed_stop_id": current_stop.id,
                "next_stop_id": next_stop.id if next_stop else None,
                "next_employee": (
                    next_stop.employee.username
                    if next_stop
                    else None
                ),
                "remaining_stops": remaining_stops,
                "ready_to_complete": ready_to_complete,
                "route_completed": False,
                "trip_type": route_run.trip_type,
            },
            status=status.HTTP_200_OK,
        )
        