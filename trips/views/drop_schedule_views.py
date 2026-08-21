import hmac
import os

from django.utils import timezone

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status

from trips.models import RouteRun, Trip
from trips.services.drop_schedule_service import DropScheduleService


@api_view(["POST"])
@permission_classes([AllowAny])
def trigger_drop_schedule(request):

    # ---------------------------------------------------------
    # SECURITY CHECK
    # ---------------------------------------------------------
    expected_secret = os.environ.get(
        "DROP_CRON_SECRET",
        "",
    )

    provided_secret = request.headers.get(
        "X-Cron-Secret",
        "",
    )

    if (
        not expected_secret
        or not provided_secret
        or not hmac.compare_digest(
            expected_secret,
            provided_secret,
        )
    ):
        return Response(
            {
                "error": "Unauthorized"
            },
            status=status.HTTP_401_UNAUTHORIZED,
        )

    # ---------------------------------------------------------
    # GET ACTION FROM REQUEST
    # ---------------------------------------------------------
    action = request.data.get("action")

    # ---------------------------------------------------------
    # TEMPORARY DEBUG ACTION
    #
    # This lets us inspect the Render production database
    # because Render Free does not provide Shell access.
    # ---------------------------------------------------------
    if action == "debug":

        today = timezone.localdate()

        drop_trips = Trip.objects.filter(
            trip_date=today,
            trip_type=Trip.TRIP_TYPE_DROP,
        )

        drop_route_runs = RouteRun.objects.filter(
            run_date=today,
            trip_type=Trip.TRIP_TYPE_DROP,
        )

        active_drop_route_runs = (
            drop_route_runs.filter(
                completed_at__isnull=True,
            )
        )

        # -----------------------------------------------------
        # Detailed active RouteRun information
        # -----------------------------------------------------
        route_run_details = []

        for route_run in (
            active_drop_route_runs
            .select_related(
                "driver",
                "vehicle",
                "route_template",
            )
            .prefetch_related(
                "stops__employee",
            )
        ):

            stops = route_run.stops.all()

            route_run_details.append(
                {
                    "route_run_id": route_run.id,

                    "run_date": str(
                        route_run.run_date
                    ),

                    "started_at": (
                        str(route_run.started_at)
                        if route_run.started_at
                        else None
                    ),

                    "completed_at": (
                        str(route_run.completed_at)
                        if route_run.completed_at
                        else None
                    ),

                    "driver": (
                        route_run.driver.username
                        if route_run.driver
                        else None
                    ),

                    "vehicle": (
                        route_run.vehicle.vehicle_number
                        if route_run.vehicle
                        else None
                    ),

                    "total_stops": stops.count(),

                    "boarded": stops.filter(
                        is_boarded=True,
                        is_no_show=False,
                    ).count(),

                    "waiting": stops.filter(
                        is_boarded=False,
                        is_no_show=False,
                    ).count(),

                    "no_show": stops.filter(
                        is_no_show=True,
                    ).count(),

                    "cab_ready_sent": (
                        route_run
                        .drop_cab_ready_notification_sent
                    ),

                    "waiting_notification_sent": (
                        route_run
                        .drop_waiting_notification_sent
                    ),
                }
            )

        return Response(
            {
                "success": True,
                "action": "debug",
                "today": str(today),

                "drop_trips": drop_trips.count(),

                "drop_route_runs": (
                    drop_route_runs.count()
                ),

                "active_drop_route_runs": (
                    active_drop_route_runs.count()
                ),

                "route_runs": route_run_details,
            },
            status=status.HTTP_200_OK,
        )

    # ---------------------------------------------------------
    # CAB READY FCM
    # ---------------------------------------------------------
    elif action == "cab_ready":

        sent = (
            DropScheduleService
            .send_cab_ready()
        )

    # ---------------------------------------------------------
    # WAITING FCM
    # ---------------------------------------------------------
    elif action == "waiting":

        sent = (
            DropScheduleService
            .send_waiting()
        )

    # ---------------------------------------------------------
    # INVALID ACTION
    # ---------------------------------------------------------
    else:

        return Response(
            {
                "error": (
                    "Invalid action. "
                    "Use cab_ready, waiting, or debug."
                )
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    # ---------------------------------------------------------
    # NORMAL SUCCESS RESPONSE
    # ---------------------------------------------------------
    return Response(
        {
            "success": True,
            "action": action,
            "notifications_sent": sent,
        },
        status=status.HTTP_200_OK,
    )