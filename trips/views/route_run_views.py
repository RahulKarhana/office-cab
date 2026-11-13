from datetime import datetime, time, timedelta

from django.db.models import Min, Q
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

        # Employees may only access runs in which they have a trip.
        # This does not grant access to other employees' routes.
        if getattr(user, "role", "") == "EMPLOYEE" and self.action == "live_status":
            return RouteRun.objects.filter(
                trips__employee=user,
            ).distinct().prefetch_related(
                "stops__employee"
            ).select_related(
                "route_template", "driver", "vehicle"
            ).order_by("-created_at")

        return RouteRun.objects.none()

    # Operational visibility is based on office shift date plus the actual
    # scheduled departure (Trip.pickup_time). No migration is required.
    # Unstarted overnight DROP routes have a four-hour post-departure grace
    # period. Started routes remain visible for up to 24 hours; older ones
    # require admin intervention instead of staying active indefinitely.
    OVERNIGHT_START_GRACE = timedelta(hours=4)
    SHIFT_DAY_START = time(hour=6, minute=0)
    ROUTE_START_EARLY = timedelta(hours=2)
    ROUTE_START_LATE = timedelta(hours=4)

    def _driver_runs(self, user):
        return self.get_queryset().filter(driver=user).annotate(
            scheduled_at=Min(
                "trips__pickup_time",
                filter=~Q(trips__status=Trip.STATUS_CANCELLED),
            )
        )

    @staticmethod
    def _date_at_midnight(day):
        naive = datetime.combine(day, time.min)
        return timezone.make_aware(naive, timezone.get_current_timezone())

    def _route_is_cancelled(self, route):
        # RouteRun has no cancellation field. A run is treated as cancelled
        # only when it has linked trips and ALL of them are cancelled.
        linked = route.trips.all()
        return bool(linked) and all(
            trip.status == Trip.STATUS_CANCELLED for trip in linked
        )

    def _operational_day(self, now):
        local_now = timezone.localtime(now)
        day = local_now.date()
        if local_now.time() < self.SHIFT_DAY_START:
            day -= timedelta(days=1)
        return day

    def _scheduled_start_error(self, route, now):
        """Return an error for an unstarted route outside its start window."""
        scheduled = Trip.objects.filter(
            route_run=route,
        ).exclude(status=Trip.STATUS_CANCELLED).aggregate(
            first=Min("pickup_time")
        )["first"]
        if scheduled is None:
            return "Route has no scheduled departure. Contact Admin."
        if timezone.is_naive(scheduled):
            scheduled = timezone.make_aware(scheduled, timezone.get_current_timezone())
        if not scheduled - self.ROUTE_START_EARLY <= now <= scheduled + self.ROUTE_START_LATE:
            return "Route cannot start outside its scheduled departure window. Contact Admin for rescheduling."
        return None

    def _valid_previous_shift_drop(self, route, now, today):
        if route.trip_type != Trip.TRIP_TYPE_DROP:
            return False
        if route.run_date != today - timedelta(days=1):
            return False
        if route.completed_at is not None or self._route_is_cancelled(route):
            return False

        scheduled = route.scheduled_at
        if scheduled is None:
            # Cannot prove that this is an overnight DROP.
            return False
        if timezone.is_naive(scheduled):
            scheduled = timezone.make_aware(scheduled, timezone.get_current_timezone())
        if timezone.localtime(scheduled).date() != today:
            return False

        if route.started_at is not None:
            started = route.started_at
            if timezone.is_naive(started):
                started = timezone.make_aware(started, timezone.get_current_timezone())
            return started <= now

        return scheduled - timedelta(hours=2) <= now <= scheduled + self.OVERNIGHT_START_GRACE

    @action(detail=False, methods=["get"], url_path="today_active")
    def today_active(self, request):
        if getattr(request.user, "role", "") != "DRIVER":
            return Response(
                {"error": "Only driver can view active route run."},
                status=status.HTTP_403_FORBIDDEN,
            )

        now = timezone.now()
        shift_day = self._operational_day(now)
        previous_day = shift_day - timedelta(days=1)
        runs = list(self._driver_runs(request.user).filter(
            Q(run_date__in=[previous_day, shift_day]) |
            Q(started_at__isnull=False, completed_at__isnull=True)
        ).prefetch_related("trips"))

        eligible_today = [
            run for run in runs
            if run.run_date == shift_day
            and run.completed_at is None
            and not self._route_is_cancelled(run)
        ]
        overnight = [
            run for run in runs
            if self._valid_previous_shift_drop(run, now, shift_day + timedelta(days=1))
            or (run.trip_type == Trip.TRIP_TYPE_DROP
                and run.run_date < shift_day
                and run.started_at is not None
                and run.completed_at is None
                and not self._route_is_cancelled(run))
        ]
        # Deterministic selection: started older DROP first, then nearest
        # unstarted overnight DROP, then this shift's DROP.
        overnight.sort(key=lambda r: (
            0 if r.started_at else 1,
            r.started_at or r.scheduled_at or self._date_at_midnight(r.run_date),
            r.pk,
        ))
        eligible_today.sort(key=lambda r: (r.scheduled_at or self._date_at_midnight(r.run_date), r.pk))
        active_pickups = sorted(
            (r for r in runs if r.trip_type == Trip.TRIP_TYPE_PICKUP
             and r.started_at is not None and r.completed_at is None
             and not self._route_is_cancelled(r)),
            key=lambda r: (r.started_at, r.pk),
        )
        pickup = next(iter(active_pickups), None) or next(
            (r for r in eligible_today if r.trip_type == Trip.TRIP_TYPE_PICKUP), None
        )
        drop = next(iter(overnight), None) or next(
            (r for r in eligible_today if r.trip_type == Trip.TRIP_TYPE_DROP), None
        )

        return Response({
            "pickup": self.get_serializer(pickup).data if pickup else None,
            "drop": self.get_serializer(drop).data if drop else None,
            "operational_date": shift_day.isoformat(),
            "overnight_drop": bool(drop and drop.run_date < shift_day),
        })

    @action(detail=False, methods=["get"], url_path="upcoming_assignments")
    def upcoming_assignments(self, request):
        if getattr(request.user, "role", "") != "DRIVER":
            return Response(
                {"error": "Only driver can view upcoming assignments."},
                status=status.HTTP_403_FORBIDDEN,
            )

        today = self._operational_day(timezone.now())
        last_day = today + timedelta(days=6)
        runs = list(self._driver_runs(request.user).filter(
            run_date__range=(today, last_day),
            completed_at__isnull=True,
        ).prefetch_related("trips"))
        runs = [run for run in runs if not self._route_is_cancelled(run)]
        runs.sort(key=lambda run: (
            run.run_date,
            run.scheduled_at or self._date_at_midnight(run.run_date),
            run.pk,
        ))
        return Response({
            "from_date": today.isoformat(),
            "through_date": last_day.isoformat(),
            "routes": self.get_serializer(runs, many=True).data,
        })

    def _get_route_employee_ids(self, route_run):
        return list(route_run.stops.values_list("employee_id", flat=True))

    def _get_related_trip_queryset(self, route_run):
        stop_employee_ids = self._get_route_employee_ids(route_run)

        return Trip.objects.filter(
            route_run=route_run,
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
        boarded_stops = list(RouteService.get_ordered_stops(route_run).filter(
            is_boarded=True,
            is_no_show=False,
        ))

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
        ordered_stops = list(RouteService.get_pending_stops(route_run))

        total_stops = len(ordered_stops)

        driver_name = (
            route_run.driver.display_name
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
                "message": f"{stop.employee.display_name} marked as boarded.",
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
                "message": f"{stop.employee.display_name} marked as No Show.",
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

        if self._route_is_cancelled(route_run):
            return Response({"error": "Route has been cancelled."}, status=status.HTTP_400_BAD_REQUEST)

        if route_run.started_at is None:
            schedule_error = self._scheduled_start_error(route_run, timezone.now())
            if schedule_error:
                return Response({"error": schedule_error}, status=status.HTTP_400_BAD_REQUEST)

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
                boarded_stops = RouteService.get_pending_stops(route_run)

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
                "completed_employee": current_stop.employee.display_name,
                "late_seconds": current_stop.late_seconds,

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
                    next_stop.employee.display_name
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
            # =====================================================
            # PICKUP ROUTE COMPLETION
            # =====================================================

            # Employees actually picked up.
            picked_employee_ids = list(
                route_run.stops.filter(
                    is_picked=True,
                    is_no_show=False,
                ).values_list(
                    "employee_id",
                    flat=True,
                )
            )

            # Employees resolved as No Show.
            no_show_employee_ids = list(
                route_run.stops.filter(
                    is_no_show=True,
                ).values_list(
                    "employee_id",
                    flat=True,
                )
            )

            # Close successful pickup trips.
            trip_qs.filter(
                employee_id__in=picked_employee_ids
            ).update(
                status=Trip.STATUS_COMPLETED,
                end_time=completed_time,
            )

            # No Show is also a finished pickup outcome.
            # Do not leave the Trip as STARTED.
            trip_qs.filter(
                employee_id__in=no_show_employee_ids
            ).update(
                status=Trip.STATUS_COMPLETED,
                end_time=completed_time,
            )

            completed_employee_ids = list(
                set(
                    picked_employee_ids
                    + no_show_employee_ids
                )
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