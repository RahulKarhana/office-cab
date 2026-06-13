from django.core.management.base import BaseCommand
from django.utils import timezone
from trips.models import Trip
from trips.utils.notification import send_push_notification


class Command(BaseCommand):
    help = "Send 10AM cab assignment notifications"

    def handle(self, *args, **kwargs):
        today = timezone.localdate()

        trips = Trip.objects.filter(
            trip_date=today,
            status="ASSIGNED"
        ).select_related("employee")

        sent_users = set()

        for trip in trips:
            user = trip.employee

            if user.id in sent_users:
                continue

            send_push_notification(
                user,
                "Cab Assigned",
                "Your cab for today is assigned",
                {"type": "DAILY_ASSIGNMENT"}
            )

            sent_users.add(user.id)

        self.stdout.write(self.style.SUCCESS("10AM notifications sent"))