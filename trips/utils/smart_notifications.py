from trips.models import Notification
from trips.utils.notification import send_push_notification


def create_smart_notification(
    *,
    user,
    title,
    message,
    notification_type=Notification.TYPE_INFO,
    priority=Notification.PRIORITY_LOW,
    trip=None,
    route_run=None,
    driver=None,
    employee=None,
    push_data=None,
):
    notification = Notification.objects.create(
        user=user,
        title=title,
        message=message,
        notification_type=notification_type,
        priority=priority,
        trip=trip,
        route_run=route_run,
        driver=driver,
        employee=employee,
    )

    try:
        send_push_notification(
            user=user,
            title=title,
            body=message,
            data=push_data or {},
        )
    except Exception as e:
        print("SMART NOTIFICATION FCM ERROR:", e)

    return notification