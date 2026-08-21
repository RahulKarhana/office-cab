from django.utils import timezone

from trips.models import RouteRun, Trip
from trips.services.notification_service import NotificationService


class DropScheduleService:

    @staticmethod
    def get_today_drop_runs():
        today = timezone.localdate()

        return (
            RouteRun.objects
            .select_related(
                "driver",
                "vehicle",
                "route_template",
            )
            .prefetch_related(
                "stops__employee",
            )
            .filter(
                run_date=today,
                trip_type=Trip.TRIP_TYPE_DROP,
                completed_at__isnull=True,
            )
        )

    @staticmethod
    def send_cab_ready():
        sent = 0

        for route_run in DropScheduleService.get_today_drop_runs():

            if route_run.drop_cab_ready_notification_sent:
                continue

            vehicle_number = (
                route_run.vehicle.vehicle_number
                if route_run.vehicle
                else "your assigned cab"
            )

            stops = (
                route_run.stops
                .select_related("employee")
                .filter(
                    is_no_show=False,
                )
            )

            for stop in stops:
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

            route_run.drop_cab_ready_notification_sent = True
            route_run.save(
                update_fields=[
                    "drop_cab_ready_notification_sent"
                ]
            )

        return sent

    @staticmethod
    def send_waiting():
        sent = 0

        for route_run in DropScheduleService.get_today_drop_runs():

            if route_run.drop_waiting_notification_sent:
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

            route_run.drop_waiting_notification_sent = True
            route_run.save(
                update_fields=[
                    "drop_waiting_notification_sent"
                ]
            )

        return sent