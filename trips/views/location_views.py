from math import asin, cos, radians, sin, sqrt

from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from trips.models import (
    DriverLocation,
    DriverLocationHistory,
    Notification,
    RouteRun,
    RouteRunStop,
    Trip,
)
from trips.serializers import DriverLocationSerializer
from trips.services.notification_service import NotificationService
from trips.services.route_service import RouteService


# =========================
# DISTANCE CALCULATOR
# =========================

def calculate_distance_km(lat1, lon1, lat2, lon2):
    lon1, lat1, lon2, lat2 = map(
        float,
        [lon1, lat1, lon2, lat2],
    )

    lon1, lat1, lon2, lat2 = map(
        radians,
        [lon1, lat1, lon2, lat2],
    )

    dlon = lon2 - lon1
    dlat = lat2 - lat1

    a = (
        sin(dlat / 2) ** 2
        + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    )

    c = 2 * asin(sqrt(a))
    earth_radius_km = 6371

    return c * earth_radius_km


# =========================
# SHOULD TRACK DRIVER SPEED
# =========================

def should_track_driver_speed(driver):
    route_run = (
        RouteRun.objects.filter(
            driver=driver,
            started_at__isnull=False,
            completed_at__isnull=True,
        )
        .order_by("-started_at")
        .first()
    )

    if not route_run:
        return False, None

    # =========================
    # PICKUP
    # Track only after first employee picked
    # =========================

    if route_run.trip_type == Trip.TRIP_TYPE_PICKUP:
        first_picked = RouteRunStop.objects.filter(
            route_run=route_run,
            is_picked=True,
        ).exists()

        return first_picked, route_run

    # =========================
    # DROP
    # Track until last employee dropped
    # =========================

    if route_run.trip_type == Trip.TRIP_TYPE_DROP:
        pending_drop_exists = RouteRunStop.objects.filter(
            route_run=route_run,
            is_picked=False,
            is_no_show=False,
        ).exists()

        return pending_drop_exists, route_run

    return False, route_run


# =========================
# LIVE DRIVER STATUS ENGINE
# =========================

def get_live_driver_status(driver, route_run=None):
    latest_location = (
        DriverLocation.objects.filter(driver=driver)
        .order_by("-updated_at")
        .first()
    )

    if not latest_location:
        return {
            "status": "OFFLINE",
            "label": "Offline",
            "color": "red",
        }

    minutes_since_update = (
        timezone.now() - latest_location.updated_at
    ).total_seconds() / 60

    if minutes_since_update > 10:
        return {
            "status": "OFFLINE",
            "label": "Offline",
            "color": "red",
        }

    recent_speed = (
        DriverLocationHistory.objects.filter(driver=driver)
        .order_by("-recorded_at")
        .first()
    )

    if recent_speed and recent_speed.speed_kmph > 80:
        return {
            "status": "OVERSPEED",
            "label": "Overspeed",
            "color": "danger",
        }

    if route_run and route_run.completed_at:
        return {
            "status": "COMPLETED",
            "label": "Completed",
            "color": "green",
        }

    if recent_speed and recent_speed.speed_kmph <= 5:
        return {
            "status": "IDLE",
            "label": "Idle",
            "color": "yellow",
        }

    return {
        "status": "MOVING",
        "label": "Moving",
        "color": "green",
    }


# =========================
# PICKUP PROXIMITY ALERTS
# =========================

def send_pickup_proximity_alerts(
    *,
    route_run,
    current_stop,
    distance_km,
    eta_minutes=None,
):
    if route_run.trip_type != Trip.TRIP_TYPE_PICKUP:
        return

    if current_stop.is_picked or current_stop.is_no_show:
        return

    update_fields = []

    # =========================
    # 5-MINUTE ALERT
    # =========================

    if (
        eta_minutes is not None
        and eta_minutes <= 5
        and not current_stop.five_min_alert_sent
    ):
        NotificationService.send_notification(
            current_stop.employee,
            (
                "Your driver is coming.\n"
                f"Estimated arrival: {eta_minutes} minutes.\n"
                "Please be ready at your pickup location."
            ),
            title="Driver Arriving Soon 🚕",
            push_data={
                "type": "PICKUP_5_MINUTES",
                "route_run_id": str(route_run.id),
                "stop_id": str(current_stop.id),
                "eta_minutes": str(eta_minutes),
                "screen": "active_trip",
            },
        )

        current_stop.five_min_alert_sent = True
        update_fields.append("five_min_alert_sent")

    # =========================
    # 1-KM ALERT
    # =========================

    if (
        distance_km <= 1
        and not current_stop.one_km_alert_sent
    ):
        NotificationService.send_notification(
            current_stop.employee,
            "Your cab is approximately 1 km away.",
            title="Cab 1 km Away 🚕",
            push_data={
                "type": "PICKUP_1_KM",
                "route_run_id": str(route_run.id),
                "stop_id": str(current_stop.id),
                "distance_km": str(round(distance_km, 2)),
                "screen": "active_trip",
            },
        )

        current_stop.one_km_alert_sent = True
        update_fields.append("one_km_alert_sent")

    # =========================
    # 500-METRE ALERT
    # =========================

    if (
        distance_km <= 0.5
        and not current_stop.five_hundred_meter_alert_sent
    ):
        NotificationService.send_notification(
            current_stop.employee,
            "Your cab is approximately 500 metres away.",
            title="Cab 500 m Away 🚕",
            push_data={
                "type": "PICKUP_500_METERS",
                "route_run_id": str(route_run.id),
                "stop_id": str(current_stop.id),
                "distance_km": str(round(distance_km, 2)),
                "screen": "active_trip",
            },
        )

        current_stop.five_hundred_meter_alert_sent = True
        update_fields.append(
            "five_hundred_meter_alert_sent",
        )

    # =========================
    # 100-METRE ALERT
    # =========================

    if (
        distance_km <= 0.1
        and not current_stop.hundred_meter_alert_sent
    ):
        NotificationService.send_notification(
            current_stop.employee,
            (
                "Your cab is approximately 100 metres away.\n"
                "Please come to your pickup point."
            ),
            title="Cab 100 m Away 🚕",
            push_data={
                "type": "PICKUP_100_METERS",
                "route_run_id": str(route_run.id),
                "stop_id": str(current_stop.id),
                "distance_km": str(round(distance_km, 3)),
                "screen": "active_trip",
            },
        )

        current_stop.hundred_meter_alert_sent = True
        update_fields.append("hundred_meter_alert_sent")

    if update_fields:
        current_stop.save(update_fields=update_fields)


# =========================
# DRIVER LOCATION VIEWSET
# =========================

class DriverLocationViewSet(viewsets.ModelViewSet):
    serializer_class = DriverLocationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user

        if user.role == "ADMIN":
            return DriverLocation.objects.select_related(
                "driver",
            ).all()

        if user.role == "DRIVER":
            return DriverLocation.objects.select_related(
                "driver",
            ).filter(
                driver=user,
            )

        if user.role == "EMPLOYEE":
            active_trip = (
                Trip.objects.select_related(
                    "driver",
                    "vehicle",
                    "route_run",
                )
                .filter(
                    employee=user,
                    status=Trip.STATUS_STARTED,
                )
                .order_by("-created_at")
                .first()
            )

            if active_trip:
                return DriverLocation.objects.select_related(
                    "driver",
                ).filter(
                    driver=active_trip.driver,
                )

            return DriverLocation.objects.none()

        return DriverLocation.objects.none()

    # =========================
    # UPDATE DRIVER LOCATION
    # =========================

    @action(detail=False, methods=["post"])
    def update_my_location(self, request):
        user = request.user

        if user.role != "DRIVER":
            raise PermissionDenied(
                "Only driver can update location.",
            )

        started_trip = (
            Trip.objects.filter(
                driver=user,
                status=Trip.STATUS_STARTED,
            )
            .select_related("route_run")
            .first()
        )

        active_route_run = (
            RouteRun.objects.filter(
                driver=user,
                started_at__isnull=False,
                completed_at__isnull=True,
            )
            .prefetch_related("stops__employee")
            .order_by("-created_at")
            .first()
        )

        if not started_trip and not active_route_run:
            return Response(
                {
                    "error": (
                        "You can update location "
                        "only during active trip"
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        latitude = request.data.get("latitude")
        longitude = request.data.get("longitude")
        speed_value = request.data.get("speed", 0)

        if latitude is None or longitude is None:
            return Response(
                {
                    "error": (
                        "Latitude and longitude required"
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            latitude = float(latitude)
            longitude = float(longitude)
            speed_kmph = float(speed_value or 0)
        except (TypeError, ValueError):
            return Response(
                {
                    "error": (
                        "Latitude, longitude and speed "
                        "must be valid numbers."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # =========================
        # SAVE LIVE LOCATION
        # =========================

        location, _ = DriverLocation.objects.update_or_create(
            driver=user,
            defaults={
                "latitude": latitude,
                "longitude": longitude,
            },
        )

        # =========================
        # SAVE SPEED HISTORY
        # =========================

        track_speed, speed_route_run = (
            should_track_driver_speed(user)
        )

        if track_speed and speed_route_run:
            DriverLocationHistory.objects.create(
                driver=user,
                route_run=speed_route_run,
                trip_type=speed_route_run.trip_type,
                latitude=latitude,
                longitude=longitude,
                speed_kmph=speed_kmph,
                is_overspeed=speed_kmph > 80,
            )

            # =========================
            # OVERSPEED ALERT
            # =========================

            if speed_kmph > 80:
                recent_alert_exists = Notification.objects.filter(
                    notification_type=(
                        Notification.TYPE_OVERSPEED
                    ),
                    driver=user,
                    created_at__gte=(
                        timezone.now()
                        - timezone.timedelta(minutes=5)
                    ),
                ).exists()

                if not recent_alert_exists:
                    self._notify_admins(
                        (
                            f"Driver {user.username} is "
                            f"overspeeding at "
                            f"{round(speed_kmph, 1)} km/h."
                        ),
                        title="⚠️ Overspeed Alert",
                        notification_type=(
                            Notification.TYPE_OVERSPEED
                        ),
                        priority=(
                            Notification.PRIORITY_CRITICAL
                        ),
                        route_run=speed_route_run,
                        driver=user,
                        push_data={
                            "type": "OVERSPEED_ALERT",
                            "driver_id": str(user.id),
                            "route_run_id": str(
                                speed_route_run.id,
                            ),
                            "speed": str(
                                round(speed_kmph, 1),
                            ),
                            "screen": "admin_dashboard",
                        },
                    )

        route_run = (
            started_trip.route_run
            if started_trip and started_trip.route_run
            else active_route_run
        )

        # =========================
        # ROUTE DELAY ALERT
        # =========================

        if route_run and route_run.started_at:
            running_minutes = (
                timezone.now() - route_run.started_at
            ).total_seconds() / 60

            # Existing fallback duration delay:
            # Pickup > 120 minutes
            # Drop > 90 minutes
            delay_limit = (
                120
                if route_run.trip_type
                == Trip.TRIP_TYPE_PICKUP
                else 90
            )

            if running_minutes > delay_limit:
                recent_delay_alert = Notification.objects.filter(
                    notification_type=(
                        Notification.TYPE_ROUTE_DELAY
                    ),
                    route_run=route_run,
                    created_at__gte=(
                        timezone.now()
                        - timezone.timedelta(minutes=15)
                    ),
                ).exists()

                if not recent_delay_alert:
                    self._notify_admins(
                        (
                            f"{route_run.trip_type.capitalize()} "
                            f"route #{route_run.id} is delayed."
                        ),
                        title="⏰ Route Delayed",
                        notification_type=(
                            Notification.TYPE_ROUTE_DELAY
                        ),
                        priority=(
                            Notification.PRIORITY_HIGH
                        ),
                        route_run=route_run,
                        driver=user,
                        push_data={
                            "type": "ROUTE_DELAY",
                            "route_run_id": str(route_run.id),
                            "driver_id": str(user.id),
                            "screen": "admin_dashboard",
                        },
                    )

        # =========================
        # EMPLOYEE DISTANCE ALERTS
        # =========================

        if route_run:
            current_stop = RouteService.get_current_stop(
                route_run,
            )

            if (
                current_stop
                and current_stop.pickup_latitude is not None
                and current_stop.pickup_longitude is not None
            ):
                distance_km = calculate_distance_km(
                    latitude,
                    longitude,
                    current_stop.pickup_latitude,
                    current_stop.pickup_longitude,
                )

                eta_minutes = max(
                    1,
                    int((distance_km / 25) * 60),
                )

                send_pickup_proximity_alerts(
                    route_run=route_run,
                    current_stop=current_stop,
                    distance_km=distance_km,
                    eta_minutes=eta_minutes,
                )

        serializer = self.get_serializer(location)

        return Response(
            serializer.data,
            status=status.HTTP_200_OK,
        )

    # =========================
    # NOTIFY ADMINS
    # Preserved as a wrapper so existing calls keep working.
    # =========================

    def _notify_admins(
        self,
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
        return NotificationService.notify_admins(
            message,
            title=title,
            push_data=push_data,
            notification_type=notification_type,
            priority=priority,
            trip=trip,
            route_run=route_run,
            driver=driver,
            employee=employee,
        )

    # =========================
    # SEND NOTIFICATION
    # Preserved as a wrapper so existing calls keep working.
    # =========================

    def send_notification(
        self,
        user,
        message,
        title="Trip Update",
        push_data=None,
    ):
        return NotificationService.send_notification(
            user,
            message,
            title=title,
            push_data=push_data,
        )