from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from trips.models import RouteRunStop
from trips.utils.notification import send_push_notification


class Command(BaseCommand):
    help = "Auto-send delay warning if driver is waiting more than 10 minutes."

    def handle(self, *args, **options):
        now = timezone.now()
        delay_time = now - timedelta(minutes=10)

        delayed_stops = RouteRunStop.objects.select_related(
            "employee",
            "route_run",
        ).filter(
            route_run__started_at__isnull=False,
            route_run__completed_at__isnull=True,
            waiting_started_at__isnull=False,
            waiting_started_at__lte=delay_time,
            is_picked=False,
            is_no_show=False,
            delay_warning_sent=False,
        )

        count = 0

        for stop in delayed_stops:
            route_word = (
                "drop"
                if stop.route_run.trip_type == "DROP"
                else "pickup"
            )

            send_push_notification(
                user=stop.employee,
                title="⚠️ Cab Waiting",
                body=f"Your driver has been waiting for more than 10 minutes for your {route_word}. Please respond urgently.",
                data={
                    "type": "WAITING_DELAY",
                    "route_run_id": str(stop.route_run.id),
                    "stop_id": str(stop.id),
                    "trip_type": stop.route_run.trip_type,
                    "screen": "active_trip",
                },
            )

            stop.delay_warning_sent = True
            stop.save(update_fields=["delay_warning_sent"])

            count += 1

        self.stdout.write(
            self.style.SUCCESS(f"Delay warnings sent: {count}")
        )