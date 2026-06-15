import json
from datetime import timedelta
from rest_framework.test import APIRequestFactory, force_authenticate
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import Q
from django.db import transaction
import math
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import resolve, reverse
from django.utils import timezone
from django.db.models import Count, Q, Avg, Max
from django.utils.dateparse import parse_date
from collections import OrderedDict
from django.contrib.auth import authenticate, login, logout
from django.contrib import messages
from math import radians, cos, sin, asin, sqrt
from django.views.decorators.http import require_GET, require_POST
import openpyxl
from django.db.models import Count, Q
from django.http import HttpResponse
from trips.models import (
    Trip,
    Vehicle,
    RouteTemplate,
    RouteRun,
    RouteStop,
    Notification,
    EmergencyAlert,
    DriverLocation,
    RouteRunStop,
    Review,
    EmployeeLeave,
    TripCancellation,
    DriverLocationHistory,
)

User = get_user_model()


# =========================
# AUTH / PERMISSION HELPERS
# =========================
def _is_admin_user(user):
    return bool(
        user.is_authenticated and
        (getattr(user, "role", "") == "ADMIN" or user.is_superuser)
    )


admin_required = user_passes_test(_is_admin_user, login_url="/admin/login/")


# =========================
# HELPERS
# =========================
def _redirect_back(request, fallback_name="/admin-web/routes/"):
    referer = request.META.get("HTTP_REFERER")
    if referer:
        return redirect(referer)
    return redirect(fallback_name)


def _extract_response_message(data, default_message):
    if isinstance(data, dict):
        if data.get("message"):
            return str(data["message"])
        if data.get("detail"):
            return str(data["detail"])
        if data.get("error"):
            return str(data["error"])
        if data.get("errors"):
            return str(data["errors"])
    return default_message


def _safe_float(value):
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _build_internal_api_request(original_request, path, payload=None, method="post"):
    factory = APIRequestFactory()
    method = (method or "post").lower()

    payload = payload or {}

    if method == "post":
        internal_request = factory.post(path, data=payload, format="json")
    elif method == "put":
        internal_request = factory.put(path, data=payload, format="json")
    elif method == "patch":
        internal_request = factory.patch(path, data=payload, format="json")
    elif method == "delete":
        internal_request = factory.delete(path, data=payload, format="json")
    else:
        internal_request = factory.post(path, data=payload, format="json")

    force_authenticate(internal_request, user=original_request.user)

    internal_request.session = getattr(original_request, "session", None)
    internal_request.COOKIES = getattr(original_request, "COOKIES", {})
    internal_request.META["HTTP_HOST"] = original_request.META.get("HTTP_HOST", "")
    internal_request.META["SERVER_NAME"] = original_request.META.get("SERVER_NAME", "localhost")
    internal_request.META["SERVER_PORT"] = original_request.META.get("SERVER_PORT", "8000")
    internal_request.META["wsgi.url_scheme"] = original_request.META.get("wsgi.url_scheme", "http")

    return internal_request


def _call_same_server_api(request, path, payload=None, method="post"):
    try:
        match = resolve(path)
    except Exception as e:
        return False, 404, {"detail": f"API path not found: {path}", "error": str(e)}

    try:
        internal_request = _build_internal_api_request(
            request,
            path,
            payload,
            method=method,
        )
        response = match.func(internal_request, *match.args, **match.kwargs)
    except Exception as e:
        return False, 500, {"detail": f"Internal API call failed: {str(e)}"}

    status_code = getattr(response, "status_code", 500)
    data = {}

    try:
        if hasattr(response, "data"):
            data = response.data
        else:
            data = json.loads(response.content.decode("utf-8"))
    except Exception:
        try:
            data = {"detail": response.content.decode("utf-8", errors="ignore")}
        except Exception:
            data = {"detail": "Unknown response received from API."}

    success = 200 <= status_code < 300
    return success, status_code, data

def _get_latest_driver_locations_map():
    latest_map = {}
    for dl in DriverLocation.objects.select_related("driver").order_by("-updated_at", "-id"):
        if dl.driver_id not in latest_map:
            latest_map[dl.driver_id] = dl
    return latest_map




def _distance_km(lat1, lng1, lat2, lng2):
    try:
        lat1 = float(lat1)
        lng1 = float(lng1)
        lat2 = float(lat2)
        lng2 = float(lng2)
    except (TypeError, ValueError):
        return None

    radius = 6371

    d_lat = math.radians(lat2 - lat1)
    d_lng = math.radians(lng2 - lng1)

    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(d_lng / 2) ** 2
    )

    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return round(radius * c, 2)

def admin_login_page(request):
    if request.user.is_authenticated:
        return redirect("admin_web:dashboard")

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "").strip()

        user = authenticate(request, username=username, password=password)

        if user is not None and user.is_staff:
            login(request, user)
            next_url = request.GET.get("next")
            return redirect(next_url or "admin_web:dashboard")

        messages.error(request, "Invalid username or password.")

    return render(request, "admin_web/login.html")


def admin_logout_page(request):
    logout(request)
    return redirect("admin_web:admin_login")

# =========================
# DASHBOARD
# =========================@login_required
@admin_required
@require_GET
def dashboard(request):
    today = timezone.localdate()

    total_employees = User.objects.filter(role="EMPLOYEE").count()
    total_drivers = User.objects.filter(role="DRIVER").count()
    total_routes = RouteTemplate.objects.count()
    total_vehicles = Vehicle.objects.count()

    today_trips_qs = Trip.objects.select_related(
        "employee",
        "driver",
        "vehicle",
        "route_run",
        "route_run__route_template",
    ).filter(trip_date=today)

    today_total_trips = today_trips_qs.count()
    today_completed_trips = today_trips_qs.filter(status=Trip.STATUS_COMPLETED).count()
    today_cancelled_trips = today_trips_qs.filter(status=Trip.STATUS_CANCELLED).count()
    today_started_trips = today_trips_qs.filter(status=Trip.STATUS_STARTED).count()

    completion_percent = (
        round((today_completed_trips / today_total_trips) * 100)
        if today_total_trips > 0
        else 0
    )

    unread_notifications = Notification.objects.filter(is_read=False).count()
    unread_emergency_alerts = EmergencyAlert.objects.filter(status="ACTIVE").count()

    # =========================
    # TODAY DASHBOARD ANALYTICS
    # =========================
    started_cabs = RouteRun.objects.select_related(
        "route_template",
        "driver",
        "vehicle",
    ).filter(
        run_date=today,
        started_at__isnull=False,
        completed_at__isnull=True,
    ).order_by("-started_at")

    completed_cabs = RouteRun.objects.select_related(
        "route_template",
        "driver",
        "vehicle",
    ).filter(
        run_date=today,
        completed_at__isnull=False,
    ).order_by("-completed_at")

    late_cabs = RouteRun.objects.select_related(
        "route_template",
        "driver",
        "vehicle",
    ).filter(
        run_date=today,
        started_at__isnull=True,
        trips__status=Trip.STATUS_ASSIGNED,
    ).distinct().order_by("run_date")

    cancelled_trips = today_trips_qs.filter(
        status=Trip.STATUS_CANCELLED
    ).order_by("-created_at")

    no_show_stops = RouteRunStop.objects.select_related(
        "employee",
        "route_run",
        "route_run__route_template",
        "route_run__driver",
        "route_run__vehicle",
    ).filter(
        route_run__run_date=today,
        is_no_show=True,
    ).order_by("-picked_at", "stop_order")

    recent_live_trips = today_trips_qs.filter(
        status__in=[
            Trip.STATUS_ASSIGNED,
            Trip.STATUS_STARTED,
            Trip.STATUS_COMPLETED,
            Trip.STATUS_CANCELLED,
        ]
    ).order_by("-created_at")[:8]

    recent_alerts = EmergencyAlert.objects.select_related(
        "employee"
    ).order_by("-created_at")[:5]

    context = {
        "today": today,

        "total_employees": total_employees,
        "total_drivers": total_drivers,
        "total_routes": total_routes,
        "total_vehicles": total_vehicles,

        "today_total_trips": today_total_trips,
        "today_completed_trips": today_completed_trips,
        "today_cancelled_trips": today_cancelled_trips,
        "today_started_trips": today_started_trips,
        "completion_percent": completion_percent,

        "unread_notifications": unread_notifications,
        "unread_emergency_alerts": unread_emergency_alerts,

        "started_cabs": started_cabs,
        "completed_cabs": completed_cabs,
        "late_cabs": late_cabs,
        "cancelled_trips": cancelled_trips,
        "no_show_stops": no_show_stops,

        "started_cabs_count": started_cabs.count(),
        "completed_cabs_count": completed_cabs.count(),
        "late_cabs_count": late_cabs.count(),
        "cancelled_trips_count": cancelled_trips.count(),
        "no_show_count": no_show_stops.count(),

        "recent_live_trips": recent_live_trips,
        "recent_alerts": recent_alerts,
    }

    return render(request, "admin_web/dashboard.html", context)


# =========================
# EMPLOYEES
# =========================
@login_required
@admin_required
@require_GET
def employees_page(request):
    query = request.GET.get("q", "").strip()
    filter_type = request.GET.get("filter", "all").strip()

    employees = User.objects.filter(role="EMPLOYEE").order_by("username")

    today = timezone.localdate()

    assigned_employee_ids = set(
        RouteStop.objects.values_list("employee_id", flat=True)
    )

    total_employees = employees.count()
    active_employees = employees.filter(is_active=True).count()
    inactive_employees = employees.filter(is_active=False).count()
    employees_with_pickup = employees.exclude(
        pickup_location__isnull=True
    ).exclude(
        pickup_location=""
    ).count()

    assigned_employees_count = employees.filter(
        id__in=assigned_employee_ids
    ).count()

    unassigned_employees_count = employees.exclude(
        id__in=assigned_employee_ids
    ).count()

    if query:
        employees = employees.filter(
            Q(username__icontains=query)
            | Q(phone_number__icontains=query)
            | Q(address__icontains=query)
            | Q(pickup_location__icontains=query)
        )

    if filter_type == "assigned":
        employees = employees.filter(id__in=assigned_employee_ids)
    elif filter_type == "unassigned":
        employees = employees.exclude(id__in=assigned_employee_ids)
    elif filter_type == "active":
        employees = employees.filter(is_active=True)
    elif filter_type == "inactive":
        employees = employees.filter(is_active=False)

    employee_rows = []

    for employee in employees:
        today_trips = Trip.objects.select_related(
            "driver",
            "vehicle",
            "route_run",
            "route_run__route_template",
        ).filter(
            employee=employee,
            trip_date=today,
        ).exclude(
            status=Trip.STATUS_CANCELLED
        )

        pickup_trip = today_trips.filter(trip_type=Trip.TRIP_TYPE_PICKUP).first()
        drop_trip = today_trips.filter(trip_type=Trip.TRIP_TYPE_DROP).first()

        main_trip = pickup_trip or drop_trip

        employee_rows.append({
            "employee": employee,
            "is_assigned": employee.id in assigned_employee_ids,
            "pickup_trip": pickup_trip,
            "drop_trip": drop_trip,
            "main_trip": main_trip,
            "route_name": (
                main_trip.route_run.route_template.name
                if main_trip and main_trip.route_run and main_trip.route_run.route_template
                else "--"
            ),
            "driver_name": main_trip.driver.username if main_trip and main_trip.driver else "--",
            "vehicle_number": main_trip.vehicle.vehicle_number if main_trip and main_trip.vehicle else "--",
        })

    return render(request, "admin_web/employees.html", {
        "employee_rows": employee_rows,
        "query": query,
        "filter_type": filter_type,
        "total_employees": total_employees,
        "active_employees": active_employees,
        "inactive_employees": inactive_employees,
        "employees_with_pickup": employees_with_pickup,
        "assigned_employees_count": assigned_employees_count,
        "unassigned_employees_count": unassigned_employees_count,
        "today": today,
    })

# =========================
# DRIVERS
# =========================
@login_required
@admin_required
@require_GET
def drivers_page(request):
    query = request.GET.get("q", "").strip()
    drivers = User.objects.filter(role="DRIVER").select_related("vehicle").order_by("-id")

    if query:
        drivers = drivers.filter(
            Q(username__icontains=query) |
            Q(vehicle__vehicle_number__icontains=query)
        )

    return render(request, "admin_web/drivers.html", {
        "drivers": drivers,
        "query": query,
    })

@login_required
@admin_required
@require_GET
def notifications_page(request):
    notifications = Notification.objects.select_related(
        "user",
        "driver",
        "employee",
        "trip",
        "route_run",
    ).order_by("-created_at")

    unread_count = notifications.filter(is_read=False).count()

    return render(request, "admin_web/notifications.html", {
        "notifications": notifications,
        "unread_count": unread_count,
    })

# =========================
# ROUTES
# =========================
@login_required
@admin_required
@require_GET
def routes_page(request):
    query = request.GET.get("q", "").strip()
    selected_date = request.GET.get("date", "").strip()

    today = timezone.localdate()
    if not selected_date:
        selected_date = str(today)

    parsed_selected_date = parse_date(selected_date) if selected_date else None
    selected_day = str(parsed_selected_date.day) if parsed_selected_date else ""

    routes = RouteTemplate.objects.select_related(
        "driver",
        "vehicle",
    ).prefetch_related(
        "stops",
        "stops__employee",
    ).order_by("id")

    if query:
        routes = routes.filter(
            Q(name__icontains=query)
            | Q(driver__username__icontains=query)
            | Q(vehicle__vehicle_number__icontains=query)
            | Q(vehicle__vehicle_model__icontains=query)
        )

    route_cards = []
    total_routes = 0
    pickup_assigned_count = 0
    drop_assigned_count = 0
    both_assigned_count = 0
    nothing_assigned_count = 0

    for index, route in enumerate(routes, start=1):
        total_routes += 1

        stops = list(route.stops.all())
        total_employees = len(stops)

        seat_count = route.vehicle.seat_count if getattr(route, "vehicle", None) else 0
        assigned_employees_count = total_employees
        remaining_seats = max(seat_count - assigned_employees_count, 0)

        pickup_assigned = False
        drop_assigned = False

        if parsed_selected_date:
            pickup_assigned = Trip.objects.filter(
                route_run__route_template=route,
                trip_type=Trip.TRIP_TYPE_PICKUP,
                trip_date=parsed_selected_date,
            ).exclude(status=Trip.STATUS_CANCELLED).exists()

            drop_assigned = Trip.objects.filter(
                route_run__route_template=route,
                trip_type=Trip.TRIP_TYPE_DROP,
                trip_date=parsed_selected_date,
            ).exclude(status=Trip.STATUS_CANCELLED).exists()

        if pickup_assigned and drop_assigned:
            ui_state = "both"
            ui_label = "Pickup + Drop Assigned"
            both_assigned_count += 1
            pickup_assigned_count += 1
            drop_assigned_count += 1
        elif pickup_assigned:
            ui_state = "pickup"
            ui_label = "Pickup Assigned"
            pickup_assigned_count += 1
        elif drop_assigned:
            ui_state = "drop"
            ui_label = "Drop Assigned"
            drop_assigned_count += 1
        else:
            ui_state = "none"
            ui_label = "Nothing Assigned"
            nothing_assigned_count += 1

        employee_rows = []
        for stop_index, stop in enumerate(stops, start=1):
            employee_name = stop.employee.username if getattr(stop, "employee", None) else "--"

            employee_rows.append({
                "index": stop_index,
                "employee_id": stop.employee.id if getattr(stop, "employee", None) else None,
                "employee_name": employee_name or "--",
                "pickup_location": getattr(stop, "pickup_location", "") or "--",
                "pickup_latitude": getattr(stop, "pickup_latitude", None),
                "pickup_longitude": getattr(stop, "pickup_longitude", None),
            })

        route_cards.append({
            "index": index,
            "id": route.id,
            "name": route.name or f"Route {route.id}",
            "driver_id": route.driver.id if getattr(route, "driver", None) else "",
            "driver_name": route.driver.username if getattr(route, "driver", None) else "--",
            "vehicle_id": route.vehicle.id if getattr(route, "vehicle", None) else "",
            "vehicle_number": route.vehicle.vehicle_number if getattr(route, "vehicle", None) else "--",
            "vehicle_model": route.vehicle.vehicle_model if getattr(route, "vehicle", None) else "--",
            "pickup_assigned": pickup_assigned,
            "drop_assigned": drop_assigned,
            "ui_state": ui_state,
            "ui_label": ui_label,
            "employee_rows": employee_rows,
            "total_employees": total_employees,
            "seat_count": seat_count,
            "assigned_employees_count": assigned_employees_count,
            "remaining_seats": remaining_seats,
        })

    employees = User.objects.filter(role="EMPLOYEE", is_active=True).order_by("username")
    drivers = User.objects.filter(role="DRIVER", is_active=True).order_by("username")
    vehicles = Vehicle.objects.select_related("driver").order_by("vehicle_number")

    holiday_dates = []

    context = {
        "page_name": "Routes",
        "query": query,
        "selected_date": selected_date,
        "route_cards": route_cards,
        "total_routes": total_routes,
        "pickup_assigned_count": pickup_assigned_count,
        "drop_assigned_count": drop_assigned_count,
        "both_assigned_count": both_assigned_count,
        "nothing_assigned_count": nothing_assigned_count,
        "selected_day": selected_day,
        "employees": employees,
        "drivers": drivers,
        "vehicles": vehicles,
        "holiday_dates": holiday_dates,
    }
    return render(request, "admin_web/routes.html", context)

@login_required
@admin_required
@require_POST
def create_route(request):
    name = request.POST.get("name", "").strip()
    driver = request.POST.get("driver")
    vehicle = request.POST.get("vehicle")
    stops_json = request.POST.get("stops_json", "[]")

    if not name:
        messages.error(request, "Route name is required.")
        return redirect("/admin-web/routes/")

    try:
        stops = json.loads(stops_json)
    except Exception:
        messages.error(request, "Invalid stops data.")
        return redirect("/admin-web/routes/")

    payload = {
        "name": name,
        "driver": driver or None,
        "vehicle": vehicle or None,
        "stops": stops,
    }

    success, status_code, data = _call_same_server_api(
        request,
        "/api/trips/routes/",
        payload,
        method="post",
    )

    if success:
        messages.success(request, _extract_response_message(data, "Route created successfully."))
    else:
        messages.error(request, _extract_response_message(data, f"Failed to create route. ({status_code})"))

    return redirect("/admin-web/routes/")


@login_required
@admin_required
@require_POST
def edit_route(request, route_id):
    name = request.POST.get("name", "").strip()
    driver = request.POST.get("driver")
    vehicle = request.POST.get("vehicle")
    stops_json = request.POST.get("stops_json", "[]")

    if not name:
        messages.error(request, "Route name is required.")
        return redirect("/admin-web/routes/")

    try:
        stops = json.loads(stops_json)
    except Exception:
        messages.error(request, "Invalid stops data.")
        return redirect("/admin-web/routes/")

    payload = {
        "name": name,
        "driver": driver or None,
        "vehicle": vehicle or None,
        "stops": stops,
    }

    success, status_code, data = _call_same_server_api(
        request,
        f"/api/trips/routes/{route_id}/",
        payload,
        method="put",
    )

    if success:
        messages.success(request, _extract_response_message(data, "Route updated successfully."))
    else:
        messages.error(request, _extract_response_message(data, f"Failed to update route. ({status_code})"))

    return redirect("/admin-web/routes/")


@login_required
@admin_required
@require_POST
def delete_route(request, route_id):
    route = get_object_or_404(RouteTemplate, id=route_id)
    route_name = route.name or f"Route {route.id}"
    route.delete()
    messages.success(request, f'Route "{route_name}" deleted successfully.')
    return redirect("/admin-web/routes/")


@login_required
@admin_required
@require_POST
def assign_route_trip(request, route_id, trip_type):
    route = get_object_or_404(RouteTemplate, id=route_id)
    trip_type = (trip_type or "").upper()

    if trip_type not in ["PICKUP", "DROP"]:
        messages.error(request, "Invalid trip type.")
        return redirect("/admin-web/routes/")

    date = request.POST.get("date", "").strip()
    time = request.POST.get("time", "").strip()

    if not date or not time:
        messages.error(request, "Date and time are required.")
        return redirect("/admin-web/routes/")

    success, status_code, data = _call_same_server_api(
        request,
        f"/api/trips/routes/{route.id}/generate_trips/",
        {
            "date": date,
            "time": time,
            "trip_type": trip_type,
        },
        method="post",
    )

    if success:
        messages.success(
            request,
            _extract_response_message(data, f"{trip_type.title()} assigned successfully."),
        )
    else:
        messages.error(
            request,
            _extract_response_message(data, f"Failed to assign {trip_type.lower()} route. ({status_code})"),
        )

    return redirect("/admin-web/routes/")


@login_required
@admin_required
@require_POST
def repeat_route_action(request, route_id):
    route = get_object_or_404(RouteTemplate, id=route_id)

    trip_type = request.POST.get("trip_type", "").strip().upper()
    date = request.POST.get("date", "").strip()
    time = request.POST.get("time", "").strip()

    if trip_type not in ["PICKUP", "DROP"]:
        messages.error(request, "Select a valid repeat route type.")
        return redirect("/admin-web/routes/")

    if not date or not time:
        messages.error(request, "Date and time are required.")
        return redirect("/admin-web/routes/")

    success, status_code, data = _call_same_server_api(
        request,
        f"/api/trips/routes/{route.id}/repeat_route/",
        {
            "date": date,
            "time": time,
            "trip_type": trip_type,
        },
        method="post",
    )

    if success:
        messages.success(
            request,
            _extract_response_message(data, "Route repeated successfully."),
        )
    else:
        messages.error(
            request,
            _extract_response_message(data, f"Failed to repeat route. ({status_code})"),
        )

    return redirect("/admin-web/routes/")       


@login_required
@admin_required
@require_POST
def repeat_all_routes_action(request):
    trip_type = request.POST.get("trip_type", "").strip().upper()
    time = request.POST.get("time", "").strip()

    start_date_str = request.POST.get("start_date", "").strip()
    end_date_str = request.POST.get("end_date", "").strip()
    single_date_str = request.POST.get("date", "").strip()

    if not trip_type or not time:
        messages.error(request, "Trip type and time are required.")
        return redirect("/admin-web/routes/")

    if trip_type not in ["PICKUP", "DROP"]:
        messages.error(request, "Invalid trip type.")
        return redirect("/admin-web/routes/")

    if single_date_str and not start_date_str and not end_date_str:
        start_date = parse_date(single_date_str)
        end_date = start_date
    else:
        start_date = parse_date(start_date_str) if start_date_str else None
        end_date = parse_date(end_date_str) if end_date_str else None

    if not start_date or not end_date:
        messages.error(request, "Valid date or date range is required.")
        return redirect("/admin-web/routes/")

    if start_date > end_date:
        messages.error(request, "End date cannot be earlier than start date.")
        return redirect("/admin-web/routes/")

    routes = RouteTemplate.objects.all().order_by("id")
    if not routes.exists():
        messages.warning(request, "No saved routes found to repeat.")
        return redirect("/admin-web/routes/")

    success_count = 0
    fail_count = 0

    current_date = start_date
    while current_date <= end_date:
        for route in routes:
            success, _, _ = _call_same_server_api(
                request,
                f"/api/trips/routes/{route.id}/repeat_route/",
                {
                    "date": str(current_date),
                    "time": time,
                    "trip_type": trip_type,
                },
                method="post",
            )
            if success:
                success_count += 1
            else:
                fail_count += 1
        current_date += timedelta(days=1)

    if success_count:
        messages.success(request, f"{success_count} routes repeated successfully.")
    if fail_count:
        messages.warning(request, f"{fail_count} routes skipped or failed.")
    if not success_count and not fail_count:
        messages.info(request, "No routes were repeated.")

    return redirect("/admin-web/routes/")


# =========================
# TRIPS
# =========================
@login_required
@admin_required
@require_GET
def trips_page(request):
    query = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "").strip()
    date_filter = request.GET.get("date", "").strip()

    route_runs = RouteRun.objects.select_related(
        "driver",
        "vehicle",
        "route_template",
    ).prefetch_related(
        "stops__employee"
    ).order_by("-created_at")

    if query:
        route_runs = route_runs.filter(
            Q(driver__username__icontains=query) |
            Q(vehicle__vehicle_number__icontains=query) |
            Q(route_template__name__icontains=query)
        )

    parsed_date = parse_date(date_filter) if date_filter else None
    if parsed_date:
        route_runs = route_runs.filter(run_date=parsed_date)

    trip_rows = []

    for run in route_runs:

        employees = []
        completed_stops = 0
        total_stops = run.stops.count()

        for stop in run.stops.all().order_by("stop_order"):

            if stop.employee:
                employees.append(stop.employee.username)

            if stop.is_picked:
                completed_stops += 1

        if run.completed_at:
            status = "COMPLETED"
        elif run.started_at:
            status = "STARTED"
        else:
            status = "ASSIGNED"

        if status_filter and status != status_filter:
            continue

        trip_rows.append({
            "id": run.id,
            "route_name": run.route_template.name if run.route_template else "Manual Route",
            "driver_name": run.driver.username if run.driver else "--",
            "vehicle_number": run.vehicle.vehicle_number if run.vehicle else "--",
            "trip_type": run.trip_type,
            "run_date": run.run_date,
            "status": status,
            "employee_count": len(employees),
            "employees": employees,
            "completed_stops": completed_stops,
            "total_stops": total_stops,
            "progress_percent": round(
                (completed_stops / total_stops) * 100
            ) if total_stops else 0,
        })

    context = {
        "trips": trip_rows,
        "query": query,
        "status_filter": status_filter,
        "date_filter": date_filter,
    }

    return render(
        request,
        "admin_web/trips.html",
        context
    )


@login_required
@admin_required
@require_POST
def cancel_trip(request, trip_id):
    trip = get_object_or_404(Trip, id=trip_id)

    if trip.status == "COMPLETED":
        messages.error(request, "Completed trip cannot be cancelled.")
        return redirect("admin_web:trips")

    if trip.status == "CANCELLED":
        messages.warning(request, "Trip is already cancelled.")
        return redirect("admin_web:trips")

    success, status_code, data = _call_same_server_api(
        request,
        f"/api/trips/{trip_id}/cancel-trip/",
        {},
        method="post",
    )

    if success:
        messages.success(
            request,
            _extract_response_message(data, "Trip cancelled successfully."),
        )
    else:
        messages.error(
            request,
            _extract_response_message(data, f"Failed to cancel trip. ({status_code})"),
        )

    return redirect("admin_web:trips")


# =========================
# EMERGENCY ALERTS
# =========================
@login_required
@admin_required
@require_GET
def alerts_page(request):
    query = request.GET.get("q", "").strip()

    alerts = EmergencyAlert.objects.select_related(
        "employee",
        "trip",
        "route_run",
        "route_run__route_template",
    ).order_by("-created_at")

    if query:
        alerts = alerts.filter(
            Q(employee__username__icontains=query) |
            Q(title__icontains=query) |
            Q(message__icontains=query)
        )

    latest_driver_locations = _get_latest_driver_locations_map()
    alert_modal_data = {}

    for alert in alerts:
        trip = getattr(alert, "trip", None)
        employee = getattr(alert, "employee", None)
        route_run = getattr(alert, "route_run", None)

        driver = None
        vehicle_number = "--"
        route_name = "--"

        if trip and trip.driver:
            driver = trip.driver
        elif route_run and getattr(route_run, "driver", None):
            driver = route_run.driver

        if trip and trip.vehicle:
            vehicle_number = trip.vehicle.vehicle_number
        elif route_run and getattr(route_run, "vehicle", None):
            vehicle_number = route_run.vehicle.vehicle_number

        if route_run and getattr(route_run, "route_template", None):
            route_name = route_run.route_template.name

        driver_location = latest_driver_locations.get(driver.id) if driver else None

        alert_modal_data[str(alert.id)] = {
            "id": alert.id,
            "title": alert.title or "Emergency SOS",
            "message": alert.message or "",
            "status": alert.status or "ACTIVE",
            "created_at": alert.created_at.isoformat() if alert.created_at else "",
            "employee_name": employee.username if employee else "--",
            "employee_phone": getattr(employee, "phone_number", "") if employee else "",
            "pickup_location": getattr(trip, "pickup_location", "") if trip else "",
            "drop_location": getattr(trip, "drop_location", "") if trip else "",
            "trip_type": getattr(trip, "trip_type", "") if trip else "",
            "trip_status": getattr(trip, "status", "") if trip else "",
            "trip_id": trip.id if trip else None,
            "driver_name": driver.username if driver else "--",
            "driver_id": driver.id if driver else None,
            "vehicle_number": vehicle_number,
            "route_name": route_name,
            "alert_lat": _safe_float(alert.latitude),
            "alert_lng": _safe_float(alert.longitude),
            "driver_live_lat": _safe_float(driver_location.latitude) if driver_location else None,
            "driver_live_lng": _safe_float(driver_location.longitude) if driver_location else None,
            "driver_updated_at": driver_location.updated_at.isoformat() if driver_location and driver_location.updated_at else "",
            "resolve_url": f"/admin-web/alerts/{alert.id}/resolve/",
            "tracking_url": "/admin-web/tracking/",
        }

    latest_active_alert = alerts.filter(status="ACTIVE").first()

    context = {
        "alerts": alerts,
        "query": query,
        "total_alerts": EmergencyAlert.objects.count(),
        "active_alerts": EmergencyAlert.objects.filter(status="ACTIVE").count(),
        "resolved_alerts": EmergencyAlert.objects.filter(status="RESOLVED").count(),
        "alert_modal_data_json": json.dumps(alert_modal_data, cls=DjangoJSONEncoder),
        "latest_active_alert_id": latest_active_alert.id if latest_active_alert else "",
    }
    return render(request, "admin_web/alerts.html", context)


@login_required
@admin_required
@require_GET
def alerts_data_api(request):
    alerts = EmergencyAlert.objects.select_related(
        "employee",
        "trip",
        "route_run",
        "route_run__route_template",
    ).order_by("-created_at")[:30]

    latest_driver_locations = _get_latest_driver_locations_map()
    data = []

    for alert in alerts:
        trip = getattr(alert, "trip", None)
        employee = getattr(alert, "employee", None)
        route_run = getattr(alert, "route_run", None)

        driver = None
        vehicle_number = "--"
        route_name = "--"

        if trip and trip.driver:
            driver = trip.driver
        elif route_run and getattr(route_run, "driver", None):
            driver = route_run.driver

        if trip and trip.vehicle:
            vehicle_number = trip.vehicle.vehicle_number
        elif route_run and getattr(route_run, "vehicle", None):
            vehicle_number = route_run.vehicle.vehicle_number

        if route_run and getattr(route_run, "route_template", None):
            route_name = route_run.route_template.name

        driver_location = latest_driver_locations.get(driver.id) if driver else None

        data.append({
            "id": alert.id,
            "employee_name": employee.username if employee else "--",
            "employee_phone": getattr(employee, "phone_number", "") if employee else "",
            "title": alert.title or "Emergency SOS",
            "message": alert.message or "",
            "status": alert.status or "ACTIVE",
            "created_at": alert.created_at.strftime("%d %b %Y, %I:%M %p") if alert.created_at else "",
            "trip_id": trip.id if trip else None,
            "trip_type": getattr(trip, "trip_type", "") if trip else "",
            "trip_status": getattr(trip, "status", "") if trip else "",
            "pickup_location": getattr(trip, "pickup_location", "") if trip else "",
            "drop_location": getattr(trip, "drop_location", "") if trip else "",
            "driver_name": driver.username if driver else "--",
            "driver_id": driver.id if driver else None,
            "vehicle_number": vehicle_number,
            "route_name": route_name,
            "alert_lat": _safe_float(alert.latitude),
            "alert_lng": _safe_float(alert.longitude),
            "driver_live_lat": _safe_float(driver_location.latitude) if driver_location else None,
            "driver_live_lng": _safe_float(driver_location.longitude) if driver_location else None,
            "driver_updated_at": driver_location.updated_at.isoformat() if driver_location and driver_location.updated_at else "",
            "resolve_url": f"/admin-web/alerts/{alert.id}/resolve/",
            "tracking_url": "/admin-web/tracking/",
        })

    return JsonResponse({
        "results": data,
        "total_alerts": EmergencyAlert.objects.count(),
        "active_alerts": EmergencyAlert.objects.filter(status="ACTIVE").count(),
        "resolved_alerts": EmergencyAlert.objects.filter(status="RESOLVED").count(),
    })


@login_required
@admin_required
@require_POST
def resolve_alert(request, alert_id):
    alert = get_object_or_404(EmergencyAlert, id=alert_id)
    alert.status = "RESOLVED"
    alert.save(update_fields=["status"])
    messages.success(request, "Alert resolved successfully.")
    return redirect("admin_web:alerts")


# =========================
# LIVE TRACKING
# =========================
@login_required
@admin_required
@require_GET
def live_tracking(request):
    locations = DriverLocation.objects.select_related("driver").order_by("-updated_at")
    return render(request, "admin_web/live_tracking.html", {
        "locations": locations
    })

def calculate_distance_km(lat1, lon1, lat2, lon2):
    lon1, lat1, lon2, lat2 = map(
        float,
        [lon1, lat1, lon2, lat2]
    )

    lon1, lat1, lon2, lat2 = map(
        radians,
        [lon1, lat1, lon2, lat2]
    )

    dlon = lon2 - lon1
    dlat = lat2 - lat1

    a = (
        sin(dlat / 2) ** 2
        + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    )

    c = 2 * asin(sqrt(a))

    return c * 6371

@login_required
@admin_required
@require_GET
def live_cab_cards_api(request):
    now = timezone.now()

    active_runs = RouteRun.objects.select_related(
        "driver",
        "vehicle",
        "route_template",
    ).prefetch_related(
        "stops",
    ).filter(
        completed_at__isnull=True,
    ).order_by("-started_at", "-created_at")

    latest_locations = _get_latest_driver_locations_map()

    data = []

    for run in active_runs:
        location = latest_locations.get(run.driver_id)

        total_stops = run.stops.count()
        completed_stops = run.stops.filter(is_picked=True).count()
        pending_stops = total_stops - completed_stops

        online_status = "OFFLINE"
        minutes_ago = None

        if location and location.updated_at:
            diff_minutes = (now - location.updated_at).total_seconds() / 60
            minutes_ago = round(diff_minutes)

            if diff_minutes <= 2:
                online_status = "ONLINE"
            elif diff_minutes <= 5:
                online_status = "IDLE"
            else:
                online_status = "OFFLINE"

        latest_speed = DriverLocationHistory.objects.filter(
            driver=run.driver,
            route_run=run,
        ).order_by("-recorded_at").first()

        speed = latest_speed.speed_kmph if latest_speed else 0
        moving_status = "MOVING" if speed and speed > 5 else "STOPPED"

        avg_speed = speed if speed and speed > 10 else 25

        remaining_stops_qs = run.stops.filter(
            is_picked=False
        ).order_by("stop_order")

        next_stop = remaining_stops_qs.first()

        eta_minutes = None
        eta_label = "Location unavailable"
        next_stop_name = "--"
        next_stop_location = "--"

        if next_stop:
            next_stop_name = next_stop.employee.username if next_stop.employee else "--"
            next_stop_location = next_stop.pickup_location or "--"

            if (
                location
                and location.latitude
                and location.longitude
                and next_stop.pickup_latitude
                and next_stop.pickup_longitude
            ):
                distance_km = calculate_distance_km(
                    location.latitude,
                    location.longitude,
                    next_stop.pickup_latitude,
                    next_stop.pickup_longitude,
                )

                eta_minutes = max(1, round((distance_km / avg_speed) * 60))
                eta_label = f"{eta_minutes} mins to next stop"

        estimated_completion_minutes = None
        estimated_completion_time = "--"

        if pending_stops > 0:
            base_minutes = eta_minutes or 0
            stop_buffer_minutes = pending_stops * 6
            estimated_completion_minutes = base_minutes + stop_buffer_minutes
            estimated_completion_time = (
                now + timezone.timedelta(minutes=estimated_completion_minutes)
            ).strftime("%I:%M %p")
        else:
            estimated_completion_minutes = 0
            estimated_completion_time = "Almost completed"

        data.append({
            "route_run_id": run.id,
            "driver_id": run.driver_id,
            "driver_name": run.driver.username if run.driver else "--",
            "vehicle_number": run.vehicle.vehicle_number if run.vehicle else "--",
            "vehicle_model": run.vehicle.vehicle_model if run.vehicle else "--",
            "route_name": run.route_template.name if run.route_template else "--",
            "trip_type": run.trip_type,
            "run_date": str(run.run_date),
            "online_status": online_status,
            "moving_status": moving_status,
            "speed_kmph": round(speed or 0, 1),
            "total_stops": total_stops,
            "completed_stops": completed_stops,
            "pending_stops": pending_stops,
            "progress_percent": round((completed_stops / total_stops) * 100) if total_stops else 0,
            "latitude": location.latitude if location else None,
            "longitude": location.longitude if location else None,
            "last_updated": location.updated_at.isoformat() if location and location.updated_at else "",
            "minutes_ago": minutes_ago,

            "next_stop_name": next_stop_name,
            "next_stop_location": next_stop_location,
            "eta_minutes": eta_minutes,
            "eta_label": eta_label,
            "estimated_completion_minutes": estimated_completion_minutes,
            "estimated_completion_time": estimated_completion_time,
        })

    return JsonResponse({
        "results": data,
        "count": len(data),
    })
# =========================
# REPORTS
# =========================
@login_required
@admin_required
@require_GET
def reports_page(request):
    selected_date = request.GET.get("date", "").strip()
    active_report = request.GET.get("report", "trip_by_driver").strip()

    today = timezone.localdate()
    parsed_date = parse_date(selected_date) if selected_date else today
    selected_date = str(parsed_date)

    trips = Trip.objects.select_related(
        "employee",
        "driver",
        "vehicle",
        "route_run",
        "route_run__route_template",
    ).filter(
        trip_date=parsed_date
    ).order_by("driver__username", "pickup_time")

    total_trips = trips.count()
    completed_trips = trips.filter(status=Trip.STATUS_COMPLETED).count()
    started_trips = trips.filter(status=Trip.STATUS_STARTED).count()
    cancelled_trips_count = trips.filter(status=Trip.STATUS_CANCELLED).count()
    assigned_trips = trips.filter(status=Trip.STATUS_ASSIGNED).count()

    cancelled_trips = trips.filter(status=Trip.STATUS_CANCELLED)

    no_show_stops = RouteRunStop.objects.select_related(
        "employee",
        "route_run",
        "route_run__driver",
        "route_run__vehicle",
        "route_run__route_template",
    ).filter(
        route_run__run_date=parsed_date,
        is_no_show=True,
    ).order_by("route_run__trip_type", "stop_order")

    leave_reports = EmployeeLeave.objects.select_related(
        "employee"
    ).filter(
        leave_date=parsed_date
    ).order_by("employee__username")

    sos_reports = EmergencyAlert.objects.select_related(
        "employee",
        "trip",
        "route_run",
        "route_run__driver",
        "route_run__vehicle",
        "route_run__route_template",
    ).filter(
        created_at__date=parsed_date
    ).order_by("-created_at")

    review_reports = Review.objects.select_related(
        "employee",
        "trip",
        "trip__driver",
        "trip__vehicle",
        "trip__route_run",
        "trip__route_run__route_template",
    ).filter(
        created_at__date=parsed_date
    ).order_by("-created_at")

    late_employees = RouteRunStop.objects.select_related(
        "employee",
        "route_run",
        "route_run__driver",
        "route_run__vehicle",
        "route_run__route_template",
    ).filter(
        route_run__run_date=parsed_date,
        waiting_started_at__isnull=False,
    ).order_by("route_run__trip_type", "stop_order")

    # =========================
    # DRIVER SPEED REPORTS
    # =========================

    speed_reports = DriverLocationHistory.objects.select_related(
        "driver",
        "route_run",
        "route_run__route_template",
    ).filter(
        recorded_at__date=parsed_date
    ).order_by(
        "-speed_kmph",
        "-recorded_at",
    )
    # =========================
    # DRIVER ROUTE TIMELINE REPORT
    # =========================

    route_runs = RouteRun.objects.select_related(
        "driver",
        "vehicle",
        "route_template",
    ).prefetch_related(
        "stops__employee",
    ).filter(
        run_date=parsed_date
    ).order_by("driver__username", "started_at", "created_at")

    route_trips = Trip.objects.select_related(
        "employee",
        "driver",
        "vehicle",
        "route_run",
    ).filter(
        route_run__in=route_runs
    )

    trip_map = {
        (trip.route_run_id, trip.employee_id): trip
        for trip in route_trips
    }

    cancellations = TripCancellation.objects.select_related(
        "trip",
        "trip__employee",
    ).filter(
        trip__in=route_trips
    )

    cancel_map = {
        cancel.trip_id: cancel
        for cancel in cancellations
    }

    driver_timeline_reports = []

    for run in route_runs:
        stops_data = []

        for stop in run.stops.all().order_by("stop_order"):
            trip = trip_map.get((run.id, stop.employee_id))
            cancellation = cancel_map.get(trip.id) if trip else None

            waiting_minutes = None
            if stop.waiting_started_at and stop.picked_at:
                waiting_minutes = round(
                    (stop.picked_at - stop.waiting_started_at).total_seconds() / 60
                )

            if stop.is_no_show:
                stop_status = "NO_SHOW"
            elif cancellation:
                stop_status = "CANCELLED"
            elif stop.is_picked:
                stop_status = "PICKED"
            else:
                stop_status = "PENDING"

            stops_data.append({
                "stop_order": stop.stop_order,
                "employee_name": stop.employee.username if stop.employee else "--",
                "pickup_location": stop.pickup_location or "--",
                "reached_time": stop.waiting_started_at,
                "picked_time": stop.picked_at,
                "waiting_minutes": waiting_minutes,
                "is_no_show": stop.is_no_show,
                "is_picked": stop.is_picked,
                "status": stop_status,
                "cancel_reason": cancellation.reason if cancellation else "",
                "trip_status": trip.status if trip else "--",
            })

        total_duration_minutes = None
        if run.started_at and run.completed_at:
            total_duration_minutes = round(
                (run.completed_at - run.started_at).total_seconds() / 60
            )

        notification_count = Notification.objects.filter(
            route_run=run
        ).count()

        driver_timeline_reports.append({
            "route_run_id": run.id,
            "route_name": run.route_template.name if run.route_template else "Manual Route",
            "driver_name": run.driver.username if run.driver else "--",
            "vehicle_number": run.vehicle.vehicle_number if run.vehicle else "--",
            "vehicle_model": run.vehicle.vehicle_model if run.vehicle else "--",
            "trip_type": run.trip_type,
            "status": "COMPLETED" if run.completed_at else "STARTED" if run.started_at else "ASSIGNED",
            "started_at": run.started_at,
            "completed_at": run.completed_at,
            "total_duration_minutes": total_duration_minutes,
            "total_stops": len(stops_data),
            "completed_stops": len([s for s in stops_data if s["status"] == "PICKED"]),
            "no_show_count": len([s for s in stops_data if s["status"] == "NO_SHOW"]),
            "cancelled_count": len([s for s in stops_data if s["status"] == "CANCELLED"]),
            "notification_count": notification_count,
            "stops": stops_data,
        })

    # =========================
    # DRIVER SUMMARY
    # =========================

    driver_map = OrderedDict()

    for trip in trips:

        driver_id = trip.driver_id or "unassigned"
        driver_name = (
            trip.driver.username
            if trip.driver
            else "Unassigned Driver"
        )

        if driver_id not in driver_map:

            driver_map[driver_id] = {
                "driver_name": driver_name,
                "vehicle_number": (
                    trip.vehicle.vehicle_number
                    if trip.vehicle
                    else "--"
                ),
                "total": 0,
                "assigned": 0,
                "started": 0,
                "completed": 0,
                "cancelled": 0,
                "pickup": 0,
                "drop": 0,
                "trips": [],
            }

        row = driver_map[driver_id]

        row["total"] += 1

        if trip.status == Trip.STATUS_ASSIGNED:
            row["assigned"] += 1

        elif trip.status == Trip.STATUS_STARTED:
            row["started"] += 1

        elif trip.status == Trip.STATUS_COMPLETED:
            row["completed"] += 1

        elif trip.status == Trip.STATUS_CANCELLED:
            row["cancelled"] += 1

        if trip.trip_type == Trip.TRIP_TYPE_PICKUP:
            row["pickup"] += 1

        elif trip.trip_type == Trip.TRIP_TYPE_DROP:
            row["drop"] += 1

        row["trips"].append(trip)

    driver_reports = list(driver_map.values())

    # =========================
    # UNASSIGNED EMPLOYEES
    # =========================

    all_employee_ids = set(
        User.objects.filter(
            role="EMPLOYEE",
            is_active=True,
        ).values_list(
            "id",
            flat=True,
        )
    )

    assigned_employee_ids = set(
        trips.values_list(
            "employee_id",
            flat=True,
        )
    )

    unassigned_employees = User.objects.filter(
        role="EMPLOYEE",
        is_active=True,
    ).exclude(
        id__in=assigned_employee_ids
    ).order_by("username")

    # =========================
    # UNASSIGNED DRIVERS
    # =========================

    all_driver_ids = set(
        User.objects.filter(
            role="DRIVER",
            is_active=True,
        ).values_list(
            "id",
            flat=True,
        )
    )

    assigned_driver_ids = set(
        trips.exclude(
            driver_id__isnull=True
        ).values_list(
            "driver_id",
            flat=True,
        )
    )

    unassigned_drivers = User.objects.filter(
        role="DRIVER",
        is_active=True,
    ).exclude(
        id__in=assigned_driver_ids
    ).order_by("username")

    # =========================
    # CONTEXT
    # =========================

    context = {
        "selected_date": selected_date,
        "active_report": active_report,

        "total_trips": total_trips,
        "completed_trips": completed_trips,
        "started_trips": started_trips,
        "cancelled_trips_count": cancelled_trips_count,
        "assigned_trips": assigned_trips,

        "trips": trips,
        "driver_reports": driver_reports,
        "cancelled_trips": cancelled_trips,
        "no_show_stops": no_show_stops,
        "leave_reports": leave_reports,
        "sos_reports": sos_reports,
        "review_reports": review_reports,
        "late_employees": late_employees,
        "unassigned_employees": unassigned_employees,
        "unassigned_drivers": unassigned_drivers,

        # ✅ SPEED REPORT
        "speed_reports": speed_reports,
        "driver_timeline_reports": driver_timeline_reports,
    }

    return render(
        request,
        "admin_web/reports.html",
        context
    )

@login_required
@admin_required
@require_GET
def route_analytics_page(request):
    date_filter = request.GET.get("date", "").strip()

    today = timezone.localdate()
    selected_date = parse_date(date_filter) if date_filter else today

    route_runs = RouteRun.objects.select_related(
        "route_template",
        "driver",
        "vehicle",
    ).prefetch_related(
        "stops__employee",
        "trips",
    ).filter(
        run_date=selected_date,
    ).order_by("trip_type", "route_template__name")

    route_rows = []

    for run in route_runs:
        stops = list(run.stops.all())
        trips = list(run.trips.all())

        total_stops = len(stops)
        completed_stops = len([s for s in stops if getattr(s, "is_picked", False)])
        no_show_count = len([s for s in stops if getattr(s, "is_no_show", False)])

        total_trips = len(trips)
        completed_trips = len([t for t in trips if t.status == Trip.STATUS_COMPLETED])
        cancelled_trips = len([t for t in trips if t.status == Trip.STATUS_CANCELLED])

        completion_percent = round((completed_stops / total_stops) * 100, 1) if total_stops else 0
        trip_completion_percent = round((completed_trips / total_trips) * 100, 1) if total_trips else 0

        duration_minutes = None
        if run.started_at and run.completed_at:
            duration_minutes = int((run.completed_at - run.started_at).total_seconds() / 60)

        efficiency_score = 100
        efficiency_score -= no_show_count * 8
        efficiency_score -= cancelled_trips * 5

        if duration_minutes:
            limit = 120 if run.trip_type == Trip.TRIP_TYPE_PICKUP else 90
            if duration_minutes > limit:
                efficiency_score -= 15

        efficiency_score = max(0, min(100, efficiency_score))

        if efficiency_score >= 85:
            health = "EXCELLENT"
            health_label = "Excellent"
        elif efficiency_score >= 60:
            health = "MODERATE"
            health_label = "Moderate"
        else:
            health = "CRITICAL"
            health_label = "Critical"

        route_rows.append({
            "id": run.id,
            "route_name": run.route_template.name if run.route_template else "Manual Route",
            "trip_type": run.trip_type,
            "driver_name": run.driver.username if run.driver else "--",
            "vehicle_number": run.vehicle.vehicle_number if run.vehicle else "--",
            "total_stops": total_stops,
            "completed_stops": completed_stops,
            "no_show_count": no_show_count,
            "total_trips": total_trips,
            "completed_trips": completed_trips,
            "cancelled_trips": cancelled_trips,
            "completion_percent": completion_percent,
            "trip_completion_percent": trip_completion_percent,
            "duration_minutes": duration_minutes or 0,
            "efficiency_score": efficiency_score,
            "health": health,
            "health_label": health_label,
        })

    route_rows = sorted(
        route_rows,
        key=lambda x: (x["efficiency_score"], x["completion_percent"]),
        reverse=True,
    )

    most_delayed_routes = sorted(
        [r for r in route_rows if r["duration_minutes"] > 0],
        key=lambda x: x["duration_minutes"],
        reverse=True,
    )[:5]

    critical_routes = [r for r in route_rows if r["health"] == "CRITICAL"]

    context = {
        "date_filter": selected_date.strftime("%Y-%m-%d"),
        "routes": route_rows,
        "total_routes": len(route_rows),
        "excellent_routes": len([r for r in route_rows if r["health"] == "EXCELLENT"]),
        "moderate_routes": len([r for r in route_rows if r["health"] == "MODERATE"]),
        "critical_routes_count": len(critical_routes),
        "most_delayed_routes": most_delayed_routes,
        "critical_routes": critical_routes[:5],
    }

    return render(request, "admin_web/route_analytics.html", context)
@login_required
@admin_required
def export_reports_excel(request):
    trips = Trip.objects.select_related(
        "employee",
        "driver",
        "vehicle",
        "route_run",
        "route_run__route_template",
    ).order_by("-pickup_time")

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Trip Reports"

    # Header
    headers = [
        "Employee",
        "Driver",
        "Vehicle",
        "Route",
        "Trip Type",
        "Trip Date",
        "Pickup Time",
        "Start Time",
        "End Time",
        "Status",
    ]

    ws.append(headers)

    # Data
    for trip in trips:
        ws.append([
            trip.employee.username if trip.employee else "",
            trip.driver.username if trip.driver else "",
            trip.vehicle.vehicle_number if trip.vehicle else "",
            trip.route_run.route_template.name if trip.route_run and trip.route_run.route_template else "Manual",
            trip.trip_type,
            str(trip.trip_date),
            str(trip.pickup_time),
            str(trip.start_time),
            str(trip.end_time),
            trip.status,
        ])

    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = 'attachment; filename="trip_report.xlsx"'

    wb.save(response)
    return response


@login_required
@admin_required
@require_GET
def driver_performance_page(request):
    date_filter = request.GET.get("date", "").strip()
    period = request.GET.get("period", "daily").strip().lower()

    if period not in ["daily", "weekly", "monthly"]:
        period = "daily"

    selected_date = parse_date(date_filter) if date_filter else timezone.localdate()

    if period == "weekly":
        start_date = selected_date - timezone.timedelta(days=selected_date.weekday())
        end_date = start_date + timezone.timedelta(days=6)
        period_label = f"Weekly: {start_date.strftime('%d %b')} - {end_date.strftime('%d %b %Y')}"
    elif period == "monthly":
        start_date = selected_date.replace(day=1)
        if start_date.month == 12:
            next_month = start_date.replace(year=start_date.year + 1, month=1, day=1)
        else:
            next_month = start_date.replace(month=start_date.month + 1, day=1)
        end_date = next_month - timezone.timedelta(days=1)
        period_label = f"Monthly: {start_date.strftime('%B %Y')}"
    else:
        start_date = selected_date
        end_date = selected_date
        period_label = f"Daily: {selected_date.strftime('%d %b %Y')}"

    trips = Trip.objects.select_related("driver", "vehicle").filter(
        trip_date__range=[start_date, end_date]
    )

    speed_history = DriverLocationHistory.objects.select_related(
        "driver",
        "route_run",
    ).filter(
        recorded_at__date__range=[start_date, end_date]
    )

    drivers = User.objects.filter(role="DRIVER").annotate(
        total_trips=Count(
            "driver_trips",
            filter=Q(driver_trips__in=trips),
            distinct=True,
        ),
        completed_trips=Count(
            "driver_trips",
            filter=Q(
                driver_trips__in=trips,
                driver_trips__status=Trip.STATUS_COMPLETED,
            ),
            distinct=True,
        ),
        started_trips=Count(
            "driver_trips",
            filter=Q(
                driver_trips__in=trips,
                driver_trips__status=Trip.STATUS_STARTED,
            ),
            distinct=True,
        ),
        cancelled_trips=Count(
            "driver_trips",
            filter=Q(
                driver_trips__in=trips,
                driver_trips__status=Trip.STATUS_CANCELLED,
            ),
            distinct=True,
        ),
        assigned_trips=Count(
            "driver_trips",
            filter=Q(
                driver_trips__in=trips,
                driver_trips__status=Trip.STATUS_ASSIGNED,
            ),
            distinct=True,
        ),
    ).order_by("-total_trips")

    speed_stats = (
        speed_history
        .values("driver_id")
        .annotate(
            avg_speed=Avg("speed_kmph"),
            max_speed=Max("speed_kmph"),
            overspeed_count=Count(
                "id",
                filter=Q(is_overspeed=True),
            ),
            speed_records=Count("id"),
        )
    )

    speed_map = {
        item["driver_id"]: item
        for item in speed_stats
    }

    driver_rows = []

    for driver in drivers:
        stats = speed_map.get(driver.id, {})

        avg_speed = round(stats.get("avg_speed") or 0, 1)
        max_speed = round(stats.get("max_speed") or 0, 1)
        overspeed_count = stats.get("overspeed_count") or 0
        speed_records = stats.get("speed_records") or 0

        completion_percent = 0
        if driver.total_trips:
            completion_percent = round(
                (driver.completed_trips / driver.total_trips) * 100,
                1,
            )

        safety_score = 100
        safety_score -= overspeed_count * 5
        safety_score -= driver.cancelled_trips * 3
        safety_score = max(0, min(100, safety_score))

        if safety_score >= 85:
            badge = "SAFE"
            badge_label = "Safe Driver"
        elif safety_score >= 60:
            badge = "MODERATE"
            badge_label = "Moderate Risk"
        else:
            badge = "RISKY"
            badge_label = "Risky Driver"

        driver_rows.append({
            "id": driver.id,
            "username": driver.username,
            "phone_number": getattr(driver, "phone_number", "") or "--",
            "total_trips": driver.total_trips,
            "completed_trips": driver.completed_trips,
            "started_trips": driver.started_trips,
            "cancelled_trips": driver.cancelled_trips,
            "assigned_trips": driver.assigned_trips,
            "completion_percent": completion_percent,
            "avg_speed": avg_speed,
            "max_speed": max_speed,
            "overspeed_count": overspeed_count,
            "speed_records": speed_records,
            "safety_score": safety_score,
            "badge": badge,
            "badge_label": badge_label,
        })

    driver_rows = sorted(
        driver_rows,
        key=lambda x: (x["safety_score"], x["completion_percent"]),
        reverse=True,
    )

    leaderboard = driver_rows[:5]

    risky_drivers_panel = sorted(
        [d for d in driver_rows if d["badge"] == "RISKY" or d["overspeed_count"] > 0],
        key=lambda x: (x["overspeed_count"], -x["safety_score"]),
        reverse=True,
    )[:5]

    avg_speed_chart_labels = [d["username"] for d in driver_rows[:10]]
    avg_speed_chart_values = [d["avg_speed"] for d in driver_rows[:10]]

    context = {
        "drivers": driver_rows,
        "leaderboard": leaderboard,
        "risky_drivers_panel": risky_drivers_panel,
        "avg_speed_chart_labels": avg_speed_chart_labels,
        "avg_speed_chart_values": avg_speed_chart_values,
        "date_filter": selected_date.strftime("%Y-%m-%d"),
        "period": period,
        "period_label": period_label,
        "start_date": start_date,
        "end_date": end_date,
        "total_drivers": len(driver_rows),
        "safe_drivers": len([d for d in driver_rows if d["badge"] == "SAFE"]),
        "moderate_drivers": len([d for d in driver_rows if d["badge"] == "MODERATE"]),
        "risky_drivers": len([d for d in driver_rows if d["badge"] == "RISKY"]),
        "total_overspeed": sum(d["overspeed_count"] for d in driver_rows),
    }

    return render(request, "admin_web/driver_performance.html", context)

@login_required
@admin_required
@require_GET
def assigned_trips_page(request):
    query = request.GET.get("q", "").strip()
    date_filter = request.GET.get("date", "").strip()
    trip_type_filter = request.GET.get("trip_type", "").strip().upper()

    today = timezone.localdate()
    tomorrow = today + timezone.timedelta(days=1)

    runs = RouteRun.objects.select_related(
        "route_template",
        "driver",
        "vehicle",
    ).prefetch_related(
        "stops__employee",
        "trips__employee",
    ).filter(
        trips__status__in=[
            Trip.STATUS_ASSIGNED,
            Trip.STATUS_STARTED,
        ],
        run_date__gte=today,
    ).distinct()

    parsed_date = parse_date(date_filter) if date_filter else None
    if parsed_date:
        runs = runs.filter(run_date=parsed_date)

    if trip_type_filter in ["PICKUP", "DROP"]:
        runs = runs.filter(trip_type=trip_type_filter)

    if query:
        runs = runs.filter(
            Q(route_template__name__icontains=query)
            | Q(driver__username__icontains=query)
            | Q(vehicle__vehicle_number__icontains=query)
            | Q(stops__employee__username__icontains=query)
            | Q(stops__pickup_location__icontains=query)
        ).distinct()

    runs = runs.order_by("run_date", "trip_type", "route_template__name")

    grouped = OrderedDict()
    total_assigned = 0
    total_started = 0

    for run in runs:
        active_trips = list(
            run.trips.filter(
                status__in=[Trip.STATUS_ASSIGNED, Trip.STATUS_STARTED]
            ).select_related("employee")
        )

        if not active_trips:
            continue

        trip_map = {trip.employee_id: trip for trip in active_trips}

        assigned_count = sum(1 for trip in active_trips if trip.status == Trip.STATUS_ASSIGNED)
        started_count = sum(1 for trip in active_trips if trip.status == Trip.STATUS_STARTED)

        total_assigned += assigned_count
        total_started += started_count

        stops = list(run.stops.select_related("employee").order_by("stop_order"))

        completed_employees = len([s for s in stops if getattr(s, "is_picked", False)])
        total_employees = len([s for s in stops if s.employee_id in trip_map])
        completion_percent = int((completed_employees / total_employees) * 100) if total_employees else 0

        current_stop = next(
            (s for s in stops if s.employee_id in trip_map and not getattr(s, "is_picked", False)),
            None,
        )

        current_stop_name = (
            current_stop.employee.username
            if current_stop and current_stop.employee
            else "Completed" if completion_percent == 100 else "Not started"
        )

        eta_text = "--"
        if started_count:
            remaining = total_employees - completed_employees
            eta_text = f"{remaining * 7} min" if remaining > 0 else "Completed"

        employees = []
        for stop in stops:
            trip = trip_map.get(stop.employee_id)
            if not trip:
                continue

            employees.append({
                "trip_id": trip.id,
                "employee_name": stop.employee.username if stop.employee else "--",
                "pickup_location": stop.pickup_location or "--",
                "drop_location": trip.drop_location or "Office",
                "status": trip.status,
                "is_picked": getattr(stop, "is_picked", False),
                "is_no_show": getattr(stop, "is_no_show", False),
                "is_current": bool(current_stop and stop.id == current_stop.id),
                "stop_order": stop.stop_order,
            })

        first_trip = active_trips[0]

        card = {
            "id": run.id,
            "route_name": run.route_template.name if run.route_template else "Manual Route",
            "driver_name": run.driver.username if run.driver else "--",
            "vehicle_number": run.vehicle.vehicle_number if run.vehicle else "--",
            "trip_type": run.trip_type,
            "run_date": run.run_date,
            "pickup_time": first_trip.pickup_time if first_trip else None,
            "status": Trip.STATUS_STARTED if started_count else Trip.STATUS_ASSIGNED,
            "total_employees": total_employees,
            "assigned_count": assigned_count,
            "started_count": started_count,
            "completed_employees": completed_employees,
            "completion_percent": completion_percent,
            "current_stop_name": current_stop_name,
            "eta_text": eta_text,
            "employees": employees,
            "health_status": (
                "CRITICAL"
                if card.get("is_overspeed")
                else "MODERATE"
                if card.get("is_delayed")
                else "EXCELLENT"
            ),
            "completion_percent": int(
                (started_count / len(employees)) * 100
            ) if employees else 0,

            "is_live": started_count > 0,

            "is_delayed": False,

            "is_overspeed": False,
        }

        if run.run_date == today:
            label = "Today's Date"
        elif run.run_date == tomorrow:
            label = "Tomorrow's Date"
        else:
            label = run.run_date.strftime("%d %b %Y")

        if label not in grouped:
            grouped[label] = {
                "date_value": run.run_date,
                "pickup_cards": [],
                "drop_cards": [],
            }

        if run.trip_type == Trip.TRIP_TYPE_PICKUP:
            grouped[label]["pickup_cards"].append(card)
        else:
            grouped[label]["drop_cards"].append(card)

    context = {
        "date_sections": grouped.items(),
        "query": query,
        "date_filter": date_filter,
        "trip_type_filter": trip_type_filter,
        "total_assigned": total_assigned,
        "total_started": total_started,
        "total_active": total_assigned + total_started,
    }

    return render(request, "admin_web/assigned_trips.html", context)

@login_required
@admin_required
@require_POST
def cancel_route_run_trips(request, route_run_id):
    route_run = get_object_or_404(RouteRun, id=route_run_id)

    updated = Trip.objects.filter(
        route_run=route_run,
        status__in=[Trip.STATUS_ASSIGNED, Trip.STATUS_STARTED],
    ).update(status=Trip.STATUS_CANCELLED)

    messages.success(request, f"{updated} trip(s) cancelled for this route.")
    return redirect("admin_web:assigned_trips")


@login_required
@admin_required
def cancel_date_trips(request, date):
    parsed_date = parse_date(date)

    if not parsed_date:
        messages.error(request, "Invalid date")
        return redirect("admin_web:assigned_trips")

    # Cancel ALL trips for that date
    Trip.objects.filter(
        trip_date=parsed_date
    ).update(status=Trip.STATUS_CANCELLED)

    messages.success(request, f"All trips cancelled for {parsed_date}")

    return redirect("admin_web:assigned_trips")

@login_required
@admin_required
@require_GET
def trip_history_page(request):
    query = request.GET.get("q", "").strip()
    date_filter = request.GET.get("date", "").strip()
    status_filter = request.GET.get("status", "").strip().upper()
    trip_type_filter = request.GET.get("trip_type", "").strip().upper()

    runs = RouteRun.objects.select_related(
        "route_template",
        "driver",
        "vehicle",
    ).prefetch_related(
        "stops__employee",
        "trips__employee",
    ).filter(
        trips__status__in=[
            Trip.STATUS_COMPLETED,
            Trip.STATUS_CANCELLED,
        ]
    ).distinct()

    parsed_date = parse_date(date_filter) if date_filter else None
    if parsed_date:
        runs = runs.filter(run_date=parsed_date)

    if trip_type_filter in ["PICKUP", "DROP"]:
        runs = runs.filter(trip_type=trip_type_filter)

    if query:
        runs = runs.filter(
            Q(route_template__name__icontains=query)
            | Q(driver__username__icontains=query)
            | Q(vehicle__vehicle_number__icontains=query)
            | Q(stops__employee__username__icontains=query)
            | Q(stops__pickup_location__icontains=query)
        ).distinct()

    runs = runs.order_by("-run_date", "trip_type", "route_template__name")

    grouped = OrderedDict()
    total_completed = 0
    total_cancelled = 0

    for run in runs:
        history_trips = run.trips.filter(
            status__in=[Trip.STATUS_COMPLETED, Trip.STATUS_CANCELLED]
        ).select_related("employee")

        if status_filter in [Trip.STATUS_COMPLETED, Trip.STATUS_CANCELLED]:
            history_trips = history_trips.filter(status=status_filter)

        history_trips = list(history_trips)

        if not history_trips:
            continue

        trip_map = {trip.employee_id: trip for trip in history_trips}

        completed_count = sum(1 for trip in history_trips if trip.status == Trip.STATUS_COMPLETED)
        cancelled_count = sum(1 for trip in history_trips if trip.status == Trip.STATUS_CANCELLED)

        total_completed += completed_count
        total_cancelled += cancelled_count

        employees = []
        for stop in run.stops.all():
            trip = trip_map.get(stop.employee_id)
            if not trip:
                continue

            employees.append({
                "trip_id": trip.id,
                "employee_name": stop.employee.username if stop.employee else "--",
                "pickup_location": stop.pickup_location or "--",
                "drop_location": trip.drop_location or "Office",
                "status": trip.status,
                "start_time": trip.start_time,
                "end_time": trip.end_time,
            })

        first_trip = history_trips[0]

        if completed_count and cancelled_count:
            run_status = "MIXED"
        elif completed_count:
            run_status = Trip.STATUS_COMPLETED
        else:
            run_status = Trip.STATUS_CANCELLED

        card = {
            "id": run.id,
            "route_name": run.route_template.name if run.route_template else "Manual Route",
            "driver_name": run.driver.username if run.driver else "--",
            "vehicle_number": run.vehicle.vehicle_number if run.vehicle else "--",
            "trip_type": run.trip_type,
            "run_date": run.run_date,
            "pickup_time": first_trip.pickup_time if first_trip else None,
            "status": run_status,
            "total_employees": len(employees),
            "completed_count": completed_count,
            "cancelled_count": cancelled_count,
            "employees": employees,
        }

        label = run.run_date.strftime("%d %b %Y")

        if label not in grouped:
            grouped[label] = {
                "date_value": run.run_date,
                "pickup_cards": [],
                "drop_cards": [],
            }

        if run.trip_type == Trip.TRIP_TYPE_PICKUP:
            grouped[label]["pickup_cards"].append(card)
        else:
            grouped[label]["drop_cards"].append(card)

    context = {
        "date_sections": grouped.items(),
        "query": query,
        "date_filter": date_filter,
        "status_filter": status_filter,
        "trip_type_filter": trip_type_filter,
        "total_completed": total_completed,
        "total_cancelled": total_cancelled,
        "total_history": total_completed + total_cancelled,
    }

    return render(request, "admin_web/trip_history.html", context)

@login_required
@admin_required
@require_POST
def start_trip(request, trip_id):
    trip = get_object_or_404(Trip, id=trip_id)

    if trip.status != Trip.STATUS_ASSIGNED:
        messages.error(request, "Only assigned trip can be started.")
        return redirect("admin_web:trips")

    trip.status = Trip.STATUS_STARTED
    trip.start_time = timezone.now()
    trip.save(update_fields=["status", "start_time"])

    messages.success(request, "Trip started successfully.")
    return redirect("admin_web:trips")


@login_required
@admin_required
@require_POST
def complete_trip(request, trip_id):
    trip = get_object_or_404(Trip, id=trip_id)

    if trip.status != Trip.STATUS_STARTED:
        messages.error(request, "Only started trip can be completed.")
        return redirect("admin_web:trips")

    trip.status = Trip.STATUS_COMPLETED
    trip.end_time = timezone.now()
    trip.save(update_fields=["status", "end_time"])

    messages.success(request, "Trip completed successfully.")
    return redirect("admin_web:trips")


@login_required
@admin_required
@require_POST
def restore_trip(request, trip_id):
    trip = get_object_or_404(Trip, id=trip_id)

    if trip.status != Trip.STATUS_CANCELLED:
        messages.warning(request, "Only cancelled trips can be restored.")
        return _redirect_back(request, "admin_web:trip_history")                        

    trip.status = Trip.STATUS_ASSIGNED
    trip.save(update_fields=["status"])

    messages.success(request, "Trip restored successfully.")
    return _redirect_back(request, "admin_web:trip_history")

@login_required
@admin_required
@require_GET
def employee_route_search(request, employee_id):
    employee = get_object_or_404(
        User,
        id=employee_id,
        role="EMPLOYEE",
    )

    employee_lat = employee.pickup_latitude
    employee_lng = employee.pickup_longitude

    route_suggestions = []

    routes = RouteTemplate.objects.select_related(
        "driver",
        "vehicle",
    ).prefetch_related(
        "stops",
        "stops__employee",
    ).order_by("name")

    for route in routes:
        stops = list(route.stops.all())
        seat_count = route.vehicle.seat_count if route.vehicle else 0
        used_seats = len(stops)
        seats_left = max(seat_count - used_seats, 0)

        nearest_stop = None
        nearest_distance = None

        for stop in stops:
            distance = _distance_km(
                employee_lat,
                employee_lng,
                stop.pickup_latitude,
                stop.pickup_longitude,
            )

            if distance is None:
                continue

            if nearest_distance is None or distance < nearest_distance:
                nearest_distance = distance
                nearest_stop = stop

        if nearest_distance is None:
            nearest_distance = 9999

        score = nearest_distance

        if seats_left <= 0:
            score += 1000

        route_suggestions.append({
            "route": route,
            "driver": route.driver,
            "vehicle": route.vehicle,
            "seat_count": seat_count,
            "used_seats": used_seats,
            "seats_left": seats_left,
            "nearest_stop": nearest_stop,
            "nearest_distance": nearest_distance,
            "score": score,
        })

    route_suggestions = sorted(
        route_suggestions,
        key=lambda item: item["score"],
    )

    return render(request, "admin_web/employee_route_search.html", {
        "employee": employee,
        "route_suggestions": route_suggestions,
        "employee_lat": employee_lat,
        "employee_lng": employee_lng,
    })
@login_required
@admin_required
@require_POST
def assign_employee_to_route(request, employee_id, route_id):
    employee = get_object_or_404(
        User,
        id=employee_id,
        role="EMPLOYEE",
    )

    route = get_object_or_404(
        RouteTemplate.objects.select_related("vehicle"),
        id=route_id,
    )

    if not employee.pickup_location:
        messages.error(request, "Employee pickup location is missing.")
        return redirect("admin_web:employee_route_search", employee_id=employee.id)

    seat_count = route.vehicle.seat_count if route.vehicle else 0
    used_seats = route.stops.exclude(employee=employee).count()

    if used_seats >= seat_count:
        messages.error(request, "This route is already full. No seats left.")
        return redirect("admin_web:employee_route_search", employee_id=employee.id)

    with transaction.atomic():
        old_stops = RouteStop.objects.filter(employee=employee)

        for old_stop in old_stops:
            old_route = old_stop.route
            old_stop.delete()

            remaining_stops = old_route.stops.order_by("stop_order")
            for index, stop in enumerate(remaining_stops, start=1):
                if stop.stop_order != index:
                    stop.stop_order = index
                    stop.save(update_fields=["stop_order"])

        last_stop = route.stops.order_by("-stop_order").first()
        next_order = (last_stop.stop_order + 1) if last_stop else 1

        RouteStop.objects.create(
            route=route,
            employee=employee,
            pickup_location=employee.pickup_location,
            pickup_latitude=employee.pickup_latitude,
            pickup_longitude=employee.pickup_longitude,
            stop_order=next_order,
        )

    messages.success(
        request,
        f"{employee.username} assigned to {route.name} successfully.",
    )

    return redirect(f"{reverse('admin_web:routes')}?edit_route_id={route.id}")