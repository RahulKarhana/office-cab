from django.core.management.base import BaseCommand
from django.utils import timezone
from zoneinfo import ZoneInfo
from trips.models import RouteRun, Trip
from trips.services.notification_service import NotificationService


class Command(BaseCommand):
    help = "Send scheduled Drop cab-ready and waiting FCM notifications."

    def handle(self, *args, **options):
        india_timezone = ZoneInfo("Asia/Kolkata")
        now = timezone.now().astimezone(india_timezone)
        weekday = now.weekday()

        # Python weekday:
        # Monday = 0
        # Tuesday = 1
        # Wednesday = 2
        # Thursday = 3
        # Friday = 4
        # Saturday = 5
        # Sunday = 6

        self.stdout.write(
            f"Checking Drop notifications at "
            f"{now.strftime('%Y-%m-%d %H:%M:%S')}"
        )

        # ---------------------------------------------------------
        # SATURDAY / SUNDAY
        # ---------------------------------------------------------
        if weekday >= 5:
            self.stdout.write(
                "Weekend detected. No Drop waiting notifications."
            )
            return

        current_hour = now.hour
        current_minute = now.minute

        # ---------------------------------------------------------
        # MONDAY - THURSDAY
        # ---------------------------------------------------------
        if weekday in [0, 1, 2, 3]:

            # 2:30 AM
            if current_hour == 2 and current_minute == 30:
                self.send_cab_ready_notifications(now)
                return

            # 3:00 AM
            if current_hour == 3 and current_minute == 0:
                self.send_waiting_notifications(now)
                return

        # ---------------------------------------------------------
        # FRIDAY
        # ---------------------------------------------------------
        elif weekday == 4:

            # 4:00 AM
            if current_hour == 4 and current_minute == 0:
                self.send_cab_ready_notifications(now)
                return

            # 4:30 AM
            if current_hour == 4 and current_minute == 30:
                self.send_waiting_notifications(now)
                return

        self.stdout.write(
            "No Drop notification is scheduled for this time."
        )

    # =============================================================
    # FIND TODAY'S DROP ROUTES
    # =============================================================

    def get_today_drop_runs(self, now):
        today = now.date()

        return (
            RouteRun.objects
            .select_related(
                "driver",
                "vehicle",
                "route_template",
            )
            .prefetch_related(
                "stops__employee"
            )
            .filter(
                run_date=today,
                trip_type=Trip.TRIP_TYPE_DROP,
                completed_at__isnull=True,
            )
        )
    def send_cab_ready_notifications(self, now):

        route_runs = self.get_today_drop_runs(now)

        route_count = 0
        notification_count = 0

        for route_run in route_runs:
            route_count += 1

            vehicle_number = (
                route_run.vehicle.vehicle_number
                if route_run.vehicle
                else "your assigned cab"
            )

            stops = (
                route_run.stops
                .select_related("employee")
                .filter(is_no_show=False)
                .order_by("stop_order")
            )

            for stop in stops:

                if not stop.employee:
                    continue

                message = (
                    f"Your cab {vehicle_number} is ready. "
                    "Please reach the cab on time."
                )

                NotificationService.send_notification(
                    stop.employee,
                    message,
                    title="Your Drop Cab Is Ready 🚕",
                    push_data={
                        "type": "DROP_CAB_READY",
                        "route_run_id": str(route_run.id),
                        "stop_id": str(stop.id),
                        "trip_type": route_run.trip_type,
                        "vehicle_number": str(vehicle_number),
                        "screen": "drop_route",
                    },
                )

                notification_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Cab-ready FCM completed. "
                f"Routes: {route_count}, "
                f"Notifications: {notification_count}"
            )
        )

    # =============================================================
    # WAITING STARTED
    # Mon-Thu = 3:00 AM
    # Friday   = 4:30 AM
    # =============================================================

    def send_waiting_notifications(self, now):

        route_runs = self.get_today_drop_runs(now)

        route_count = 0
        notification_count = 0

        for route_run in route_runs:
            route_count += 1

            vehicle_number = (
                route_run.vehicle.vehicle_number
                if route_run.vehicle
                else "your assigned cab"
            )

            # IMPORTANT:
            # Only employees who have NOT boarded and have NOT
            # already been marked No Show should receive this FCM.
            pending_stops = (
                route_run.stops
                .select_related("employee")
                .filter(
                    is_boarded=False,
                    is_no_show=False,
                )
                .order_by("stop_order")
            )

            for stop in pending_stops:

                if not stop.employee:
                    continue

                message = (
                    "Everyone is waiting for you. "
                    "Please reach the cab as soon as possible."
                )

                NotificationService.send_notification(
                    stop.employee,
                    message,
                    title="Drop Cab Waiting ⏳",
                    push_data={
                        "type": "DROP_WAITING_STARTED",
                        "route_run_id": str(route_run.id),
                        "stop_id": str(stop.id),
                        "trip_type": route_run.trip_type,
                        "vehicle_number": str(vehicle_number),
                        "screen": "drop_route",
                    },
                )

                notification_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Waiting FCM completed. "
                f"Routes: {route_count}, "
                f"Notifications: {notification_count}"
            )
        )