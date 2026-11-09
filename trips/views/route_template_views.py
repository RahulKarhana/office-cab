from datetime import datetime, timedelta
import traceback
from django.db import transaction
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.authentication import SessionAuthentication
from rest_framework_simplejwt.authentication import JWTAuthentication

from accounts.models import User
from trips.models import (
    RouteTemplate,
    RouteStop,
    RouteRun,
    RouteRunStop,
    Trip,
    Vehicle,
    EmployeeLeave,
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
            RouteTemplate.objects.filter(is_active=True)
            .prefetch_related("stops__employee")
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
                {"error": "Only admin can archive routes."},
                status=status.HTTP_403_FORBIDDEN,
            )

        route = self.get_object()

        has_active_trips = Trip.objects.filter(
            route_run__route_template=route,
            status__in=[Trip.STATUS_ASSIGNED, Trip.STATUS_STARTED],
        ).exists()

        if has_active_trips:
            return Response(
                {"detail": "Please cancel assigned/started trips before archiving this route."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        route.archive()

        return Response(
            {
                "detail": (
                    f'Route "{route.name}" archived successfully. '
                    "Historical trips, route runs, stops and chats were preserved."
                )
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["get"], url_path="create_form_data")
    def create_form_data(self, request):
        if not is_admin_user(request.user):
            return Response(
                {"error": "Only admin can access route form data."},
                status=status.HTTP_403_FORBIDDEN,
            )

        used_driver_ids = set(
            RouteTemplate.objects.filter(is_active=True).values_list("driver_id", flat=True)
        )

        used_employee_ids = set(
            RouteStop.objects.filter(route__is_active=True).values_list("employee_id", flat=True)
        )

        drivers_qs = User.objects.filter(role="DRIVER", is_active=True).order_by("username")
        employees_qs = User.objects.filter(role="EMPLOYEE", is_active=True).order_by("username")
        vehicles_qs = Vehicle.objects.select_related("driver").all()

        drivers_data = []
        for driver in drivers_qs:
            drivers_data.append(
                {
                    "id": driver.id,
                    "username": driver.display_name,
                    "phone_number": getattr(driver, "phone_number", None),
                    "is_selectable": driver.id not in used_driver_ids,
                }
            )

        employees_data = []
        for employee in employees_qs:
            employees_data.append(
                {
                    "id": employee.id,
                    "username": employee.display_name,
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
    
    def _resolve_shift_datetime(self, date_str, time_str, trip_type):
        """
        Admin enters the office shift date.

        For DROP trips scheduled after midnight, the actual
        departure belongs to the following calendar day.
        """
        pickup_datetime, error_response = self._parse_pickup_datetime(
            date_str,
            time_str,
        )

        if error_response is not None:
            return None, None, error_response

        shift_date = pickup_datetime.date()

        if trip_type == Trip.TRIP_TYPE_DROP:
            if pickup_datetime.hour < 12:
                pickup_datetime = pickup_datetime + timedelta(days=1)

        return shift_date, pickup_datetime, None

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

    def _get_duplicate_users(
        self, route, pickup_datetime, trip_type, shift_date
    ):
        duplicate_users = []

        for stop in route.stops.all():
            if EmployeeLeave.objects.filter(employee=stop.employee, leave_date=shift_date).exists():
                continue
            exists = Trip.objects.filter(
                employee=stop.employee,
                trip_date=shift_date,
                trip_type=trip_type,
                status__in=[
                    Trip.STATUS_ASSIGNED,
                    Trip.STATUS_STARTED,
                    Trip.STATUS_COMPLETED,
                ],
            ).exists()

            if exists:
                duplicate_users.append(stop.employee.display_name)

        return duplicate_users

    def _validate_duplicate_trips(
        self, route, pickup_datetime, trip_type, shift_date
    ):
        duplicate_users = self._get_duplicate_users(
            route,
            pickup_datetime,
            trip_type,
            shift_date,
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

    def _create_route_run_and_trips(
            self, route, pickup_datetime, trip_type, shift_date
        ):
        with transaction.atomic():
            template_stops = list(route.stops.all())
            custom_drop = trip_type == Trip.TRIP_TYPE_DROP and bool(template_stops) and all(
                stop.drop_order is not None for stop in template_stops
            )
            if custom_drop:
                orders = [stop.drop_order for stop in template_stops]
                if sorted(orders) != list(range(1, len(template_stops) + 1)):
                    raise ValueError("Saved DROP order is invalid. Please save it again.")

            route_run = RouteRun.objects.create(
                route_template=route,
                driver=route.driver,
                vehicle=route.vehicle,
                trip_type=trip_type,
                run_date=shift_date,
                use_custom_drop_order=custom_drop,
            )

            created_count = 0
            skipped_leave_count = 0

            for stop in template_stops:
                is_on_leave = EmployeeLeave.objects.filter(
                    employee=stop.employee,
                    leave_date=shift_date,
                ).exists()

                if is_on_leave:
                    skipped_leave_count += 1
                    continue

                RouteRunStop.objects.create(
                    route_run=route_run,
                    employee=stop.employee,
                    pickup_location=stop.pickup_location,
                    pickup_latitude=stop.pickup_latitude,
                    pickup_longitude=stop.pickup_longitude,
                    stop_order=(stop.drop_order if custom_drop else stop.stop_order),
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
                    trip_date=shift_date,
                    status=Trip.STATUS_ASSIGNED,
                    notification_sent=False,
                )

                created_count += 1

            if created_count == 0:
                route_run.delete()

            return route_run if created_count else None, created_count, skipped_leave_count
        
    def _should_send_assignment_notification_now(self, route_run):
        now = timezone.localtime()
        today = timezone.localdate()

        ten_am_today = timezone.localtime().replace(
        hour=10, minute=0, second=0, microsecond=0
    )
        # For an overnight DROP, the shift date can be yesterday while
        # the actual departure is today. Never send future-shift notices early.
        return route_run.run_date <= today and now >= ten_am_today

    def _send_assignment_notifications(self, route, route_run, trip_type):
        departure = Trip.objects.filter(route_run=route_run).order_by("id").first()
        departure_label = (
            timezone.localtime(departure.pickup_time).strftime("%d %b %Y, %I:%M %p")
            if departure else "the scheduled time"
        )
        send_push_notification(
            route.driver,
            "New Trip Assigned 🚖",
            f"You have a new {trip_type.lower()} route assigned for {departure_label}.",
            {
                "type": "TRIP_ASSIGNED",
                "trip_type": trip_type,
                "route_id": str(route.id),
                "route_run_id": str(route_run.id),
            },
        )

        for trip in Trip.objects.filter(route_run=route_run).select_related("employee"):
            send_push_notification(
                trip.employee,
                "Cab Assigned 🚖",
                f"Your {trip_type.lower()} cab is assigned for {departure_label}.",
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

            shift_date, pickup_datetime, error_response = (
                self._resolve_shift_datetime(
                    request.data.get("date"),
                    request.data.get("time"),
                    trip_type,
                )
            )

            if error_response is not None:
                return error_response

            duplicate_response = self._validate_duplicate_trips(
                route,
                pickup_datetime,
                trip_type,
                shift_date,
            )

            if duplicate_response is not None:
                return duplicate_response

            route_run, created_count, skipped_leave_count = self._create_route_run_and_trips(
                route,
                pickup_datetime,
                trip_type,
                shift_date,
            )

            if route_run is None:
                return Response(
                    {
                        "error": "No trips created. All employees may be on leave for selected date.",
                        "created_count": created_count,
                        "skipped_leave_count": skipped_leave_count,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            notification_sent = self._handle_assignment_notification(
                route,
                route_run,
                trip_type,
            )

            return Response(
                {
                    "message": f"{created_count} trip(s) created successfully. {skipped_leave_count} employee(s) skipped due to leave.",
                    "route_run_id": route_run.id,
                    "office_shift_date": shift_date.isoformat(),
                    "actual_departure_datetime": pickup_datetime.isoformat(),
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

    @action(detail=False,methods=["post"],url_path="preview-route-order",authentication_classes=[JWTAuthentication, SessionAuthentication],
    permission_classes=[IsAuthenticated],)
    def preview_route_order(self, request):
        if not is_admin_user(request.user):
            return Response({"error": "Only admin can preview route order."}, status=403)
        employee_ids = request.data.get("employee_ids", [])
        mode = request.data.get("mode", "MANUAL")

        if not employee_ids:
            return Response({"error": "Please select employees first."}, status=400)

        employees = list(
            User.objects.filter(id__in=employee_ids, role="EMPLOYEE")
        )

        employee_map = {emp.id: emp for emp in employees}

        employees = [
            employee_map[int(emp_id)]
            for emp_id in employee_ids
            if str(emp_id).isdigit() and int(emp_id) in employee_map
        ]

        if len(employees) < 2:
            return Response({"error": "Minimum 2 employees required."}, status=400)

        missing_location = [
            emp.display_name
            for emp in employees
            if emp.pickup_latitude is None or emp.pickup_longitude is None
        ]

        if missing_location:
            return Response(
                {
                    "error": "Pickup latitude/longitude missing for: "
                    + ", ".join(missing_location)
                },
                status=400,
            )

        mode = str(mode).upper()

        if mode == "MANUAL":
            ordered = employees

        elif mode == "DISTANCE":
            base = employees[0]

            def distance(emp):
                return (
                    (float(emp.pickup_latitude) - float(base.pickup_latitude)) ** 2
                    + (float(emp.pickup_longitude) - float(base.pickup_longitude)) ** 2
                )

            ordered = sorted(employees, key=distance)

        elif mode == "SAFE":
            males = [e for e in employees if not getattr(e, "is_female", False)]
            females = [e for e in employees if getattr(e, "is_female", False)]

            ordered = []

            if males:
                ordered.append(males.pop(0))

            ordered.extend(females)
            ordered.extend(males)

            if not ordered:
                ordered = employees

        else:
            ordered = employees

        return Response([
            {
                "id": emp.id,
                "username": emp.display_name,
                "pickup_location": emp.pickup_location,
                "pickup_latitude": emp.pickup_latitude,
                "pickup_longitude": emp.pickup_longitude,
            }
            for emp in ordered
        ])    
    def _validate_complete_stop_order(self, route, employee_ids):
        """Validate an exact permutation of the employees on this route."""
        if not isinstance(employee_ids, list) or not employee_ids:
            return None, "employee_ids must be a non-empty list."
        try:
            ids = [int(value) for value in employee_ids]
        except (TypeError, ValueError):
            return None, "All employee_ids must be integers."
        if len(set(ids)) != len(ids):
            return None, "Duplicate employee IDs are not allowed."
        existing_ids = set(route.stops.values_list("employee_id", flat=True))
        if set(ids) != existing_ids or len(ids) != len(existing_ids):
            return None, "Provide every employee already assigned to this route, exactly once."
        return ids, None

    @action(detail=True, methods=["post"], url_path="save-route-order")
    def save_route_order(self, request, pk=None):
        if not is_admin_user(request.user):
            return Response({"error": "Only admin can save route order."}, status=403)
        with transaction.atomic():
            route = self.get_object()
            ordered_ids, error = self._validate_complete_stop_order(
                route, request.data.get("employee_ids", [])
            )
            if error:
                return Response({"error": error}, status=400)
            stops_by_employee = {
                stop.employee_id: stop
                for stop in RouteStop.objects.select_for_update().filter(route=route)
            }
            for index, employee_id in enumerate(ordered_ids, start=1):
                stops_by_employee[employee_id].stop_order = index
            RouteStop.objects.bulk_update(list(stops_by_employee.values()), ["stop_order"])
        return Response({"message": "PICKUP order saved. Existing DROP order preserved."})

    @action(detail=True, methods=["post"], url_path="save-drop-order")
    def save_drop_order(self, request, pk=None):
        """Save an independent DROP order; affects only future generated runs."""
        if not is_admin_user(request.user):
            return Response({"error": "Only admin can save DROP order."}, status=403)
        with transaction.atomic():
            route = self.get_object()
            ordered_ids, error = self._validate_complete_stop_order(
                route, request.data.get("employee_ids", [])
            )
            if error:
                return Response({"error": error}, status=400)
            stops_by_employee = {
                stop.employee_id: stop
                for stop in RouteStop.objects.select_for_update().filter(route=route)
            }
            for index, employee_id in enumerate(ordered_ids, start=1):
                stops_by_employee[employee_id].drop_order = index
            RouteStop.objects.bulk_update(list(stops_by_employee.values()), ["drop_order"])
        return Response({"message": "Custom DROP order saved for future runs."})

    @action(detail=True, methods=["post"], url_path="reset-drop-order")
    def reset_drop_order(self, request, pk=None):
        """Return future DROP runs to the legacy reverse-PICKUP behavior."""
        if not is_admin_user(request.user):
            return Response({"error": "Only admin can reset DROP order."}, status=403)
        with transaction.atomic():
            route = self.get_object()
            RouteStop.objects.select_for_update().filter(route=route).update(drop_order=None)
        return Response({"message": "Future DROP runs will use reverse PICKUP order."})

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

            shift_date, pickup_datetime, error_response = self._resolve_shift_datetime(
                request.data.get("date"),
                request.data.get("time"),
                trip_type,
            )

            if error_response is not None:
                return error_response

            duplicate_response = self._validate_duplicate_trips(
                route,
                pickup_datetime,
                trip_type,
                shift_date,
            )

            if duplicate_response is not None:
                return duplicate_response

            route_run, created_count, skipped_leave_count = self._create_route_run_and_trips(
                route,
                pickup_datetime,
                trip_type,
                shift_date,
            )

            if route_run is None:
                return Response(
                    {
                        "error": "No trips created. All employees may be on leave for selected date.",
                        "created_count": created_count,
                        "skipped_leave_count": skipped_leave_count,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
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
                    "office_shift_date": shift_date.isoformat(),
                    "actual_departure_datetime": pickup_datetime.isoformat(),
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

        return RouteStop.objects.filter(
            route__is_active=True,
        ).select_related(
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