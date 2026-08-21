import hmac
import os

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status

from trips.services.drop_schedule_service import DropScheduleService


@api_view(["POST"])
@permission_classes([AllowAny])
def trigger_drop_schedule(request):
    expected_secret = os.environ.get("DROP_CRON_SECRET", "")
    provided_secret = request.headers.get("X-Cron-Secret", "")

    if (
        not expected_secret
        or not provided_secret
        or not hmac.compare_digest(
            expected_secret,
            provided_secret,
        )
    ):
        return Response(
            {"error": "Unauthorized"},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    action = request.data.get("action")

    if action == "cab_ready":
        sent = DropScheduleService.send_cab_ready()

    elif action == "waiting":
        sent = DropScheduleService.send_waiting()

    else:
        return Response(
            {
                "error": (
                    "Invalid action. "
                    "Use cab_ready or waiting."
                )
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    return Response(
        {
            "success": True,
            "action": action,
            "notifications_sent": sent,
        },
        status=status.HTTP_200_OK,
    )