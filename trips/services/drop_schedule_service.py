
from datetime import timedelta
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone

from trips.models import RouteRun, Trip
from trips.services.notification_service import NotificationService


class DropScheduleService:

    INDIA_TZ = ZoneInfo("Asia/Kolkata")

    @staticmethod
    def get_shift_date(now=None):
        now = now or timezone.now()
        local_now = timezone.localtime(
            now, DropScheduleService.INDIA_TZ
        )
        return local_now.date() - timedelta(days=1)

    @staticmethod
    def get_today_drop_runs(now=None):
        shift_date = DropScheduleService.get_shift_date(now)

        return (
            RouteRun.objects
            .select_related(
                "driver",
                "vehicle",
                "route_template",
            )
            .prefetch_related("stops__employee")
            .filter(
                run_date=shift_date,
                trip_type=Trip.TRIP_TYPE_DROP,
                completed_at__isnull=True,
            )
        )

    @staticmethod
    def is_scheduled_time(action, now=None):
        now = now or timezone.now()
        local_now = timezone.localtime(
            now, DropScheduleService.INDIA_TZ
        )

        shift_date = local_now.date() - timedelta(days=1)
        weekday = shift_date.weekday()

        if weekday > 4:
            return False

        if weekday == 4:
            schedule = {
                "cab_ready": (4, 0),
                "waiting": (4, 30),
            }
        else:
            schedule = {
                "cab_ready": (2, 30),
                "waiting": (3, 0),
            }

        expected = schedule.get(action)

        return expected == (
            local_now.hour,
            local_now.minute,
        )

    @staticmethod
    def send_cab_ready(now=None):
        if not DropScheduleService.is_scheduled_time(
            "cab_ready", now
        ):
            return 0

        sent = 0

        for route_run in DropScheduleService.get_today_drop_runs(now):
            with transaction.atomic():
                locked_run = RouteRun.objects.select_for_update().get(
                    pk=route_run.pk
                )

                if locked_run.drop_cab_ready_notification_sent:
                    continue

                vehicle_number = (
                    route_run.vehicle.vehicle_number
                    if route_run.vehicle
                    else "your assigned cab"
                )

                stops = (
                    route_run.stops
                    .select_related("employee")
                    .filter(is_no_show=False)
                )

                for stop in stops:
                    if not stop.employee:
                        continue

                    NotificationService.send_notification(
                        stop.employee,
                        (
                            f"Your cab {vehicle_number} is ready. "
                            "Please reach the cab on time."
                        ),
                        title="Your Drop Cab Is Ready 🚕",
                        push_data={
                            "type": "DROP_CAB_READY",
                            "route_run_id": str(route_run.id),
                            "stop_id": str(stop.id),
                            "trip_type": "DROP",
                            "vehicle_number": str(vehicle_number),
                            "screen": "drop_route",
                        },
                    )
                    sent += 1

                locked_run.drop_cab_ready_notification_sent = True
                locked_run.save(
                    update_fields=[
                        "drop_cab_ready_notification_sent"
                    ]
                )

        return sent

    @staticmethod
    def send_waiting(now=None):
        if not DropScheduleService.is_scheduled_time(
            "waiting", now
        ):
            return 0

        sent = 0

        for route_run in DropScheduleService.get_today_drop_runs(now):
            with transaction.atomic():
                locked_run = RouteRun.objects.select_for_update().get(
                    pk=route_run.pk
                )

                if locked_run.drop_waiting_notification_sent:
                    continue

                pending_stops = (
                    route_run.stops
                    .select_related("employee")
                    .filter(
                        is_boarded=False,
                        is_no_show=False,
                    )
                )

                for stop in pending_stops:
                    if not stop.employee:
                        continue

                    NotificationService.send_notification(
                        stop.employee,
                        (
                            "Everyone is waiting for you. "
                            "Please reach the cab as soon as possible."
                        ),
                        title="Drop Cab Waiting ⏳",
                        push_data={
                            "type": "DROP_WAITING_STARTED",
                            "route_run_id": str(route_run.id),
                            "stop_id": str(stop.id),
                            "trip_type": "DROP",
                            "screen": "drop_route",
                        },
                    )
                    sent += 1

                locked_run.drop_waiting_notification_sent = True
                locked_run.save(
                    update_fields=[
                        "drop_waiting_notification_sent"
                    ]
                )

        return sent
