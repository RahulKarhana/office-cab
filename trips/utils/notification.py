import os
import firebase_admin

from django.conf import settings
from firebase_admin import credentials, messaging

from trips.models import DeviceToken, Notification


def initialize_firebase():
    if firebase_admin._apps:
        return

    service_account_path = os.path.join(
        settings.BASE_DIR,
        "config",
        "firebase-service-account.json",
    )

    cred = credentials.Certificate(service_account_path)
    firebase_admin.initialize_app(cred)


def send_push_notification(user, title, body, data=None):
    initialize_firebase()

    data = data or {}
    data = {str(k): str(v) for k, v in data.items()}

    tokens = list(
        DeviceToken.objects.filter(user=user, is_active=True)
        .values_list("token", flat=True)
    )

    if not tokens:
        print(f"No FCM tokens for user {user}")

        # Save in DB even if no device
        Notification.objects.create(
            user=user,
            title=title,
            message=body,
        )
        return

    message = messaging.MulticastMessage(
        notification=messaging.Notification(
            title=title,
            body=body,
        ),
        tokens=tokens,
        data=data,
    )

    try:
        response = messaging.send_multicast(message)

        print(
            f"Push sent: {response.success_count}, failed: {response.failure_count}"
        )

        # Handle failed tokens
        for idx, resp in enumerate(response.responses):
            if not resp.success:
                failed_token = tokens[idx]
                DeviceToken.objects.filter(token=failed_token).update(
                    is_active=False
                )

    except Exception as e:
        print(f"FCM ERROR: {e}")

    