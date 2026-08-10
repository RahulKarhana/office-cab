import os
import firebase_admin

from django.conf import settings
from firebase_admin import credentials, messaging

from trips.models import DeviceToken


def initialize_firebase():
    # Already initialized by settings.py
    if firebase_admin._apps:
        print("✅ Firebase already initialized")
        return True

    service_account_path = os.environ.get(
        "FIREBASE_CREDENTIALS_PATH",
        os.path.join(
            settings.BASE_DIR,
            "config",
            "firebase_key.json",
        ),
    )

    print("🔥 Firebase path:", service_account_path)

    if not os.path.exists(service_account_path):
        print("❌ Firebase key not found:", service_account_path)
        return False

    try:
        cred = credentials.Certificate(service_account_path)
        firebase_admin.initialize_app(cred)

        print("✅ Firebase initialized from notification.py")
        return True

    except Exception as e:
        print("❌ Firebase initialization error:", e)
        return False


def send_push_notification(user, title, body, data=None):
    print("🔥 PUSH FUNCTION CALLED FOR:", user.username)

    if not initialize_firebase():
        print("❌ Push cancelled because Firebase is unavailable")
        return

    tokens = list(
        DeviceToken.objects.filter(
            user=user,
            is_active=True,
        ).values_list("token", flat=True)
    )

    print("📱 TOKENS FOUND:", len(tokens))

    if not tokens:
        print("❌ No active FCM tokens for:", user.username)
        return

    try:
        message = messaging.MulticastMessage(
            notification=messaging.Notification(
                title=title,
                body=body,
            ),
            data={
                str(k): str(v)
                for k, v in (data or {}).items()
            },
            tokens=tokens,
        )

        response = messaging.send_each_for_multicast(message)

        print(
            "✅ FCM RESULT - success:",
            response.success_count,
            "failed:",
            response.failure_count,
        )

        # Show individual Firebase errors
        for index, result in enumerate(response.responses):
            if not result.success:
                print(
                    "❌ FCM token failed:",
                    index,
                    result.exception,
                )

        return response

    except Exception as e:
        print("❌ Firebase send error:", e)
        return None