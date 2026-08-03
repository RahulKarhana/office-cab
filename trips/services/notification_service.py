from django.contrib.auth import get_user_model

from trips.models import Notification
from trips.utils.notification import send_push_notification
from trips.utils.smart_notifications import create_smart_notification

User = get_user_model()


class NotificationService:
    @staticmethod
    def send_notification(user, message, title="Trip Update", push_data=None):
        if not user:
            return None

        notification = Notification.objects.create(
            user=user,
            title=title,
            message=message,
        )

        try:
            send_push_notification(
                user=user,
                title=title,
                body=message,
                data=push_data or {},
            )
        except Exception as e:
            print("FCM ERROR:", e)

        return notification

    @staticmethod
    def notify_admins(
        message,
        title="Admin Alert",
        push_data=None,
        notification_type=Notification.TYPE_INFO,
        priority=Notification.PRIORITY_LOW,
        trip=None,
        route_run=None,
        driver=None,
        employee=None,
    ):
        admins = User.objects.filter(role="ADMIN", is_active=True)
        created = []

        for admin in admins:
            notification = create_smart_notification(
                user=admin,
                title=title,
                message=message,
                notification_type=notification_type,
                priority=priority,
                trip=trip,
                route_run=route_run,
                driver=driver,
                employee=employee,
                push_data=push_data or {},
            )
            created.append(notification)

        return created
