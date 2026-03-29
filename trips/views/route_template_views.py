from datetime import datetime
import traceback
from django.db import transaction
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from accounts.models import User
from trips.models import (
    RouteTemplate,
    RouteStop,
    RouteRun,
    RouteRunStop,
    Trip,
    Vehicle,
)
from trips.serializers import (
    RouteTemplateSerializer,
    RouteStopSerializer,
    VehicleOptionSerializer,
)


class RouteTemplateViewSet(viewsets.ModelViewSet):
    serializer_class = RouteTemplateSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user

        if user.role != "ADMIN":
            return RouteTemplate.objects.none()

        return (
            RouteTemplate.objects.prefetch_related("stops__employee")
            .select_related("driver", "vehicle")
            .order_by("-created_at")
        )

    def create(self, request, *args, **kwargs):
        if request.user.role != "ADMIN":
            return Response(
                {"error": "Only admin can create routes"},
                status=status.HTTP_403_FORBIDDEN,
            )
        return super().create(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        if request.user.role != "ADMIN":
            return Response(
                {"error": "Only admin can delete routes."},
                status=status.HTTP_403_FORBIDDEN,
            )

        route = self.get_object()

        has_active_trips = Trip.objects.filter(
            route_run__route_template=route,
            status__in=[Trip.STATUS_ASSIGNED, Trip.STATUS_STARTED],
        ).exists()

        if has_active_trips:
            return Response(
                {"detail": "Please cancel assigned trips before deleting this route."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return super().destroy(request, *args, **kwargs)

    # =========================
    # FORM DATA
    # =========================
    @action(detail=False, methods=["get"], url_path="create_form_data")
    def create_form_data(self, request):
        if request.user.role != "ADMIN":
            return Response(
                {"error": "Only admin can access route form data."},
                status=status.HTTP_403_FORBIDDEN,
            )

        used_driver_ids = set(
            RouteTemplate.objects.values_list("driver_id", flat=True)
        )

        used_employee_ids = set(
            RouteStop.objects.values_list("employee_id", flat=True)
        )

        drivers_qs = User.objects.filter(role="DRIVER").order_by("username")
        employees_qs = User.objects.filter(role="EMPLOYEE").order_by("username")
        vehicles_qs = Vehicle.objects.select_related("driver").all()

        drivers_data = []
        for driver in drivers_qs:
            drivers_data.append(
                {
                    "id": driver.id,
                    "username": driver.username,
                    "phone_number": getattr(driver, "phone_number", None),
                    "is_selectable": driver.id not in used_driver_ids,
                }
            )

        employees_data = []
        for employee in employees_qs:
            employees_data.append(
                {
                    "id": employee.id,
                    "username": employee.username,
                    "phone_number": getattr(employee, "phone_number", None),
                    "pickup_location": getattr(employee, "pickup_location", None),
                    "pickup_latitude": getattr(employee, "pickup_latitude", None),
                    "pickup_longitude": getattr(employee, "pickup_longitude", None),
                    "is_selectable": employee.id not in used_employee_ids,
                }
            )

        vehicles_data = VehicleOptionSerializer(vehicles_qs, many=True).data

        return Response(
            {
                "drivers": drivers_data,
                "employees": employees_data,
                "vehicles": vehicles_data,
            },
            status=status.HTTP_200_OK,
        )

    # =========================
    # COMMON HELPERS
    # =========================
    def _parse_pickup_datetime(self, date_str, time_str):
        if not date_str or not time_str:
            return None, Response(
                {"error": "date and time are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            pickup_datetime = datetime.strptime(
                f"{date_str} {time_str}",
                "%Y-%m-%d %H:%M",
            )
            return pickup_datetime, None

        except ValueError:
            return None, Response(
                {"error": "Invalid date/time format. Use YYYY-MM-DD and HH:MM."},
                status=status.HTTP_400_BAD_REQUEST,
            )

    def _get_duplicate_users(self, route, pickup_datetime, trip_type):
        duplicate_users = []

        for stop in route.stops.all():
            exists = Trip.objects.filter(
                employee=stop.employee,
                trip_date=pickup_datetime.date(),
                trip_type=trip_type,
                status__in=[
                    Trip.STATUS_ASSIGNED,
                    Trip.STATUS_STARTED,
                    Trip.STATUS_COMPLETED,
                ],
            ).exists()

            if exists:
                duplicate_users.append(stop.employee.username)

        return duplicate_users

    def _validate_duplicate_trips(self, route, pickup_datetime, trip_type):
        duplicate_users = self._get_duplicate_users(
            route,
            pickup_datetime,
            trip_type,
        )

        if duplicate_users:
            trip_label = "pickup" if trip_type == "PICKUP" else "drop"

            return Response(
                {
                    "error": f"{trip_label.capitalize()} already assigned for: {', '.join(duplicate_users)}"
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        return None

    def _create_route_run_and_trips(self, route, pickup_datetime, trip_type):
        with transaction.atomic():
            route_run = RouteRun.objects.create(
                route_template=route,
                driver=route.driver,
                vehicle=route.vehicle,
                trip_type=trip_type,
                run_date=pickup_datetime.date(),
            )

            created_count = 0

            for stop in route.stops.all():
                RouteRunStop.objects.create(
                    route_run=route_run,
                    employee=stop.employee,
                    pickup_location=stop.pickup_location,
                    pickup_latitude=stop.pickup_latitude,
                    pickup_longitude=stop.pickup_longitude,
                    stop_order=stop.stop_order,
                )

                Trip.objects.create(
                    route_run=route_run,
                    employee=stop.employee,
                    driver=route.driver,
                    vehicle=route.vehicle,
                    pickup_location=stop.pickup_location,
                    drop_location="Office",
                    trip_type=trip_type,
                    pickup_latitude=stop.pickup_latitude,
                    pickup_longitude=stop.pickup_longitude,
                    pickup_time=pickup_datetime,
                    trip_date=pickup_datetime.date(),
                    status=Trip.STATUS_ASSIGNED,
                    notification_sent=False,
                )

                created_count += 1

            return route_run, created_count

    # =========================
    # GENERATE TRIPS
    # =========================
    @action(detail=True, methods=["post"], url_path="generate_trips")
    def generate_trips(self, request, pk=None):
        if request.user.role != "ADMIN":
            return Response(
                {"error": "Only admin can generate trips"},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            route = self.get_object()

            trip_type = request.data.get("trip_type")

            if trip_type not in ["PICKUP", "DROP"]:
                return Response(
                    {"error": "trip_type must be PICKUP or DROP"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            pickup_datetime, error_response = self._parse_pickup_datetime(
                request.data.get("date"),
                request.data.get("time"),
            )

            if error_response:
                return error_response

            duplicate_response = self._validate_duplicate_trips(
                route,
                pickup_datetime,
                trip_type,
            )

            if duplicate_response:
                return duplicate_response

            route_run, created_count = self._create_route_run_and_trips(
                route,
                pickup_datetime,
                trip_type,
            )

            return Response(
                {
                    "message": f"{created_count} trip(s) created successfully.",
                    "route_run_id": route_run.id,
                },
                status=status.HTTP_201_CREATED,
            )

        except Exception as e:
            print("ERROR:", str(e))
            traceback.print_exc()

            return Response(
                {"detail": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )

    # =========================
    # REPEAT ROUTE
    # =========================
    @action(detail=True, methods=["post"], url_path="repeat_route")
    def repeat_route(self, request, pk=None):
        if request.user.role != "ADMIN":
            return Response(
                {"error": "Only admin can repeat routes."},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            route = self.get_object()

            trip_type = request.data.get("trip_type")

            if trip_type not in ["PICKUP", "DROP"]:
                return Response(
                    {"error": "trip_type must be PICKUP or DROP"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            pickup_datetime, error_response = self._parse_pickup_datetime(
                request.data.get("date"),
                request.data.get("time"),
            )

            if error_response:
                return error_response

            duplicate_response = self._validate_duplicate_trips(
                route,
                pickup_datetime,
                trip_type,
            )

            if duplicate_response:
                return duplicate_response

            route_run, created_count = self._create_route_run_and_trips(
                route,
                pickup_datetime,
                trip_type,
            )

            return Response(
                {
                    "message": f"{created_count} trip(s) created.",
                    "route_run_id": route_run.id,
                },
                status=status.HTTP_201_CREATED,
            )

        except Exception as e:
            print("ERROR:", str(e))
            traceback.print_exc()

            return Response(
                {"detail": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )


# =========================
# ROUTE STOP VIEWSET
# =========================
class RouteStopViewSet(viewsets.ModelViewSet):
    serializer_class = RouteStopSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user

        if user.role != "ADMIN":
            return RouteStop.objects.none()

        return RouteStop.objects.select_related(
            "route",
            "employee",
        ).order_by("route_id", "stop_order")

    def create(self, request, *args, **kwargs):
        if request.user.role != "ADMIN":
            return Response(
                {"error": "Only admin can create route stops."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        if request.user.role != "ADMIN":
            return Response(
                {"error": "Only admin can update route stops."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        if request.user.role != "ADMIN":
            return Response(
                {"error": "Only admin can delete route stops."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return super().destroy(request, *args, **kwargs)