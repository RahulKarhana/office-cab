import hmac
import os

from django.contrib.auth import get_user_model

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status


User = get_user_model()


@api_view(["POST"])
@permission_classes([AllowAny])
def reset_admin_password(request):
    expected_secret = os.environ.get("ADMIN_RESET_SECRET", "")
    provided_secret = request.headers.get("X-Admin-Reset-Secret", "")

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

    new_password = request.data.get("new_password")

    if not new_password or len(new_password) < 8:
        return Response(
            {"error": "Password must contain at least 8 characters."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    username = request.data.get("username")

    if not username:
        superusers = list(
            User.objects.filter(
                is_superuser=True
            ).values_list(
                "username",
                flat=True,
            )
        )

        return Response(
            {
                "error": "Username required.",
                "superusers": superusers,
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        admin = User.objects.get(
            username=username,
            is_superuser=True,
        )
    except User.DoesNotExist:
        return Response(
            {
                "error": (
                    f"Superuser '{username}' "
                    "was not found."
                )
            },
            status=status.HTTP_404_NOT_FOUND,
        )

    admin.set_password(new_password)
    admin.save(update_fields=["password"])

    return Response(
        {
            "success": True,
            "message": "Production admin password changed successfully.",
        },
        status=status.HTTP_200_OK,
    )