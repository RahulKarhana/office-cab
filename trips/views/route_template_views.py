from datetime import datetime, time
import traceback

from django.db import transaction
from django.utils import timezone
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
from trips.utils.notification import send_push_notification


def is_admin_user(user):
    return bool(
        user.is_authenticated and (
            getattr(user, "role", "") == "ADMIN" or user.is_superuser
        )
    )


class RouteTemplateViewSet(viewsets.ModelViewSet):
    serializer_class = RouteTemplateSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user

        if not is_admin_user(user):
            return RouteTemplate.objects.none()

        return (
            RouteTemplate.objects.prefetch_related("stops__employee")
            .select_related("driver", "vehicle")
            .order_by("-created_at")
        )

    def create(self, request, *args, **kwargs):
        if not is_admin_user(request.user):
            return Response(
                {"error": "Only admin can create routes"},
                status=status.HTTP_403_FORBIDDEN,
            )
        return super().create(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        if not is_admin_user(request.user):
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

    @action(detail=False, methods=["get"], url_path="create_form_data")
    def create_form_data(self, request):
        if not is_admin_user(request.user):
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

    

    def _parse_pickup_datetime(self, date_str, time_str):
        if not date_str or not time_str:
            return None, Response(
                {"error": "date and time are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            naive_datetime = datetime.strptime(
                f"{date_str} {time_str}",
                "%Y-%m-%d %H:%M",
            )

            # ✅ CONVERT TO TIMEZONE AWARE
            pickup_datetime = timezone.make_aware(
                naive_datetime,
                timezone.get_current_timezone(),
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

    def _should_send_assignment_notification_now(self, route_run):
        now = timezone.localtime()
        today = timezone.localdate()

        ten_am_today = timezone.localtime().replace(
        hour=10, minute=0, second=0, microsecond=0
    )
        return route_run.run_date == today and now >= ten_am_today

    def _send_assignment_notifications(self, route, route_run, trip_type):
        send_push_notification(
            route.driver,
            "New Trip Assigned 🚖",
            f"You have a new {trip_type.lower()} route assigned.",
            {
                "type": "TRIP_ASSIGNED",
                "trip_type": trip_type,
                "route_id": str(route.id),
                "route_run_id": str(route_run.id),
            },
        )

        for stop in route.stops.all():
            send_push_notification(
                stop.employee,
                "Cab Assigned 🚖",
                f"Your cab for {trip_type.lower()} has been assigned.",
                {
                    "type": "TRIP_ASSIGNED",
                    "trip_type": trip_type,
                    "route_id": str(route.id),
                    "route_run_id": str(route_run.id),
                },
            )

        Trip.objects.filter(route_run=route_run).update(notification_sent=True)

    def _handle_assignment_notification(self, route, route_run, trip_type):
        if self._should_send_assignment_notification_now(route_run):
            self._send_assignment_notifications(route, route_run, trip_type)
            return True

        return False

    @action(detail=True, methods=["post"], url_path="generate_trips")
    def generate_trips(self, request, pk=None):
        if not is_admin_user(request.user):
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

            notification_sent = self._handle_assignment_notification(
                route,
                route_run,
                trip_type,
            )

            return Response(
                {
                    "message": f"{created_count} trip(s) created successfully.",
                    "route_run_id": route_run.id,
                    "notification_sent": notification_sent,
                    "notification_note": (
                        "Notification sent instantly."
                        if notification_sent
                        else "Notification will be sent by 10 AM scheduler."
                    ),
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

    @action(detail=False, methods=["post"], url_path="preview-route-order")
    def preview_route_order(self, request):
        employee_ids = request.data.get("employee_ids", [])
        mode = request.data.get("mode", "MANUAL")

        employees = list(User.objects.filter(id__in=employee_ids))

        if len(employees) < 2:
            return Response({"error": "Minimum 2 employees required"}, status=400)

        # 🟢 MANUAL
        if mode == "MANUAL":
            ordered = employees

        # 🔵 DISTANCE (simple optimization)
        elif mode == "DISTANCE":
            base = employees[0]

            def distance(emp):
                if not emp.pickup_latitude or not emp.pickup_longitude:
                    return 999999
                return (
                    (emp.pickup_latitude - base.pickup_latitude) ** 2 +
                    (emp.pickup_longitude - base.pickup_longitude) ** 2
                )

            ordered = sorted(employees, key=distance)

        # 🟣 FEMALE SAFETY
        elif mode == "SAFE":
            males = [e for e in employees if not e.is_female]
            females = [e for e in employees if e.is_female]

            ordered = []

            # ❌ Female should NOT be first pickup
            if males:
                ordered.append(males.pop(0))

            # females in middle
            ordered.extend(females)

            # remaining males
            ordered.extend(males)

        else:
            ordered = employees

        return Response([
            {
                "id": emp.id,
                "username": emp.username,
                "pickup_location": emp.pickup_location,
            }
            for emp in ordered
        ])
    
    @action(detail=True, methods=["post"], url_path="save-route-order")
    def save_route_order(self, request, pk=None):
        route = self.get_object()
        ordered_ids = request.data.get("employee_ids", [])

        if not ordered_ids:
            return Response({"error": "employee_ids required"}, status=400)

        RouteStop.objects.filter(route=route).delete()

        for index, emp_id in enumerate(ordered_ids, start=1):
            emp = User.objects.get(id=emp_id)

            RouteStop.objects.create(
                route=route,
                employee=emp,
                stop_order=index,
                pickup_location=emp.pickup_location,
                pickup_latitude=emp.pickup_latitude,
                pickup_longitude=emp.pickup_longitude,
            )

        return Response({"message": "Route saved successfully"})

        
    @action(detail=True, methods=["post"], url_path="repeat_route")
    def repeat_route(self, request, pk=None):
        if not is_admin_user(request.user):
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

            notification_sent = self._handle_assignment_notification(
                route,
                route_run,
                trip_type,
            )

            return Response(
                {
                    "message": f"{created_count} trip(s) created.",
                    "route_run_id": route_run.id,
                    "notification_sent": notification_sent,
                    "notification_note": (
                        "Notification sent instantly."
                        if notification_sent
                        else "Notification will be sent by 10 AM scheduler."
                    ),
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


class RouteStopViewSet(viewsets.ModelViewSet):
    serializer_class = RouteStopSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user

        if not is_admin_user(user):
            return RouteStop.objects.none()

        return RouteStop.objects.select_related(
            "route",
            "employee",
        ).order_by("route_id", "stop_order")

    def create(self, request, *args, **kwargs):
        if not is_admin_user(request.user):
            return Response(
                {"error": "Only admin can create route stops."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        if not is_admin_user(request.user):
            return Response(
                {"error": "Only admin can update route stops."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        if not is_admin_user(request.user):
            return Response(
                {"error": "Only admin can delete route stops."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return super().destroy(request, *args, **kwargs)