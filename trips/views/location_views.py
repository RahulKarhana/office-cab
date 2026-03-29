from math import radians, cos, sin, asin, sqrt
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from trips.models import DriverLocation, Trip, Notification, RouteRun
from trips.serializers import DriverLocationSerializer


def calculate_distance_km(lat1, lon1, lat2, lon2):
    lon1, lat1, lon2, lat2 = map(float, [lon1, lat1, lon2, lat2])
    lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])

    dlon = lon2 - lon1
    dlat = lat2 - lat1

    a = sin(dlat / 2) * 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) * 2
    c = 2 * asin(sqrt(a))

    r = 6371
    return c * r


class DriverLocationViewSet(viewsets.ModelViewSet):
    serializer_class = DriverLocationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user

        if user.role == "ADMIN":
            return DriverLocation.objects.select_related("driver").all()

        if user.role == "DRIVER":
            return DriverLocation.objects.select_related("driver").filter(driver=user)

        if user.role == "EMPLOYEE":
            active_trip = Trip.objects.select_related(
                "driver",
                "vehicle",
                "route_run",
            ).filter(
                employee=user,
                status=Trip.STATUS_STARTED,
            ).order_by("-created_at").first()

            if active_trip:
                return DriverLocation.objects.select_related("driver").filter(
                    driver=active_trip.driver
                )

            return DriverLocation.objects.none()

        return DriverLocation.objects.none()

    @action(detail=False, methods=["post"])
    def update_my_location(self, request):
        user = request.user

        if user.role != "DRIVER":
            raise PermissionDenied("Only driver can update location.")

        started_trip = Trip.objects.filter(
            driver=user,
            status=Trip.STATUS_STARTED,
        ).select_related(
            "employee",
            "driver",
            "route_run",
        ).first()

        active_route_run = RouteRun.objects.filter(
            driver=user,
            started_at__isnull=False,
            completed_at__isnull=True,
        ).prefetch_related("stops__employee").order_by("-created_at").first()

        if not started_trip and not active_route_run:
            return Response(
                {
                    "error": (
                        "You can update location only during a started trip "
                        "or active started route run."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        latitude = request.data.get("latitude")
        longitude = request.data.get("longitude")

        if latitude is None or longitude is None:
            return Response(
                {"error": "Latitude and longitude are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        location, created = DriverLocation.objects.get_or_create(
            driver=user,
            defaults={
                "latitude": latitude,
                "longitude": longitude,
            },
        )

        if not created:
            location.latitude = latitude
            location.longitude = longitude
            location.save()

        route_run = None
        if started_trip and started_trip.route_run:
            route_run = started_trip.route_run
        elif active_route_run:
            route_run = active_route_run

        if route_run:
            current_stop = route_run.stops.filter(
                is_picked=False
            ).order_by("stop_order").first()

            if (
                current_stop
                and current_stop.pickup_latitude is not None
                and current_stop.pickup_longitude is not None
            ):
                distance_km = calculate_distance_km(
                    float(latitude),
                    float(longitude),
                    float(current_stop.pickup_latitude),
                    float(current_stop.pickup_longitude),
                )

                today = timezone.localdate()

                if distance_km <= 1.0:
                    already_sent = Notification.objects.filter(
                        user=current_stop.employee,
                        title="Cab Near You",
                        message__icontains="1 km",
                        created_at__date=today,
                    ).exists()

                    if not already_sent:
                        Notification.objects.create(
                            user=current_stop.employee,
                            title="Cab Near You",
                            message=(
                                f"Your cab is about 1 km away. "
                                f"Driver {user.username} is on the way."
                            ),
                        )

                if distance_km <= 0.5:
                    already_sent = Notification.objects.filter(
                        user=current_stop.employee,
                        title="Cab 500m Away",
                        created_at__date=today,
                    ).exists()

                    if not already_sent:
                        Notification.objects.create(
                            user=current_stop.employee,
                            title="Cab 500m Away",
                            message=(
                                f"Your cab is about 500 meters away. "
                                f"Please be ready for pickup."
                            ),
                        )

                if distance_km <= 0.15:
                    already_sent = Notification.objects.filter(
                        user=current_stop.employee,
                        title="Cab Arrived",
                        created_at__date=today,
                    ).exists()

                    if not already_sent:
                        Notification.objects.create(
                            user=current_stop.employee,
                            title="Cab Arrived",
                            message="Your cab has arrived at the pickup point.",
                        )

        serializer = self.get_serializer(location, context={"request": request})
        return Response(serializer.data, status=status.HTTP_200_OK)