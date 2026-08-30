from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from trips.services.notification_service import NotificationService
from trips.models import Notification, RouteRun, Trip
from trips.serializers import RouteRunSerializer
from trips.services.eta_service import ETAService
from trips.services.route_service import RouteService
from trips.services.chat_service import ChatService


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

    def _send_drop_start_notifications(self, route_run):
        """
        DROP trip start notifications.

        Employee:
            Only boarded employees receive:
            "Your drop trip has started."

        Admin:
            Receives vehicle number, boarded employee count,
            and No Show count.

        No distance, drop-position, next-drop, chat,
        or arrival FCM is sent for DROP.
        """

        # ---------------------------------------------------------
        # 1. Get only employees who actually boarded
        # ---------------------------------------------------------
        boarded_stops = list(
            route_run.stops
            .select_related("employee")
            .filter(
                is_boarded=True,
                is_no_show=False,
            )
            .order_by("-stop_order")
        )

        total_boarded = len(boarded_stops)

        # ---------------------------------------------------------
        # 2. Vehicle number
        # ---------------------------------------------------------
        vehicle_number = (
            route_run.vehicle.vehicle_number
            if route_run.vehicle
            else "Not assigned"
        )

        # ---------------------------------------------------------
        # 3. FCM → BOARDED EMPLOYEES
        #
        # Keep this simple.
        # We do NOT send:
        # - drop position
        # - next drop
        # - distance alerts
        # - arrival alerts
        # - pickup chat
        # ---------------------------------------------------------
        for stop in boarded_stops:
            NotificationService.send_notification(
                stop.employee,
                "Your drop trip has started.",
                title="Trip Started 🚕",
                push_data={
                    "type": "DROP_TRIP_STARTED",
                    "route_run_id": str(route_run.id),
                    "stop_id": str(stop.id),
                    "trip_type": route_run.trip_type,
                    "screen": "active_trip",
                },
            )

        # ---------------------------------------------------------
        # 4. Count No Show employees
        # ---------------------------------------------------------
        no_show_count = route_run.stops.filter(
            is_no_show=True,
        ).count()

        # ---------------------------------------------------------
        # 5. FCM / NOTIFICATION → ADMIN
        # ---------------------------------------------------------
        admin_message = (
            "Drop cab trip has started.\n"
            f"Cab Number: {vehicle_number}\n"
            f"Employees: {total_boarded}\n"
            f"No Show: {no_show_count}"
        )

        NotificationService.notify_admins(
            admin_message,
            title="Drop Trip Started 🚕",
            notification_type=Notification.TYPE_INFO,
            priority=Notification.PRIORITY_MEDIUM,
            route_run=route_run,
            driver=route_run.driver,
            push_data={
                "type": "DROP_ROUTE_STARTED",
                "route_run_id": str(route_run.id),
                "trip_type": route_run.trip_type,
                "vehicle_number": str(vehicle_number),
                "employee_count": str(total_boarded),
                "no_show_count": str(no_show_count),
                "screen": "admin_dashboard",
            },
        )
    def _send_pickup_start_notifications(self, route_run):
        ordered_stops = list(
            route_run.stops
            .select_related("employee")
            .filter(
                is_picked=False,
                is_no_show=False,
            )
            .order_by("-stop_order")
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

    def _send_last_two_drop_alert_if_needed(self, route_run):
        """
        Send one FCM to the final 2 employees who are still
        not boarded and not marked No Show.

        This alert is sent only once per Drop RouteRun.
        """

        if route_run.trip_type != Trip.TRIP_TYPE_DROP:
            return

        if route_run.last_two_drop_alert_sent:
            return

        pending_stops = list(
            route_run.stops
            .select_related("employee")
            .filter(
                is_boarded=False,
                is_no_show=False,
            )
        )

        if len(pending_stops) != 2:
            return

        for pending_stop in pending_stops:
            NotificationService.send_notification(
                pending_stop.employee,
                (
                    "Cab is going to start.\n"
                    "Please reach the cab as soon as possible."
                ),
                title="Cab Starting Soon 🚕",
                push_data={
                    "type": "DROP_LAST_TWO_PENDING",
                    "route_run_id": str(route_run.id),
                    "stop_id": str(pending_stop.id),
                    "trip_type": "DROP",
                    "screen": "trip_details",
                },
            )

        route_run.last_two_drop_alert_sent = True
        route_run.save(
            update_fields=["last_two_drop_alert_sent"]
        )

    @action(
        detail=True,
        methods=["post"],
        url_path=r"drop-board/(?P<stop_id>[^/.]+)",
    )
    def drop_board(self, request, pk=None, stop_id=None):
        route_run = self.get_object()

        if request.user.role != "DRIVER":
            return Response(
                {"error": "Only driver can mark employee boarded."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if route_run.driver != request.user:
            return Response(
                {"error": "You are not assigned to this route."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if route_run.trip_type != Trip.TRIP_TYPE_DROP:
            return Response(
                {"error": "This action is only for drop routes."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if route_run.started_at is not None:
            return Response(
                {"error": "Boarding cannot be changed after trip starts."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        stop = route_run.stops.filter(id=stop_id).first()

        if not stop:
            return Response(
                {"error": "Employee stop not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        stop.is_boarded = True
        stop.boarded_at = timezone.now()

        # Clear No Show if driver changes the decision
        stop.is_no_show = False
        stop.no_show_at = None

        stop.save(
            update_fields=[
                "is_boarded",
                "boarded_at",
                "is_no_show",
                "no_show_at",
            ]
        )

        NotificationService.send_notification(
            stop.employee,
            "You have been marked as boarded in your drop cab.",
            title="Boarding Confirmed ✅",
            push_data={
                "type": "DROP_BOARDED",
                "route_run_id": str(route_run.id),
                "stop_id": str(stop.id),
                "trip_type": "DROP",
            },
        )

        total = route_run.stops.count()
        boarded = route_run.stops.filter(is_boarded=True).count()
        no_show = route_run.stops.filter(is_no_show=True).count()
        pending = total - boarded - no_show

        # ---------------------------------------------------------
        # DROP: Alert the final 2 employees still not in the cab
        # ---------------------------------------------------------
        self._send_last_two_drop_alert_if_needed(route_run)

        return Response(
            {
                "message": f"{stop.employee.username} marked as boarded.",
                "stop_id": stop.id,
                "is_boarded": True,
                "is_no_show": False,
                "total": total,
                "boarded": boarded,
                "no_show": no_show,
                "pending": pending,
                "ready_to_start": pending == 0 and boarded > 0,
            },
            status=status.HTTP_200_OK,
        )

    @action(
        detail=True,
        methods=["post"],
        url_path=r"drop-no-show/(?P<stop_id>[^/.]+)",
    )
    def drop_no_show(self, request, pk=None, stop_id=None):
        route_run = self.get_object()

        if request.user.role != "DRIVER":
            return Response(
                {"error": "Only driver can mark no show."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if route_run.driver != request.user:
            return Response(
                {"error": "You are not assigned to this route."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if route_run.trip_type != Trip.TRIP_TYPE_DROP:
            return Response(
                {"error": "This action is only for drop routes."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if route_run.started_at is not None:
            return Response(
                {"error": "No Show cannot be changed after trip starts."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        stop = route_run.stops.filter(id=stop_id).first()

        if not stop:
            return Response(
                {"error": "Employee stop not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        stop.is_no_show = True
        stop.no_show_at = timezone.now()

        # Remove boarding if previously marked
        stop.is_boarded = False
        stop.boarded_at = None

        stop.save(
            update_fields=[
                "is_no_show",
                "no_show_at",
                "is_boarded",
                "boarded_at",
            ]
        )

        NotificationService.send_notification(
            stop.employee,
            "You have been marked as No Show for today's drop cab.",
            title="Drop No Show",
            push_data={
                "type": "DROP_NO_SHOW",
                "route_run_id": str(route_run.id),
                "stop_id": str(stop.id),
                "trip_type": "DROP",
            },
        )

        total = route_run.stops.count()
        boarded = route_run.stops.filter(is_boarded=True).count()
        no_show = route_run.stops.filter(is_no_show=True).count()
        pending = total - boarded - no_show
        self._send_last_two_drop_alert_if_needed(route_run)

        return Response(
            {
                "message": f"{stop.employee.username} marked as No Show.",
                "stop_id": stop.id,
                "is_boarded": False,
                "is_no_show": True,
                "total": total,
                "boarded": boarded,
                "no_show": no_show,
                "pending": pending,
                "ready_to_start": pending == 0 and boarded > 0,
            },
            status=status.HTTP_200_OK,
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

        # DROP: every assigned employee must be Boarded or No Show
        # before the route can start. At least one employee must board.
        if (
            route_run.trip_type == Trip.TRIP_TYPE_DROP
            and route_run.started_at is None
        ):
            total = route_run.stops.count()
            boarded = route_run.stops.filter(is_boarded=True).count()
            no_show = route_run.stops.filter(is_no_show=True).count()
            pending = total - boarded - no_show

            if pending > 0:
                return Response(
                    {
                        "error": (
                            f"{pending} employee(s) are still pending. "
                            "Mark them Pickup Done or No Show before "
                            "starting the Drop Trip."
                        ),
                        "total": total,
                        "boarded": boarded,
                        "no_show": no_show,
                        "pending": pending,
                        "ready_to_start": False,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if boarded == 0:
                return Response(
                    {
                        "error": (
                            "No employee has boarded the cab. "
                            "Drop Trip cannot be started."
                        ),
                        "total": total,
                        "boarded": 0,
                        "no_show": no_show,
                        "pending": 0,
                        "ready_to_start": False,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        already_started = route_run.started_at is not None

        if not already_started:
            started_time = timezone.now()
            route_run.started_at = started_time
            route_run.save(update_fields=["started_at"])

            trip_qs = self._get_related_trip_queryset(route_run)

            if route_run.trip_type == Trip.TRIP_TYPE_DROP:
                boarded_stops = route_run.stops.filter(
                    is_boarded=True,
                    is_no_show=False,
                ).order_by("-stop_order")

                boarded_employee_ids = list(
                    boarded_stops.values_list("employee_id", flat=True)
                )

                # Start home-drop progression at the first boarded employee.
                # No Show employees are automatically skipped here.
                first_drop_stop = boarded_stops.first()
                if first_drop_stop:
                    route_run.current_stop_order = first_drop_stop.stop_order
                    route_run.save(update_fields=["current_stop_order"])

                boarded_trip_qs = trip_qs.filter(
                    employee_id__in=boarded_employee_ids
                )
                boarded_trip_qs.update(
                    route_run=route_run,
                    driver=route_run.driver,
                    vehicle=route_run.vehicle,
                    status=Trip.STATUS_STARTED,
                    start_time=started_time,
                )
            else:
                # Preserve existing Pickup start behavior.
                trip_qs.update(
                    route_run=route_run,
                    driver=route_run.driver,
                    vehicle=route_run.vehicle,
                    status=Trip.STATUS_STARTED,
                    start_time=started_time,
                )

            if route_run.trip_type == Trip.TRIP_TYPE_PICKUP:
                self._send_pickup_start_notifications(route_run)
            elif route_run.trip_type == Trip.TRIP_TYPE_DROP:
                self._send_drop_start_notifications(route_run)

        response_data = {
            "message": (
                "Route run was already started."
                if already_started
                else "Route run started successfully."
            ),
            "already_started": already_started,
            "route_run_id": route_run.id,
            "started_at": route_run.started_at,
            "trip_type": route_run.trip_type,
        }

        if route_run.trip_type == Trip.TRIP_TYPE_DROP:
            total = route_run.stops.count()
            boarded = route_run.stops.filter(is_boarded=True).count()
            no_show = route_run.stops.filter(is_no_show=True).count()
            response_data.update(
                {
                    "total": total,
                    "boarded": boarded,
                    "no_show": no_show,
                    "pending": total - boarded - no_show,
                    "ready_to_start": (
                        total - boarded - no_show == 0 and boarded > 0
                    ),
                }
            )

        return Response(response_data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="complete_stop")
    def complete_stop(self, request, pk=None):
        route_run = self.get_object()

        # ---------------------------------------------------------
        # 1. Only DRIVER can complete a stop
        # ---------------------------------------------------------
        if request.user.role != "DRIVER":
            return Response(
                {"error": "Only driver can complete stop."},
                status=status.HTTP_403_FORBIDDEN,
            )

        # ---------------------------------------------------------
        # 2. Driver must belong to this route
        # ---------------------------------------------------------
        if route_run.driver != request.user:
            return Response(
                {"error": "You are not assigned to this route run."},
                status=status.HTTP_403_FORBIDDEN,
            )

        # ---------------------------------------------------------
        # 3. Route must already be started
        # ---------------------------------------------------------
        if route_run.started_at is None:
            return Response(
                {"error": "Start the route before completing a stop."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ---------------------------------------------------------
        # 4. Completed route cannot be changed
        # ---------------------------------------------------------
        if route_run.completed_at is not None:
            return Response(
                {"error": "Route run already completed."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ---------------------------------------------------------
        # 5. Find current active stop
        #
        # PICKUP:
        #   pending pickup employee
        #
        # DROP:
        #   only boarded + not No Show + not already dropped
        # ---------------------------------------------------------
        current_stop = RouteService.get_current_stop(route_run)

        if not current_stop:
            remaining_stops, ready_to_complete = (
                RouteService.complete_route_if_finished(route_run)
            )

            return Response(
                {
                    "error": "No current stop found.",
                    "remaining_stops": remaining_stops,
                    "ready_to_complete": ready_to_complete,
                    "route_completed": False,
                    "trip_type": route_run.trip_type,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ---------------------------------------------------------
        # 6. Complete current stop
        #
        # RouteService handles:
        # PICKUP → Pickup Completed notification
        # DROP   → Drop Completed notification
        # ---------------------------------------------------------
        next_stop = RouteService.handle_stop_done(
            route_run,
            current_stop,
        )

        # ---------------------------------------------------------
        # 7. Check remaining stops
        # ---------------------------------------------------------
        remaining_stops, ready_to_complete = (
            RouteService.complete_route_if_finished(
                route_run
            )
        )

        # ---------------------------------------------------------
        # 8. Pickup chat belongs ONLY to PICKUP
        # DROP has no chat / waiting workflow
        # ---------------------------------------------------------
        if route_run.trip_type == Trip.TRIP_TYPE_PICKUP:
            try:
                ChatService.close_chat_for_stop(
                    route_run,
                    current_stop,
                )
            except Exception as e:
                print(
                    "CHAT CLOSE ERROR:",
                    e,
                )

        # ---------------------------------------------------------
        # 9. Update current_stop_order for UI compatibility
        #
        # RouteService decides the real next stop.
        # No Show employees are automatically skipped.
        # ---------------------------------------------------------
        if next_stop:
            route_run.current_stop_order = next_stop.stop_order
            route_run.save(
                update_fields=["current_stop_order"]
            )

        action_word = (
            "Drop"
            if route_run.trip_type == Trip.TRIP_TYPE_DROP
            else "Pickup"
        )

        # ---------------------------------------------------------
        # 10. Return result to Flutter
        # ---------------------------------------------------------
        return Response(
            {
                "message": (
                    f"{action_word} completed successfully."
                    if next_stop
                    else (
                        f"Last {action_word.lower()} completed successfully."
                    )
                ),
                "completed_stop_id": current_stop.id,
                "completed_employee": current_stop.employee.username,

                "next_stop_id": (
                    next_stop.id
                    if next_stop
                    else None
                ),

                "next_stop_order": (
                    next_stop.stop_order
                    if next_stop
                    else None
                ),

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

    @action(detail=True, methods=["post"], url_path="complete_run")
    def complete_run(self, request, pk=None):
        route_run = self.get_object()

        # ---------------------------------------------------------
        # 1. Only DRIVER can complete route
        # ---------------------------------------------------------
        if request.user.role != "DRIVER":
            return Response(
                {"error": "Only driver can complete route run."},
                status=status.HTTP_403_FORBIDDEN,
            )

        # ---------------------------------------------------------
        # 2. Driver must belong to route
        # ---------------------------------------------------------
        if route_run.driver != request.user:
            return Response(
                {"error": "You are not assigned to this route run."},
                status=status.HTTP_403_FORBIDDEN,
            )

        # ---------------------------------------------------------
        # 3. Route must already be started
        # ---------------------------------------------------------
        if route_run.started_at is None:
            return Response(
                {"error": "Route run has not been started."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ---------------------------------------------------------
        # 4. Prevent duplicate completion
        # ---------------------------------------------------------
        if route_run.completed_at is not None:
            return Response(
                {
                    "message": "Route run was already completed.",
                    "already_completed": True,
                    "route_run_id": route_run.id,
                    "completed_at": route_run.completed_at,
                },
                status=status.HTTP_200_OK,
            )

        # ---------------------------------------------------------
        # 5. Make sure all required stops are completed
        #
        # RouteService now handles:
        #
        # PICKUP:
        #   is_picked=False + is_no_show=False
        #
        # DROP:
        #   is_boarded=True
        #   is_no_show=False
        #   is_picked=False
        # ---------------------------------------------------------
        remaining_stops, ready_to_complete = (
            RouteService.complete_route_if_finished(route_run)
        )

        if not ready_to_complete:
            return Response(
                {
                    "error": (
                        f"{remaining_stops} stop(s) are still pending. "
                        "Complete all stops before completing the route."
                    ),
                    "remaining_stops": remaining_stops,
                    "ready_to_complete": False,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        completed_time = timezone.now()

        # ---------------------------------------------------------
        # 6. Complete RouteRun
        # ---------------------------------------------------------
        route_run.completed_at = completed_time
        route_run.save(
            update_fields=["completed_at"]
        )

        trip_qs = self._get_related_trip_queryset(route_run)

        # ---------------------------------------------------------
        # 7. Complete related Trips
        # ---------------------------------------------------------
        if route_run.trip_type == Trip.TRIP_TYPE_DROP:

            # Only employees who boarded and were actually dropped.
            completed_employee_ids = list(
                route_run.stops.filter(
                    is_boarded=True,
                    is_no_show=False,
                    is_picked=True,
                ).values_list(
                    "employee_id",
                    flat=True,
                )
            )

            completed_trip_qs = trip_qs.filter(
                employee_id__in=completed_employee_ids
            )

            completed_trip_qs.update(
                status=Trip.STATUS_COMPLETED,
                end_time=completed_time,
            )

        else:
            # Preserve existing Pickup completion behavior.
            completed_employee_ids = list(
                route_run.stops.filter(
                    is_picked=True,
                    is_no_show=False,
                ).values_list(
                    "employee_id",
                    flat=True,
                )
            )

            completed_trip_qs = trip_qs.filter(
                employee_id__in=completed_employee_ids
            )

            completed_trip_qs.update(
                status=Trip.STATUS_COMPLETED,
                end_time=completed_time,
            )

        # ---------------------------------------------------------
        # 8. Route completion notifications
        # ---------------------------------------------------------
        try:
            RouteService.notify_route_completed(route_run)
        except Exception as e:
            print(
                "ROUTE COMPLETION NOTIFICATION ERROR:",
                e,
            )

        # ---------------------------------------------------------
        # 9. Final response to Flutter
        # ---------------------------------------------------------
        return Response(
            {
                "message": (
                    "Drop route completed successfully."
                    if route_run.trip_type == Trip.TRIP_TYPE_DROP
                    else "Pickup route completed successfully."
                ),
                "already_completed": False,
                "route_run_id": route_run.id,
                "trip_type": route_run.trip_type,
                "completed_at": route_run.completed_at,
                "completed_employee_count": len(
                    completed_employee_ids
                ),
                "remaining_stops": 0,
                "ready_to_complete": True,
                "route_completed": True,
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