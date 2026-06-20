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
        "firebase_key.json",
    )

    if not os.path.exists(service_account_path):
        print(f"Firebase key not found: {service_account_path}")
        return

    cred = credentials.Certificate(service_account_path)
    firebase_admin.initialize_app(cred)

def send_push_notification(user, title, body, data=None):
    print("🔥 PUSH FUNCTION CALLED FOR:", user.username)

    initialize_firebase()

    tokens = list(
        DeviceToken.objects.filter(user=user, is_active=True)
        .values_list("token", flat=True)
    )

    print("📱 TOKENS FOUND:", len(tokens))

    if not tokens:
        print("❌ No FCM tokens for user", user.username)
        return

    try:
        message = messaging.MulticastMessage(
            notification=messaging.Notification(title=title, body=body),
            data={str(k): str(v) for k, v in (data or {}).items()},
            tokens=tokens,
        )
        response = messaging.send_each_for_multicast(message)
        print("✅ FCM sent:", response.success_count, "failed:", response.failure_count)
    except Exception as e:
        print("❌ Firebase send error:", e)
    