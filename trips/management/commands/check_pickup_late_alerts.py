from datetime import datetime, time, timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from trips.models import (
    Notification,
    RouteRun,
    Trip,
)
from trips.services.notification_service import NotificationService


class Command(BaseCommand):
    help = (
        "Send admin alerts when active pickup routes "
        "miss the office arrival deadline."
    )

    def handle(self, *args, **options):
        now = timezone.localtime()
        today = timezone.localdate()

        active_pickup_routes = (
            RouteRun.objects.select_related(
                "driver",
                "vehicle",
                "route_template",
            )
            .filter(
                run_date=today,
                trip_type=Trip.TRIP_TYPE_PICKUP,
                started_at__isnull=False,
                completed_at__isnull=True,
            )
        )

        deadline_alerts = 0
        late_alerts = 0

        for route_run in active_pickup_routes:
            expected_arrival = self._get_expected_arrival(
                route_run,
            )

            fifteen_minutes_late = (
                expected_arrival + timedelta(minutes=15)
            )

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

            if (
                now >= expected_arrival
                and not route_run.office_deadline_alert_sent
            ):
                NotificationService.notify_admins(
                    (
                        "Pickup cab has reached the office "
                        "deadline and the route is still active.\n"
                        f"Driver: {driver_name}\n"
                        f"Vehicle: {vehicle_number}\n"
                        f"Route: {route_name}\n"
                        "Cab late: 0 minutes"
                    ),
                    title="Pickup Office Deadline Reached",
                    notification_type=(
                        Notification.TYPE_ROUTE_DELAY
                    ),
                    priority=Notification.PRIORITY_HIGH,
                    route_run=route_run,
                    driver=route_run.driver,
                    push_data={
                        "type": "PICKUP_OFFICE_DEADLINE",
                        "route_run_id": str(route_run.id),
                        "trip_type": route_run.trip_type,
                        "late_minutes": "0",
                        "screen": "admin_dashboard",
                    },
                )

                route_run.office_deadline_alert_sent = True
                route_run.save(
                    update_fields=[
                        "office_deadline_alert_sent",
                    ],
                )

                deadline_alerts += 1

            if (
                now >= fifteen_minutes_late
                and not route_run.office_15_min_late_alert_sent
            ):
                late_minutes = max(
                    15,
                    int(
                        (
                            now - expected_arrival
                        ).total_seconds()
                        // 60
                    ),
                )

                NotificationService.notify_admins(
                    (
                        "Pickup cab is late reaching the office.\n"
                        f"Driver: {driver_name}\n"
                        f"Vehicle: {vehicle_number}\n"
                        f"Route: {route_name}\n"
                        f"Cab late: {late_minutes} minutes\n"
                        "Route is still in progress."
                    ),
                    title="Pickup Cab Late",
                    notification_type=(
                        Notification.TYPE_ROUTE_DELAY
                    ),
                    priority=(
                        Notification.PRIORITY_CRITICAL
                    ),
                    route_run=route_run,
                    driver=route_run.driver,
                    push_data={
                        "type": "PICKUP_OFFICE_LATE",
                        "route_run_id": str(route_run.id),
                        "trip_type": route_run.trip_type,
                        "late_minutes": str(late_minutes),
                        "screen": "admin_dashboard",
                    },
                )

                route_run.office_15_min_late_alert_sent = True
                route_run.save(
                    update_fields=[
                        "office_15_min_late_alert_sent",
                    ],
                )

                late_alerts += 1

        self.stdout.write(
            self.style.SUCCESS(
                (
                    "Pickup deadline alerts sent: "
                    f"{deadline_alerts}; "
                    "15-minute late alerts sent: "
                    f"{late_alerts}"
                )
            )
        )

    def _get_expected_arrival(self, route_run):
        if route_run.run_date.weekday() == 4:
            expected_clock = time(19, 0)
        else:
            expected_clock = time(17, 30)

        expected_datetime = datetime.combine(
            route_run.run_date,
            expected_clock,
        )

        return timezone.make_aware(
            expected_datetime,
            timezone.get_current_timezone(),
        )