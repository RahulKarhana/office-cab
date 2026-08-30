from datetime import datetime, time

from django.utils import timezone

from trips.models import Notification, Trip
from trips.services.notification_service import NotificationService


class RouteService:
    # ============================================================
    # ROUTE ORDERING
    # Pickup: first pickup -> last pickup
    # Drop: last pickup -> first pickup
    # ============================================================

    @staticmethod
    def get_ordered_stops(route_run):
        stops = route_run.stops.select_related("employee")

        if route_run.trip_type == Trip.TRIP_TYPE_DROP:
            return stops.order_by("-stop_order")

        return stops.order_by("stop_order")

    @staticmethod
    def get_pending_stops(route_run):
        stops = RouteService.get_ordered_stops(route_run).filter(
            is_picked=False,
            is_no_show=False,
        )

        # DROP:
        # Only employees who actually boarded the cab at office
        # can become home-drop stops.
        if route_run.trip_type == Trip.TRIP_TYPE_DROP:
            stops = stops.filter(
                is_boarded=True,
            )

        return stops

    @staticmethod
    def get_current_stop(route_run):
        return RouteService.get_pending_stops(route_run).first()

    @staticmethod
    def get_next_stop_after_current(route_run, current_stop):
        if not route_run or not current_stop:
            return None

        pending_stops = list(
            RouteService.get_pending_stops(route_run)
        )

        for index, stop in enumerate(pending_stops):
            if stop.id == current_stop.id:
                next_index = index + 1
                if next_index < len(pending_stops):
                    return pending_stops[next_index]
                return None

        return None

    # ============================================================
    # SAFE DISPLAY HELPERS
    # ============================================================

    @staticmethod
    def _driver_name(route_run):
        if route_run.driver:
            return route_run.driver.username
        return "Driver"

    @staticmethod
    def _vehicle_number(route_run):
        if route_run.vehicle:
            return route_run.vehicle.vehicle_number
        return "Not assigned"

    @staticmethod
    def _route_name(route_run):
        if route_run.route_template:
            return route_run.route_template.name

        return (
            "Drop Route"
            if route_run.trip_type == Trip.TRIP_TYPE_DROP
            else "Pickup Route"
        )

    # ============================================================
    # PICKUP POSITION MESSAGES
    # ============================================================

    @staticmethod
    def _pickup_position_message(position, total):
        if total <= 1:
            return (
                "You are the only remaining pickup. "
                "The driver is coming to you."
            )

        if position == 1:
            return "You are next. Please be ready."

        if position == total:
            return "You are the last remaining pickup."

        return f"Your updated pickup turn number is {position}."

    @staticmethod
    def _notify_pending_pickup_positions(route_run):
        if route_run.trip_type != Trip.TRIP_TYPE_PICKUP:
            return

        pending_stops = list(
            RouteService.get_pending_stops(route_run)
        )
        total = len(pending_stops)

        driver_name = RouteService._driver_name(route_run)
        vehicle_number = RouteService._vehicle_number(route_run)

        for index, stop in enumerate(pending_stops, start=1):
            if index == 1:
                title = "You Are Next 🚕"
                notification_type = "NEXT_PICKUP"
            elif index == total:
                title = "Pickup Route Update 🚕"
                notification_type = "LAST_PICKUP"
            else:
                title = "Pickup Route Update 🚕"
                notification_type = "PICKUP_POSITION_UPDATED"

            NotificationService.send_notification(
                stop.employee,
                (
                    "The cab is progressing along the pickup route.\n"
                    f"{RouteService._pickup_position_message(index, total)}\n"
                    f"Driver: {driver_name}\n"
                    f"Vehicle: {vehicle_number}"
                ),
                title=title,
                push_data={
                    "type": notification_type,
                    "route_run_id": str(route_run.id),
                    "stop_id": str(stop.id),
                    "turn_number": str(index),
                    "remaining_count": str(total),
                    "trip_type": route_run.trip_type,
                    "screen": "active_trip",
                },
            )

    # ============================================================
    # START TRIP NOTIFICATIONS
    # ============================================================

    @staticmethod
    def handle_trip_start_notifications(trip):
        route_run = trip.route_run

        if not route_run:
            message = (
                "Your pickup cab has started 🚖"
                if trip.trip_type == Trip.TRIP_TYPE_PICKUP
                else "Your drop cab has started 🚖"
            )

            NotificationService.send_notification(
                trip.employee,
                message,
                title="🚖 Trip Started",
                push_data={
                    "type": "TRIP_STARTED",
                    "trip_id": str(trip.id),
                    "trip_type": trip.trip_type,
                    "screen": "active_trip",
                },
            )
            return

        if not route_run.started_at:
            started_time = timezone.now()
            route_run.started_at = started_time
            route_run.save(update_fields=["started_at"])

            Trip.objects.filter(
                route_run=route_run,
                status=Trip.STATUS_ASSIGNED,
            ).update(
                status=Trip.STATUS_STARTED,
                start_time=started_time,
            )

        if getattr(route_run, "start_notifications_sent", False):
            return

        stops = list(RouteService.get_ordered_stops(route_run))
        total = len(stops)

        driver_name = RouteService._driver_name(route_run)
        vehicle_number = RouteService._vehicle_number(route_run)
        route_name = RouteService._route_name(route_run)

        if route_run.trip_type == Trip.TRIP_TYPE_PICKUP:
            for index, stop in enumerate(stops, start=1):
                NotificationService.send_notification(
                    stop.employee,
                    (
                        "Your pickup cab has started.\n"
                        f"{RouteService._pickup_position_message(index, total)}\n"
                        f"Driver: {driver_name}\n"
                        f"Vehicle: {vehicle_number}"
                    ),
                    title="Pickup Cab Started 🚕",
                    push_data={
                        "type": (
                            "NEXT_PICKUP"
                            if index == 1
                            else "LAST_PICKUP"
                            if index == total
                            else "PICKUP_POSITION"
                        ),
                        "trip_id": str(trip.id),
                        "route_run_id": str(route_run.id),
                        "stop_id": str(stop.id),
                        "turn_number": str(index),
                        "total_stops": str(total),
                        "trip_type": route_run.trip_type,
                        "screen": "active_trip",
                    },
                )

            NotificationService.notify_admins(
                (
                    "Pickup route has started.\n"
                    f"Driver: {driver_name}\n"
                    f"Vehicle: {vehicle_number}\n"
                    f"Route: {route_name}\n"
                    f"Employees assigned: {total}"
                ),
                title="Pickup Route Started 🚕",
                notification_type=Notification.TYPE_INFO,
                priority=Notification.PRIORITY_MEDIUM,
                route_run=route_run,
                driver=route_run.driver,
                push_data={
                    "type": "PICKUP_ROUTE_STARTED",
                    "route_run_id": str(route_run.id),
                    "trip_type": route_run.trip_type,
                    "employee_count": str(total),
                    "screen": "admin_dashboard",
                },
            )

        else:
            for stop in stops:
                NotificationService.send_notification(
                    stop.employee,
                    (
                        "Your drop cab has started from the office.\n"
                        f"Driver: {driver_name}\n"
                        f"Vehicle: {vehicle_number}"
                    ),
                    title="Drop Cab Started 🚕",
                    push_data={
                        "type": "DROP_ROUTE_STARTED",
                        "trip_id": str(trip.id),
                        "route_run_id": str(route_run.id),
                        "stop_id": str(stop.id),
                        "trip_type": route_run.trip_type,
                        "screen": "active_trip",
                    },
                )

        if hasattr(route_run, "start_notifications_sent"):
            route_run.start_notifications_sent = True
            route_run.save(
                update_fields=["start_notifications_sent"],
            )

    # ============================================================
    # STOP COMPLETION
    # Pickup Done / Drop Done
    # ============================================================

    @staticmethod
    def handle_stop_done(route_run, current_stop):
        if current_stop.is_picked:
            return RouteService.get_next_stop_after_current(
                route_run,
                current_stop,
            )

        completed_time = timezone.now()

        current_stop.is_picked = True
        current_stop.picked_at = completed_time

        update_fields = ["is_picked", "picked_at"]

        if hasattr(
            current_stop,
            "pickup_completed_notification_sent",
        ):
            current_stop.pickup_completed_notification_sent = True
            update_fields.append(
                "pickup_completed_notification_sent"
            )

        current_stop.save(update_fields=update_fields)

        driver_name = RouteService._driver_name(route_run)
        vehicle_number = RouteService._vehicle_number(route_run)

        if route_run.trip_type == Trip.TRIP_TYPE_PICKUP:

            # Pickup stop completed.
            # Do NOT send review notification here.
            # Review is available only after the entire route/trip completes.

            next_stop = RouteService.get_current_stop(route_run)

            if next_stop:
                RouteService._notify_pending_pickup_positions(
                    route_run,
                )
            else:
                RouteService.notify_heading_to_office(
                    route_run,
                )

            return next_stop

        # ------------------------------------------------------------
        # DROP STOP COMPLETED
        # ------------------------------------------------------------
        # Employee has reached home, but the complete cab route may
        # still be running for other employees.
        # Therefore DO NOT ask for a review here.
        # ------------------------------------------------------------

        NotificationService.send_notification(
            current_stop.employee,
            (
                "You have reached your drop location successfully.\n"
                f"Driver: {driver_name}\n"
                f"Vehicle: {vehicle_number}"
            ),
            title="Drop Completed ✅",
            push_data={
                "type": "DROP_STOP_COMPLETED",
                "route_run_id": str(route_run.id),
                "stop_id": str(current_stop.id),
                "trip_type": route_run.trip_type,
                "screen": "active_trip",
            },
        )

        return RouteService.get_current_stop(route_run)

    # ============================================================
    # ROUTE COMPLETION STATE
    # ============================================================

    @staticmethod
    def complete_route_if_finished(route_run):
        remaining_stops = RouteService.get_pending_stops(
            route_run
        ).count()

        ready_to_complete = remaining_stops == 0

        return remaining_stops, ready_to_complete

    # ============================================================
    # HEADING TO OFFICE
    # ============================================================

    @staticmethod
    def notify_heading_to_office(route_run):
        if route_run.trip_type != Trip.TRIP_TYPE_PICKUP:
            return

        if getattr(
            route_run,
            "heading_to_office_notification_sent",
            False,
        ):
            return

        driver_name = RouteService._driver_name(route_run)
        vehicle_number = RouteService._vehicle_number(route_run)

        picked_stops = route_run.stops.select_related(
            "employee",
        ).filter(
            is_picked=True,
        )

        for stop in picked_stops:
            NotificationService.send_notification(
                stop.employee,
                (
                    "All employee pickups are complete.\n"
                    "The cab is now heading to the office.\n"
                    f"Driver: {driver_name}\n"
                    f"Vehicle: {vehicle_number}"
                ),
                title="Heading to Office 🏢",
                push_data={
                    "type": "ALL_PICKUPS_COMPLETED",
                    "route_run_id": str(route_run.id),
                    "trip_type": route_run.trip_type,
                    "screen": "active_trip",
                },
            )

        NotificationService.notify_admins(
            (
                "All employee pickups are complete.\n"
                "The cab is now heading to the office.\n"
                f"Driver: {driver_name}\n"
                f"Vehicle: {vehicle_number}"
            ),
            title="Cab Heading to Office 🏢",
            notification_type=Notification.TYPE_INFO,
            priority=Notification.PRIORITY_MEDIUM,
            route_run=route_run,
            driver=route_run.driver,
            push_data={
                "type": "ALL_PICKUPS_COMPLETED",
                "route_run_id": str(route_run.id),
                "trip_type": route_run.trip_type,
                "screen": "admin_dashboard",
            },
        )

        if hasattr(
            route_run,
            "heading_to_office_notification_sent",
        ):
            route_run.heading_to_office_notification_sent = True
            route_run.save(
                update_fields=[
                    "heading_to_office_notification_sent"
                ],
            )

    # ============================================================
    # DRIVER ARRIVED
    # ============================================================

    @staticmethod
    def mark_arrived(route_run):
        current_stop = RouteService.get_current_stop(route_run)

        if not current_stop:
            return None

        if current_stop.arrival_time:
            return current_stop

        now = timezone.now()

        current_stop.arrival_time = now
        current_stop.waiting_started_at = now

        update_fields = [
            "arrival_time",
            "waiting_started_at",
        ]

        should_send = True

        if hasattr(
            current_stop,
            "arrival_notification_sent",
        ):
            should_send = (
                not current_stop.arrival_notification_sent
            )
            current_stop.arrival_notification_sent = True
            update_fields.append(
                "arrival_notification_sent",
            )

        current_stop.save(update_fields=update_fields)

        if should_send:
            route_word = (
                "drop"
                if route_run.trip_type == Trip.TRIP_TYPE_DROP
                else "pickup"
            )

            NotificationService.send_notification(
                current_stop.employee,
                (
                    f"Your cab has arrived at your "
                    f"{route_word} location.\n"
                    "Please come to the cab."
                ),
                title="Cab Arrived 🚕",
                push_data={
                    "type": "ARRIVED",
                    "route_run_id": str(route_run.id),
                    "stop_id": str(current_stop.id),
                    "trip_type": route_run.trip_type,
                    "chat_enabled": "true",
                    "screen": "pickup_chat",
                },
            )

        return current_stop

    # ============================================================
    # KEEP WAITING
    # ============================================================

    @staticmethod
    def keep_waiting(route_run):
        current_stop = RouteService.get_current_stop(route_run)

        if not current_stop:
            return None

        current_stop.waiting_started_at = timezone.now()

        update_fields = ["waiting_started_at"]

        if hasattr(current_stop, "keep_waiting_count"):
            current_stop.keep_waiting_count += 1
            update_fields.append("keep_waiting_count")

        if hasattr(current_stop, "waiting_minutes"):
            current_stop.waiting_minutes += 10
            update_fields.append("waiting_minutes")

        current_stop.save(update_fields=update_fields)

        route_word = (
            "drop"
            if route_run.trip_type == Trip.TRIP_TYPE_DROP
            else "pickup"
        )

        NotificationService.send_notification(
            current_stop.employee,
            (
                f"The driver is still waiting at your "
                f"{route_word} location.\n"
                "Please come to the cab as soon as possible."
            ),
            title="Driver Waiting ⏳",
            push_data={
                "type": "KEEP_WAITING",
                "route_run_id": str(route_run.id),
                "stop_id": str(current_stop.id),
                "trip_type": route_run.trip_type,
                "waiting_minutes": str(
                    getattr(
                        current_stop,
                        "waiting_minutes",
                        10,
                    )
                ),
                "screen": "pickup_chat",
            },
        )

        return current_stop

    # ============================================================
    # NO SHOW
    # ============================================================

    @staticmethod
    def mark_no_show(route_run, driver):
        current_stop = RouteService.get_current_stop(route_run)

        if not current_stop:
            return None, None, 0, False

        now = timezone.now()

        current_stop.is_no_show = True
        current_stop.no_show_at = now

        update_fields = ["is_no_show", "no_show_at"]

        should_notify_employee = True

        if hasattr(
            current_stop,
            "no_show_notification_sent",
        ):
            should_notify_employee = (
                not current_stop.no_show_notification_sent
            )
            current_stop.no_show_notification_sent = True
            update_fields.append(
                "no_show_notification_sent",
            )

        current_stop.save(update_fields=update_fields)

        if should_notify_employee:
            NotificationService.send_notification(
                current_stop.employee,
                (
                    "You have been marked as No Show "
                    "for today's pickup trip."
                ),
                title="Marked No Show",
                push_data={
                    "type": "NO_SHOW",
                    "route_run_id": str(route_run.id),
                    "stop_id": str(current_stop.id),
                    "trip_type": route_run.trip_type,
                    "screen": "trip_details",
                },
            )

        NotificationService.notify_admins(
            (
                f"{current_stop.employee.username} was "
                f"marked as No Show for route "
                f"#{route_run.id}."
            ),
            title="🚫 Employee No Show",
            notification_type=Notification.TYPE_NO_SHOW,
            priority=Notification.PRIORITY_HIGH,
            route_run=route_run,
            driver=driver,
            employee=current_stop.employee,
            push_data={
                "type": "NO_SHOW",
                "route_run_id": str(route_run.id),
                "employee_id": str(
                    current_stop.employee.id,
                ),
                "screen": "admin_dashboard",
            },
        )

        next_stop = RouteService.get_current_stop(route_run)

        if (
            route_run.trip_type == Trip.TRIP_TYPE_PICKUP
            and next_stop
        ):
            RouteService._notify_pending_pickup_positions(
                route_run,
            )

        remaining_stops, route_completed = (
            RouteService.complete_route_if_finished(
                route_run,
            )
        )

        if (
            route_run.trip_type == Trip.TRIP_TYPE_PICKUP
            and route_completed
        ):
            RouteService.notify_heading_to_office(
                route_run,
            )

        return (
            current_stop,
            next_stop,
            remaining_stops,
            route_completed,
        )

    # ============================================================
    # OFFICE DEADLINE / LATE MINUTES
    # Monday-Thursday: 5:30 PM
    # Friday: 7:00 PM
    # ============================================================

    @staticmethod
    def get_expected_office_arrival(route_run):
        run_date = route_run.run_date

        if run_date.weekday() == 4:
            expected_clock = time(19, 0)
        else:
            expected_clock = time(17, 30)

        naive_expected = datetime.combine(
            run_date,
            expected_clock,
        )

        return timezone.make_aware(
            naive_expected,
            timezone.get_current_timezone(),
        )

    @staticmethod
    def calculate_late_minutes(route_run, completed_at=None):
        completed_at = completed_at or route_run.completed_at

        if not completed_at:
            completed_at = timezone.now()

        expected_arrival = (
            RouteService.get_expected_office_arrival(
                route_run,
            )
        )

        completed_local = timezone.localtime(completed_at)

        return max(
            0,
            int(
                (
                    completed_local - expected_arrival
                ).total_seconds()
                // 60
            ),
        )

    # ============================================================
    # ROUTE COMPLETED NOTIFICATIONS
    # ============================================================

    @staticmethod
    def notify_route_completed(route_run):
        if getattr(
            route_run,
            "completion_notifications_sent",
            False,
        ):
            return

        trips = Trip.objects.select_related(
            "employee",
        ).filter(
            route_run=route_run,
            status=Trip.STATUS_COMPLETED,
        )

        driver_name = RouteService._driver_name(route_run)
        vehicle_number = RouteService._vehicle_number(route_run)

        # PICKUP:
        # Keep existing final Trip Completed / Review notification.
        #
        # DROP:
        # Do not send this again because each employee already receives
        # Drop Completed + Review when their individual drop is completed.
                # ------------------------------------------------------------
        # FINAL TRIP COMPLETION
        # Review becomes available ONLY after the complete route ends.
        # Works for both PICKUP and DROP.
        # ------------------------------------------------------------

        for trip in trips:

            if route_run.trip_type == Trip.TRIP_TYPE_PICKUP:
                message = (
                    "Today's pickup trip has been completed successfully.\n"
                    "Please submit your trip review."
                )
            else:
                message = (
                    "Today's drop trip has been completed successfully.\n"
                    "Please submit your trip review."
                )

            NotificationService.send_notification(
                trip.employee,
                message,
                title="Trip Completed ✅",
                push_data={
                    "type": "TRIP_COMPLETED",
                    "trip_id": str(trip.id),
                    "route_run_id": str(route_run.id),
                    "trip_type": route_run.trip_type,
                    "screen": "review",
                },
            )

        late_minutes = RouteService.calculate_late_minutes(
            route_run,
        )

        NotificationService.notify_admins(
            (
                f"{route_run.trip_type.capitalize()} trip "
                "completed successfully.\n"
                f"Driver: {driver_name}\n"
                f"Vehicle: {vehicle_number}\n"
                f"Cab late: {late_minutes} minutes"
            ),
            title="✅ Route Completed",
            notification_type=(
                Notification.TYPE_ROUTE_COMPLETED
            ),
            priority=Notification.PRIORITY_MEDIUM,
            route_run=route_run,
            driver=route_run.driver,
            push_data={
                "type": "ROUTE_COMPLETED",
                "route_run_id": str(route_run.id),
                "trip_type": route_run.trip_type,
                "late_minutes": str(late_minutes),
                "screen": "admin_dashboard",
            },
        )

        if hasattr(
            route_run,
            "completion_notifications_sent",
        ):
            route_run.completion_notifications_sent = True
            route_run.save(
                update_fields=[
                    "completion_notifications_sent"
                ],
            )