import json
from datetime import datetime, timedelta, time
from rest_framework.test import APIRequestFactory, force_authenticate
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import Q
from django.db import transaction
import math
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from django.db import transaction
from django.db.models import Count, Q, Avg, Max
from trips.models import RouteRunStop
from accounts.models import PickupLocationChangeRequest
from trips.utils.notification import send_push_notification
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import resolve, reverse
from django.utils import timezone
from django.db.models import Count, Q, Avg, Max
from django.utils.dateparse import parse_date
from collections import OrderedDict
from django.contrib.auth import authenticate, login, logout
from math import radians, cos, sin, asin, sqrt
from django.views.decorators.http import require_GET, require_POST
import openpyxl
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import (
    get_object_or_404,
    redirect,
    render,
)
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
    pending_location_requests = (
        PickupLocationChangeRequest.objects
        .filter(
            status=PickupLocationChangeRequest.STATUS_PENDING
        )
        .count()
    )

    # This will later include:
    # location requests
    # driver change requests
    # route change requests
    # employee account approvals
    # driver account approvals

    pending_employee_accounts = User.objects.filter(
        role=User.Role.EMPLOYEE,
        account_status=User.ACCOUNT_STATUS_PENDING,
    ).count()

    pending_driver_accounts = User.objects.filter(
        role=User.Role.DRIVER,
        account_status=User.ACCOUNT_STATUS_PENDING,
    ).count()

    pending_requests_count = (
        pending_location_requests
        + pending_employee_accounts
        + pending_driver_accounts
    )
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

    # =========================
    # LATE CAB SLA
    # =========================
    # PICKUP:
    #   Mon-Thu -> route should be completed / reach office by 5:30 PM
    #   Friday  -> route should be completed / reach office by 7:00 PM
    #
    # DROP:
    #   route should be completed within 2 hours from started_at
    #
    # We build a list instead of only checking "not started", because admin
    # wants to see actual completion delay in minutes.
    now_local = timezone.localtime()
    tz = timezone.get_current_timezone()

    if today.weekday() == 4:  # Friday
        pickup_deadline_time = time(19, 0)
    else:  # Monday-Thursday (and fallback for any manually-created weekday data)
        pickup_deadline_time = time(17, 30)

    pickup_deadline = timezone.make_aware(
        datetime.combine(today, pickup_deadline_time),
        tz,
    )

    today_route_runs = (
        RouteRun.objects
        .select_related("route_template", "driver", "vehicle")
        .prefetch_related("trips")
        .filter(run_date=today)
        .order_by("id")
    )

    late_cabs = []

    for run in today_route_runs:
        trip_type = (
            run.trips.values_list("trip_type", flat=True).first()
            or getattr(run, "trip_type", "")
            or ""
        )
        trip_type = str(trip_type).upper()
        run.dashboard_trip_type = trip_type or "--"
        run.late_minutes = 0
        run.late_reason = ""
        run.expected_complete_at = None
        run.late_reference_at = None

        # PICKUP = office arrival/completion deadline
        if trip_type == Trip.TRIP_TYPE_PICKUP:
            run.expected_complete_at = pickup_deadline

            if run.completed_at:
                completed_local = timezone.localtime(run.completed_at)
                if completed_local > pickup_deadline:
                    run.late_reference_at = completed_local
                    run.late_minutes = max(
                        1,
                        int((completed_local - pickup_deadline).total_seconds() // 60),
                    )
                    run.late_reason = "Reached office late"
                    late_cabs.append(run)

            elif now_local > pickup_deadline:
                run.late_reference_at = now_local
                run.late_minutes = max(
                    1,
                    int((now_local - pickup_deadline).total_seconds() // 60),
                )
                run.late_reason = "Office arrival pending"
                late_cabs.append(run)

        # DROP = must finish within 2 hours after trip start
        elif trip_type == Trip.TRIP_TYPE_DROP and run.started_at:
            started_local = timezone.localtime(run.started_at)
            expected_drop_complete = started_local + timedelta(hours=2)
            run.expected_complete_at = expected_drop_complete

            if run.completed_at:
                completed_local = timezone.localtime(run.completed_at)
                if completed_local > expected_drop_complete:
                    run.late_reference_at = completed_local
                    run.late_minutes = max(
                        1,
                        int((completed_local - expected_drop_complete).total_seconds() // 60),
                    )
                    run.late_reason = "Drop completed late"
                    late_cabs.append(run)

            elif now_local > expected_drop_complete:
                run.late_reference_at = now_local
                run.late_minutes = max(
                    1,
                    int((now_local - expected_drop_complete).total_seconds() // 60),
                )
                run.late_reason = "Drop still running"
                late_cabs.append(run)

    late_cabs.sort(key=lambda run: run.late_minutes, reverse=True)

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
        "pending_location_requests": pending_location_requests,
        "pending_requests_count": pending_requests_count,
        "pending_driver_accounts": pending_driver_accounts,
        "pending_employee_accounts": pending_employee_accounts,
        "started_cabs": started_cabs,
        "completed_cabs": completed_cabs,
        "late_cabs": late_cabs,
        "cancelled_trips": cancelled_trips,
        "no_show_stops": no_show_stops,

        "started_cabs_count": started_cabs.count(),
        "completed_cabs_count": completed_cabs.count(),
        "late_cabs_count": len(late_cabs),
        "cancelled_trips_count": cancelled_trips.count(),
        "no_show_count": no_show_stops.count(),

        "recent_live_trips": recent_live_trips,
        "recent_alerts": recent_alerts,
    }

    return render(request, "admin_web/dashboard.html", context)

@login_required
@admin_required
@require_GET
def late_report_page(request):
    query = request.GET.get("q", "").strip()
    date_filter = request.GET.get("date", "").strip()
    result_filter = request.GET.get("result", "").strip().upper()

    stops = (
        RouteRunStop.objects
        .select_related(
            "employee",
            "route_run",
            "route_run__driver",
            "route_run__vehicle",
            "route_run__route_template",
        )
        .filter(
            late_seconds__gt=0,
        )
        .order_by(
            "-route_run__run_date",
            "-late_seconds",
        )
    )

    # ============================================================
    # SEARCH
    # ============================================================

    if query:
        stops = stops.filter(
            Q(
                employee__username__icontains=query
            )
            |
            Q(
                route_run__driver__username__icontains=query
            )
            |
            Q(
                route_run__vehicle__vehicle_number__icontains=query
            )
            |
            Q(
                route_run__route_template__name__icontains=query
            )
        )

    # ============================================================
    # DATE FILTER
    # ============================================================

    parsed_date = (
        parse_date(date_filter)
        if date_filter
        else None
    )

    if parsed_date:
        stops = stops.filter(
            route_run__run_date=parsed_date
        )

    # ============================================================
    # RESULT FILTER
    # ============================================================

    if result_filter == "PICKED":
        stops = stops.filter(
            is_picked=True,
            is_no_show=False,
        )

    elif result_filter == "NO_SHOW":
        stops = stops.filter(
            is_no_show=True,
        )

    # ============================================================
    # BUILD REPORT
    # ============================================================

    rows = []

    total_late_seconds = 0
    pickup_late_count = 0
    no_show_late_count = 0

    for stop in stops:
        late_seconds = (
            stop.late_seconds or 0
        )

        total_late_seconds += late_seconds

        minutes = late_seconds // 60
        seconds = late_seconds % 60

        late_text = (
            f"{minutes:02d}:{seconds:02d}"
        )

        if stop.is_no_show:
            result = "NO SHOW"
            finished_at = stop.no_show_at
            no_show_late_count += 1

        elif stop.is_picked:
            result = "PICKED"
            finished_at = stop.picked_at
            pickup_late_count += 1

        else:
            result = "WAITING"
            finished_at = None

        route_run = stop.route_run

        rows.append(
            {
                "id": stop.id,

                "date": (
                    route_run.run_date
                    if route_run
                    else None
                ),

                "employee":
                    stop.employee.username,

                "route_name": (
                    route_run.route_template.name
                    if route_run
                    and route_run.route_template
                    else "Manual Route"
                ),

                "driver": (
                    route_run.driver.username
                    if route_run
                    and route_run.driver
                    else "--"
                ),

                "vehicle": (
                    route_run.vehicle.vehicle_number
                    if route_run
                    and route_run.vehicle
                    else "--"
                ),

                "trip_type": (
                    route_run.trip_type
                    if route_run
                    else "--"
                ),

                "result": result,

                "arrival_time":
                    stop.arrival_time,

                "finished_at":
                    finished_at,

                "late_seconds":
                    late_seconds,

                "late_text":
                    late_text,
            }
        )

    # ============================================================
    # SUMMARY
    # ============================================================

    total_late_minutes = round(
        total_late_seconds / 60,
        1,
    )

    context = {
        "rows": rows,

        "query": query,
        "date_filter": date_filter,
        "result_filter": result_filter,

        "total_late_employees":
            len(rows),

        "pickup_late_count":
            pickup_late_count,

        "no_show_late_count":
            no_show_late_count,

        "total_late_minutes":
            total_late_minutes,
    }

    return render(
        request,
        "admin_web/late_report.html",
        context,
    )

# ============================================================
# NO SHOW REPORT
# Shows:
# - Driver arrival time
# - Free waiting time (maximum 10 minutes)
# - Late/red waiting time
# - Total waiting time
# - Exact No Show time
# ============================================================

@login_required
@admin_required
@require_GET
def no_show_report_page(request):
    query = request.GET.get("q", "").strip()
    date_filter = request.GET.get("date", "").strip()
    trip_type_filter = (
        request.GET.get("trip_type", "")
        .strip()
        .upper()
    )

    # ========================================================
    # GET ALL NO-SHOW STOPS
    # IMPORTANT:
    # Do NOT use late_seconds__gt=0 here.
    #
    # An employee can be marked No Show even when late_seconds
    # is 0, so every is_no_show=True record must appear.
    # ========================================================

    stops = (
        RouteRunStop.objects
        .select_related(
            "employee",
            "route_run",
            "route_run__driver",
            "route_run__vehicle",
            "route_run__route_template",
        )
        .filter(
            is_no_show=True,
        )
        .order_by(
            "-route_run__run_date",
            "-no_show_at",
        )
    )

    # ========================================================
    # SEARCH
    # Employee / Driver / Vehicle / Route
    # ========================================================

    if query:
        stops = stops.filter(
            Q(
                employee__username__icontains=query
            )
            |
            Q(
                route_run__driver__username__icontains=query
            )
            |
            Q(
                route_run__vehicle__vehicle_number__icontains=query
            )
            |
            Q(
                route_run__route_template__name__icontains=query
            )
        )

    # ========================================================
    # DATE FILTER
    # ========================================================

    parsed_date = (
        parse_date(date_filter)
        if date_filter
        else None
    )

    if parsed_date:
        stops = stops.filter(
            route_run__run_date=parsed_date
        )

    # ========================================================
    # TRIP TYPE FILTER
    # PICKUP / DROP
    # ========================================================

    if trip_type_filter in ["PICKUP", "DROP"]:
        stops = stops.filter(
            route_run__trip_type=trip_type_filter
        )

    # ========================================================
    # BUILD REPORT ROWS
    # ========================================================

    rows = []

    total_waiting_seconds_all = 0
    total_free_seconds_all = 0
    total_late_seconds_all = 0

    no_show_with_late_count = 0
    no_show_within_free_count = 0

    for stop in stops:

        route_run = stop.route_run

        # ----------------------------------------------------
        # Waiting started when Driver presses ARRIVED.
        # Your RouteService sets both:
        #
        # arrival_time
        # waiting_started_at
        #
        # at that point.
        # ----------------------------------------------------

        waiting_started_at = (
            stop.waiting_started_at
            or stop.arrival_time
        )

        no_show_at = stop.no_show_at

        # ----------------------------------------------------
        # TOTAL WAITING TIME
        #
        # Driver Arrived -> Driver pressed No Show
        # ----------------------------------------------------

        total_waiting_seconds = 0

        if waiting_started_at and no_show_at:
            total_waiting_seconds = max(
                0,
                int(
                    (
                        no_show_at
                        - waiting_started_at
                    ).total_seconds()
                ),
            )

        # ----------------------------------------------------
        # FREE WAITING
        #
        # Use waiting_minutes from RouteRunStop.
        # Your current default is 10 minutes.
        # ----------------------------------------------------

        free_limit_seconds = (
            getattr(
                stop,
                "waiting_minutes",
                10,
            )
            or 10
        ) * 60

        free_wait_seconds = min(
            total_waiting_seconds,
            free_limit_seconds,
        )

        # ----------------------------------------------------
        # RED / LATE WAITING
        #
        # Anything after free waiting becomes late waiting.
        # ----------------------------------------------------

        calculated_late_seconds = max(
            0,
            total_waiting_seconds
            - free_limit_seconds,
        )

        # Prefer stored late_seconds because RouteService
        # freezes it when No Show is pressed.
        stored_late_seconds = (
            stop.late_seconds or 0
        )

        late_seconds = max(
            stored_late_seconds,
            calculated_late_seconds,
        )

        # ----------------------------------------------------
        # FORMAT TIME HELPER
        # 754 seconds -> 12:34
        # ----------------------------------------------------

        def format_duration(seconds):
            seconds = max(
                0,
                int(seconds or 0),
            )

            hours = seconds // 3600
            minutes = (
                seconds % 3600
            ) // 60
            secs = seconds % 60

            if hours > 0:
                return (
                    f"{hours:02d}:"
                    f"{minutes:02d}:"
                    f"{secs:02d}"
                )

            return (
                f"{minutes:02d}:"
                f"{secs:02d}"
            )

        free_wait_text = format_duration(
            free_wait_seconds
        )

        late_wait_text = format_duration(
            late_seconds
        )

        total_wait_text = format_duration(
            total_waiting_seconds
        )

        # ----------------------------------------------------
        # WAITING RESULT
        # ----------------------------------------------------

        if late_seconds > 0:
            waiting_status = "LATE"
            no_show_with_late_count += 1
        else:
            waiting_status = "WITHIN FREE TIME"
            no_show_within_free_count += 1

        # ----------------------------------------------------
        # SUMMARY TOTALS
        # ----------------------------------------------------

        total_waiting_seconds_all += (
            total_waiting_seconds
        )

        total_free_seconds_all += (
            free_wait_seconds
        )

        total_late_seconds_all += (
            late_seconds
        )

        # ----------------------------------------------------
        # BUILD ROW
        # ----------------------------------------------------

        rows.append(
            {
                "id": stop.id,

                "date": (
                    route_run.run_date
                    if route_run
                    else None
                ),

                "employee": (
                    stop.employee.username
                    if stop.employee
                    else "--"
                ),

                "route_name": (
                    route_run.route_template.name
                    if (
                        route_run
                        and route_run.route_template
                    )
                    else "Manual Route"
                ),

                "driver": (
                    route_run.driver.username
                    if (
                        route_run
                        and route_run.driver
                    )
                    else "--"
                ),

                "vehicle": (
                    route_run.vehicle.vehicle_number
                    if (
                        route_run
                        and route_run.vehicle
                    )
                    else "--"
                ),

                "trip_type": (
                    route_run.trip_type
                    if route_run
                    else "--"
                ),

                "arrival_time":
                    stop.arrival_time,

                "waiting_started_at":
                    waiting_started_at,

                "no_show_at":
                    no_show_at,

                "free_wait_seconds":
                    free_wait_seconds,

                "free_wait_text":
                    free_wait_text,

                "late_seconds":
                    late_seconds,

                "late_wait_text":
                    late_wait_text,

                "total_waiting_seconds":
                    total_waiting_seconds,

                "total_wait_text":
                    total_wait_text,

                "waiting_status":
                    waiting_status,

                "result":
                    "NO SHOW",
            }
        )

    # ========================================================
    # SUMMARY
    # ========================================================

    total_no_shows = len(rows)

    def summary_minutes(seconds):
        return round(
            seconds / 60,
            1,
        )

    context = {
        "rows":
            rows,

        "query":
            query,

        "date_filter":
            date_filter,

        "trip_type_filter":
            trip_type_filter,

        "total_no_shows":
            total_no_shows,

        "late_no_show_count":
            no_show_with_late_count,

        "within_free_count":
            no_show_within_free_count,

        "total_waiting_minutes":
            summary_minutes(
                total_waiting_seconds_all
            ),

        "total_free_minutes":
            summary_minutes(
                total_free_seconds_all
            ),

        "total_late_minutes":
            summary_minutes(
                total_late_seconds_all
            ),
    }

    return render(
        request,
        "admin_web/no_show_report.html",
        context,
    )



@login_required
@admin_required
@require_POST
@transaction.atomic
def delete_employee_account(request, employee_id):
    employee = get_object_or_404(
        User,
        id=employee_id,
        role=User.Role.EMPLOYEE,
    )

    # 1. Cancel/remove active + upcoming trip assignments
    # 2. Remove employee from route stops
    # 3. Remove current RouteRunStop if required
    # 4. Permanently delete account

    employee.delete()

    messages.success(
        request,
        "Employee account permanently deleted. "
        "All cab assignments were removed.",
    )

    return redirect("admin_web:employees")


@login_required
@admin_required
@require_POST
@transaction.atomic
def delete_employee_account(request, employee_id):

    employee = get_object_or_404(
        User,
        id=employee_id,
        role=User.Role.EMPLOYEE,
    )

    employee_name = employee.username

    # ==========================================================
    # 1. CANCEL ACTIVE / UPCOMING EMPLOYEE TRIPS
    # ==========================================================

    employee_trips = Trip.objects.filter(
        employee=employee,
    ).exclude(
        status__in=[
            "COMPLETED",
            "CANCELLED",
        ]
    )

    cancelled_trip_count = employee_trips.count()

    employee_trips.update(
        status="CANCELLED",
    )

    # ==========================================================
    # 2. REMOVE EMPLOYEE FROM CURRENT ROUTE RUN STOPS
    # ==========================================================
    #
    # This prevents the deleted/resigned employee from remaining
    # visible in today's pickup/drop execution.
    # ==========================================================

    RouteRunStop.objects.filter(
        employee=employee,
    ).delete()

    # ==========================================================
    # 3. REMOVE EMPLOYEE FROM PERMANENT ROUTE
    # ==========================================================
    #
    # This is the important part for seat availability.
    #
    # Once RouteStop is removed:
    # driver route occupied seats decrease automatically.
    # ==========================================================

    removed_route_stops, _ = (
        RouteStop.objects
        .filter(employee=employee)
        .delete()
    )

    # ==========================================================
    # 4. DELETE EMPLOYEE ACCOUNT PERMANENTLY
    # ==========================================================

    employee.delete()

    # ==========================================================
    # 5. SUCCESS MESSAGE
    # ==========================================================

    messages.success(
        request,
        (
            f"{employee_name} was permanently deleted. "
            f"{cancelled_trip_count} active/upcoming trip(s) "
            f"were cancelled and the route seat is now available."
        ),
    )

    return redirect(
        "admin_web:employees"
    )

@login_required
@admin_required
@require_GET
def driver_profile_page(request, driver_id):

    # ==========================================================
    # DRIVER
    # ==========================================================

    driver = get_object_or_404(
        User,
        id=driver_id,
        role=User.Role.DRIVER,
    )

    today = timezone.localdate()


    # ==========================================================
    # VEHICLE
    # ==========================================================

    try:
        vehicle = driver.vehicle
    except Exception:
        vehicle = None


    # ==========================================================
    # PERMANENT ROUTES ASSIGNED TO DRIVER
    # ==========================================================

    routes = (
        RouteTemplate.objects
        .filter(driver=driver)
        .select_related("vehicle")
        .order_by("name")
    )


    # ==========================================================
    # EMPLOYEES ASSIGNED UNDER DRIVER ROUTES
    # ==========================================================

    assigned_stops = (
        RouteStop.objects
        .filter(route__driver=driver)
        .select_related(
            "employee",
            "route",
            "route__vehicle",
        )
        .order_by(
            "route__name",
            "stop_order",
        )
    )


    assigned_employee_count = (
        assigned_stops
        .values("employee_id")
        .distinct()
        .count()
    )


    # ==========================================================
    # TODAY'S DRIVER TRIPS
    # ==========================================================

    today_trips = (
        Trip.objects
        .filter(
            driver=driver,
            trip_date=today,
        )
        .select_related(
            "employee",
            "vehicle",
            "route_run",
            "route_run__route_template",
        )
        .order_by(
            "trip_type",
            "id",
        )
    )


    # ==========================================================
    # RECENT DRIVER TRIP HISTORY
    # ==========================================================

    recent_trips = (
        Trip.objects
        .filter(
            driver=driver,
        )
        .select_related(
            "employee",
            "vehicle",
            "route_run",
            "route_run__route_template",
        )
        .order_by(
            "-trip_date",
            "-id",
        )[:20]
    )


    # ==========================================================
    # DRIVER REVIEWS
    # ==========================================================

    reviews = (
        Review.objects
        .filter(
            trip__driver=driver,
        )
        .select_related(
            "trip",
            "trip__employee",
        )
        .order_by(
            "-created_at"
        )[:10]
    )


    # ==========================================================
    # REVIEW AVERAGE
    # ==========================================================

    review_count = reviews.count()

    if review_count:

        average_rating = (
            sum(
                review.rating
                for review in reviews
            )
            / review_count
        )

        average_rating = round(
            average_rating,
            1,
        )

    else:

        average_rating = 0


    # ==========================================================
    # RENDER
    # ==========================================================

    context = {

        "driver": driver,

        "vehicle": vehicle,

        "today": today,

        "routes": routes,

        "assigned_stops": assigned_stops,

        "assigned_employee_count":
            assigned_employee_count,

        "today_trips": today_trips,

        "recent_trips": recent_trips,

        "reviews": reviews,

        "review_count": review_count,

        "average_rating": average_rating,
    }


    return render(
        request,
        "admin_web/driver_profile.html",
        context,
    )

@login_required
@admin_required
@require_POST
@transaction.atomic
def delete_driver_account(request, driver_id):

    driver = get_object_or_404(
        User,
        id=driver_id,
        role=User.Role.DRIVER,
    )

    driver_name = driver.username

    # ==========================================================
    # 1. FIND ALL PERMANENT ROUTES ASSIGNED TO THIS DRIVER
    # ==========================================================

    driver_routes = RouteTemplate.objects.filter(
        driver=driver
    )

    route_ids = list(
        driver_routes.values_list(
            "id",
            flat=True,
        )
    )

    # ==========================================================
    # 2. FIND EMPLOYEES CURRENTLY UNDER THESE ROUTES
    # ==========================================================

    affected_employee_ids = list(
        RouteStop.objects.filter(
            route_id__in=route_ids
        )
        .values_list(
            "employee_id",
            flat=True,
        )
        .distinct()
    )

    affected_employee_count = len(
        affected_employee_ids
    )

    # ==========================================================
    # 3. CANCEL DRIVER'S ACTIVE / UPCOMING TRIPS
    # ==========================================================
    #
    # Completed and already cancelled trips are untouched.
    # ==========================================================

    active_trips = Trip.objects.filter(
        driver=driver,
    ).exclude(
        status__in=[
            "COMPLETED",
            "CANCELLED",
        ]
    )

    cancelled_trip_count = (
        active_trips.count()
    )

    active_trips.update(
        status="CANCELLED"
    )

    # ==========================================================
    # 4. REMOVE EMPLOYEES FROM PERMANENT DRIVER ROUTES
    # ==========================================================
    #
    # Once these RouteStop rows are deleted, those employees
    # automatically become UNASSIGNED on the employee page.
    # ==========================================================

    RouteStop.objects.filter(
        route_id__in=route_ids
    ).delete()

    # ==========================================================
    # 5. REMOVE CURRENT / ACTIVE ROUTE RUN STOPS
    # ==========================================================

    if route_ids:

        RouteRunStop.objects.filter(
            route_run__route_template_id__in=route_ids
        ).delete()

    # ==========================================================
    # 6. CLEAR DRIVER FROM ROUTE TEMPLATES
    # ==========================================================
    #
    # We keep the route template itself.
    #
    # Admin can later assign another driver and rebuild the
    # employee route.
    # ==========================================================

    driver_routes.update(
        driver=None
    )

    # ==========================================================
    # 7. DELETE DRIVER ACCOUNT PERMANENTLY
    # ==========================================================

    driver.delete()

    # ==========================================================
    # 8. ADMIN SUCCESS MESSAGE
    # ==========================================================

    messages.success(
        request,
        (
            f"Driver {driver_name} was permanently deleted. "
            f"{affected_employee_count} employee(s) are now unassigned. "
            f"{cancelled_trip_count} active/upcoming trip(s) were cancelled."
        ),
    )

    return redirect(
        "admin_web:drivers"
    )

# =========================================================
# REQUEST MANAGEMENT
# =========================================================

@login_required
@admin_required
@require_GET
def requests_page(request):
    """
    Main Requests hub.

    Contains:
    - Location Change Requests
    - Change Driver Requests
    - Change Route Requests
    - Employee Account Approvals
    - Driver Account Approvals
    """

    # ============================================================
    # PICKUP LOCATION REQUESTS
    # ============================================================

    pending_location_requests = (
        PickupLocationChangeRequest.objects
        .filter(
            status=PickupLocationChangeRequest.STATUS_PENDING
        )
        .count()
    )

    # ============================================================
    # EMPLOYEE ACCOUNT APPROVALS
    # ============================================================

    pending_employee_accounts = (
        User.objects
        .filter(
            role=User.Role.EMPLOYEE,
            account_status=User.ACCOUNT_STATUS_PENDING,
        )
        .count()
    )

    # ============================================================
    # DRIVER ACCOUNT APPROVALS
    # ============================================================

    pending_driver_accounts = (
        User.objects
        .filter(
            role=User.Role.DRIVER,
            account_status=User.ACCOUNT_STATUS_PENDING,
        )
        .count()
    )

    # ============================================================
    # OTHER REQUEST TYPES
    # Not implemented yet
    # ============================================================

    pending_change_driver_requests = 0
    pending_change_route_requests = 0

    # ============================================================
    # TOTAL PENDING
    # ============================================================

    total_pending_requests = (
        pending_location_requests
        + pending_employee_accounts
        + pending_driver_accounts
        + pending_change_driver_requests
        + pending_change_route_requests
    )

    context = {
        "pending_location_requests":
            pending_location_requests,

        "pending_employee_accounts":
            pending_employee_accounts,

        "pending_driver_accounts":
            pending_driver_accounts,

        "pending_change_driver_requests":
            pending_change_driver_requests,

        "pending_change_route_requests":
            pending_change_route_requests,

        "total_pending_requests":
            total_pending_requests,
    }

    return render(
        request,
        "admin_web/requests.html",
        context,
    )

@login_required
@admin_required
@require_GET
def employee_account_approvals_page(request):
    """
    Show employee registrations waiting for Admin approval.
    """

    pending_employees = (
        User.objects
        .filter(
            role=User.Role.EMPLOYEE,
            account_status=User.ACCOUNT_STATUS_PENDING,
        )
        .order_by("-date_joined")
    )

    context = {
        "pending_employees": pending_employees,
        "pending_count": pending_employees.count(),
    }

    return render(
        request,
        "admin_web/employee_account_approvals.html",
        context,
    )

@login_required
@admin_required
@require_POST
def approve_employee_account(request, employee_id):

    employee = get_object_or_404(
        User,
        id=employee_id,
        role=User.Role.EMPLOYEE,
    )

    if (
        employee.account_status
        != User.ACCOUNT_STATUS_PENDING
    ):
        messages.warning(
            request,
            "This account has already been reviewed.",
        )

        return redirect(
            "admin_web:employee_account_approvals"
        )

    employee.account_status = (
        User.ACCOUNT_STATUS_APPROVED
    )

    employee.is_active = True

    employee.account_reviewed_at = timezone.now()
    employee.account_reviewed_by = request.user
    employee.account_rejection_reason = ""

    employee.save(
        update_fields=[
            "account_status",
            "is_active",
            "account_reviewed_at",
            "account_reviewed_by",
            "account_rejection_reason",
        ]
    )

    messages.success(
        request,
        f"{employee.username}'s account has been approved.",
    )

    return redirect(
        "admin_web:employee_account_approvals"
    )
@login_required
@admin_required
@require_POST
def reject_employee_account(request, employee_id):

    employee = get_object_or_404(
        User,
        id=employee_id,
        role=User.Role.EMPLOYEE,
    )

    if (
        employee.account_status
        != User.ACCOUNT_STATUS_PENDING
    ):
        messages.warning(
            request,
            "This account has already been reviewed.",
        )

        return redirect(
            "admin_web:employee_account_approvals"
        )

    rejection_reason = request.POST.get(
        "rejection_reason",
        "",
    ).strip()

    if not rejection_reason:
        messages.error(
            request,
            "Please enter a rejection reason.",
        )

        return redirect(
            "admin_web:employee_account_approvals"
        )

    employee.account_status = (
        User.ACCOUNT_STATUS_REJECTED
    )

    employee.is_active = False

    employee.account_reviewed_at = timezone.now()
    employee.account_reviewed_by = request.user
    employee.account_rejection_reason = (
        rejection_reason
    )

    employee.save(
        update_fields=[
            "account_status",
            "is_active",
            "account_reviewed_at",
            "account_reviewed_by",
            "account_rejection_reason",
        ]
    )

    messages.success(
        request,
        f"{employee.username}'s account has been rejected.",
    )

    return redirect(
        "admin_web:employee_account_approvals"
    )

@login_required
@admin_required
@require_GET
def driver_account_approvals_page(request):
    """
    Show driver registrations waiting for Admin approval.
    """

    pending_drivers = (
        User.objects
        .filter(
            role=User.Role.DRIVER,
            account_status=User.ACCOUNT_STATUS_PENDING,
        )
        .select_related("vehicle")
        .order_by("-date_joined")
    )

    context = {
        "pending_drivers": pending_drivers,
        "pending_count": pending_drivers.count(),
    }

    return render(
        request,
        "admin_web/driver_account_approvals.html",
        context,
    )

@login_required
@admin_required
@require_POST
def approve_driver_account(request, driver_id):

    driver = get_object_or_404(
        User,
        id=driver_id,
        role=User.Role.DRIVER,
    )

    if (
        driver.account_status
        != User.ACCOUNT_STATUS_PENDING
    ):
        messages.warning(
            request,
            "This account has already been reviewed.",
        )

        return redirect(
            "admin_web:driver_account_approvals"
        )

    driver.account_status = (
        User.ACCOUNT_STATUS_APPROVED
    )

    driver.is_active = True
    driver.account_reviewed_at = timezone.now()
    driver.account_reviewed_by = request.user
    driver.account_rejection_reason = ""

    driver.save(
        update_fields=[
            "account_status",
            "is_active",
            "account_reviewed_at",
            "account_reviewed_by",
            "account_rejection_reason",
        ]
    )

    messages.success(
        request,
        f"{driver.username}'s driver account has been approved.",
    )

    return redirect(
        "admin_web:driver_account_approvals"
    )

@login_required
@admin_required
@require_POST
def reject_driver_account(request, driver_id):

    driver = get_object_or_404(
        User,
        id=driver_id,
        role=User.Role.DRIVER,
    )

    if (
        driver.account_status
        != User.ACCOUNT_STATUS_PENDING
    ):
        messages.warning(
            request,
            "This account has already been reviewed.",
        )

        return redirect(
            "admin_web:driver_account_approvals"
        )

    rejection_reason = request.POST.get(
        "rejection_reason",
        "",
    ).strip()

    if not rejection_reason:
        messages.error(
            request,
            "Please enter a rejection reason.",
        )

        return redirect(
            "admin_web:driver_account_approvals"
        )

    driver.account_status = (
        User.ACCOUNT_STATUS_REJECTED
    )

    driver.is_active = False
    driver.account_reviewed_at = timezone.now()
    driver.account_reviewed_by = request.user
    driver.account_rejection_reason = rejection_reason

    driver.save(
        update_fields=[
            "account_status",
            "is_active",
            "account_reviewed_at",
            "account_reviewed_by",
            "account_rejection_reason",
        ]
    )

    messages.success(
        request,
        f"{driver.username}'s driver account has been rejected.",
    )

    return redirect(
        "admin_web:driver_account_approvals"
    )


@login_required
@admin_required
@require_GET
def location_requests_page(request):

    location_requests = (
        PickupLocationChangeRequest.objects
        .select_related(
            "employee",
            "reviewed_by",
        )
        .order_by(
            "-requested_at"
        )
    )

    pending_count = location_requests.filter(
        status=PickupLocationChangeRequest.STATUS_PENDING
    ).count()

    approved_count = location_requests.filter(
        status=PickupLocationChangeRequest.STATUS_APPROVED
    ).count()

    rejected_count = location_requests.filter(
        status=PickupLocationChangeRequest.STATUS_REJECTED
    ).count()

    context = {
        "location_requests": location_requests,
        "pending_count": pending_count,
        "approved_count": approved_count,
        "rejected_count": rejected_count,
    }

    return render(
        request,
        "admin_web/location_requests.html",
        context,
    )


@login_required
@admin_required
@require_POST
def approve_location_request(request, request_id):

    location_request = get_object_or_404(
        PickupLocationChangeRequest.objects.select_related(
            "employee"
        ),
        id=request_id,
    )

    if (
        location_request.status
        != PickupLocationChangeRequest.STATUS_PENDING
    ):
        messages.warning(
            request,
            "This location request has already been reviewed.",
        )

        return redirect(
            "admin_web:location_requests"
        )

    employee = location_request.employee

    # ---------------------------------------------------------
    # Update employee's approved pickup location
    # ---------------------------------------------------------

    employee.pickup_location = (
        location_request.requested_pickup_location
    )

    employee.pickup_latitude = (
        location_request.requested_pickup_latitude
    )

    employee.pickup_longitude = (
        location_request.requested_pickup_longitude
    )

    employee.save(
        update_fields=[
            "pickup_location",
            "pickup_latitude",
            "pickup_longitude",
        ]
    )

    # ---------------------------------------------------------
    # Mark request approved
    # ---------------------------------------------------------

    location_request.status = (
        PickupLocationChangeRequest.STATUS_APPROVED
    )

    location_request.reviewed_at = timezone.now()
    location_request.reviewed_by = request.user

    location_request.admin_note = (
        request.POST.get(
            "admin_note",
            "",
        ).strip()
    )

    location_request.save(
        update_fields=[
            "status",
            "reviewed_at",
            "reviewed_by",
            "admin_note",
        ]
    )

    # ---------------------------------------------------------
    # Notify employee
    # ---------------------------------------------------------

    try:
        send_push_notification(
            user=employee,
            title="Pickup Location Approved ✅",
            body=(
                "Your pickup location change request "
                "has been approved."
            ),
            data={
                "type": "PICKUP_LOCATION_CHANGE_APPROVED",
                "request_id": str(location_request.id),
            },
        )

    except Exception as e:
        print(
            "LOCATION APPROVAL FCM ERROR:",
            e,
        )

    messages.success(
        request,
        (
            f"{employee.username}'s pickup location "
            "change request has been approved."
        ),
    )

    return redirect(
        "admin_web:location_requests"
    )


@login_required
@admin_required
@require_POST
def reject_location_request(request, request_id):

    location_request = get_object_or_404(
        PickupLocationChangeRequest.objects.select_related(
            "employee"
        ),
        id=request_id,
    )

    if (
        location_request.status
        != PickupLocationChangeRequest.STATUS_PENDING
    ):
        messages.warning(
            request,
            "This location request has already been reviewed.",
        )

        return redirect(
            "admin_web:location_requests"
        )

    employee = location_request.employee

    # IMPORTANT:
    # We DO NOT modify employee pickup location on rejection.

    location_request.status = (
        PickupLocationChangeRequest.STATUS_REJECTED
    )

    location_request.reviewed_at = timezone.now()
    location_request.reviewed_by = request.user

    location_request.admin_note = (
        request.POST.get(
            "admin_note",
            "",
        ).strip()
    )

    location_request.save(
        update_fields=[
            "status",
            "reviewed_at",
            "reviewed_by",
            "admin_note",
        ]
    )

    # ---------------------------------------------------------
    # Notify employee
    # ---------------------------------------------------------

    try:
        send_push_notification(
            user=employee,
            title="Pickup Location Request Rejected",
            body=(
                "Your pickup location change request "
                "was not approved."
            ),
            data={
                "type": "PICKUP_LOCATION_CHANGE_REJECTED",
                "request_id": str(location_request.id),
            },
        )

    except Exception as e:
        print(
            "LOCATION REJECTION FCM ERROR:",
            e,
        )

    messages.success(
        request,
        (
            f"{employee.username}'s pickup location "
            "change request has been rejected."
        ),
    )

    return redirect(
        "admin_web:location_requests"
    )
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
    elif filter_type == "pickup":
        employees = employees.exclude(
            pickup_location__isnull=True
        ).exclude(
            pickup_location=""
        )

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

@login_required
@admin_required
@require_GET
def employee_profile_page(request, employee_id):

    employee = get_object_or_404(
        User,
        id=employee_id,
        role="EMPLOYEE",
    )

    route_stop = (
        RouteStop.objects
        .select_related(
            "route",
            "route__driver",
            "route__vehicle",
        )
        .filter(
            employee=employee
        )
        .first()
    )

    today = timezone.localdate()

    today_trips = (
        Trip.objects
        .select_related(
            "driver",
            "vehicle",
            "route_run",
            "route_run__route_template",
        )
        .filter(
            employee=employee,
            trip_date=today,
        )
        .order_by("pickup_time")
    )

    recent_trips = (
        Trip.objects
        .select_related(
            "driver",
            "vehicle",
            "route_run",
            "route_run__route_template",
        )
        .filter(
            employee=employee
        )
        .order_by("-trip_date", "-created_at")[:10]
    )

    leave_records = (
        EmployeeLeave.objects
        .filter(
            employee=employee
        )
        .order_by("-leave_date")[:10]
    )

    reviews = (
        Review.objects
        .select_related(
            "trip",
            "trip__driver",
        )
        .filter(
            employee=employee
        )
        .order_by("-created_at")[:10]
    )

    context = {
        "employee": employee,
        "route_stop": route_stop,
        "today_trips": today_trips,
        "recent_trips": recent_trips,
        "leave_records": leave_records,
        "reviews": reviews,
        "today": today,
    }

    return render(
        request,
        "admin_web/employee_profile.html",
        context,
    )
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

    # =========================================================
    # ADMIN NOTIFICATION CENTER
    #
    # Show only notifications actually belonging to this Admin
    # and only Route Start / Route Completed notifications.
    # =========================================================

    notifications = (
        Notification.objects
        .select_related(
            "user",
            "driver",
            "employee",
            "trip",
            "route_run",
        )
        .filter(
            user=request.user,
            title__in=[
                "Pickup Route Started 🚕",
                "Drop Trip Started 🚕",
                "✅ Route Completed",
            ],
        )
        .order_by(
            "-created_at"
        )
    )

    unread_count = (
        notifications
        .filter(
            is_read=False
        )
        .count()
    )

    return render(
        request,
        "admin_web/notifications.html",
        {
            "notifications":
                notifications,

            "unread_count":
                unread_count,
        },
    )
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

    # Keep ALL active employees/drivers/vehicles in the page data.
    #
    # Important for Edit Route:
    # Existing route members are already "assigned", but the edit modal still
    # needs them in ROUTE_FORM_DATA so it can show the current driver/vehicle
    # and preserve existing employees while adding/replacing another employee.
    #
    # Create Route still blocks already-used resources via is_selectable.
    assigned_employee_ids = set(
        RouteStop.objects.filter(
            route__isnull=False,
            employee_id__isnull=False,
        ).values_list("employee_id", flat=True)
    )

    assigned_driver_ids = set(
        RouteTemplate.objects.filter(
            driver__isnull=False
        ).values_list("driver_id", flat=True)
    )

    assigned_vehicle_ids = set(
        RouteTemplate.objects.filter(
            vehicle__isnull=False
        ).values_list("vehicle_id", flat=True)
    )

    employees = list(
        User.objects.filter(
            role="EMPLOYEE",
            is_active=True,
        ).order_by("username")
    )
    for employee in employees:
        employee.is_selectable = employee.id not in assigned_employee_ids

    drivers = list(
        User.objects.filter(
            role="DRIVER",
            is_active=True,
        ).order_by("username")
    )
    for driver in drivers:
        driver.is_selectable = driver.id not in assigned_driver_ids

    # Do not exclude vehicles here. Edit Route must be able to find the
    # vehicle already attached to its current driver.
    vehicles = list(
        Vehicle.objects.select_related("driver").order_by("vehicle_number")
    )
    for vehicle in vehicles:
        vehicle.is_selectable = vehicle.id not in assigned_vehicle_ids

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

        try:
            for stop in route.stops.select_related("employee").all():
                employee = stop.employee

                send_push_notification(
                    user=employee,
                    title=f"{trip_type.title()} Cab Assigned 🚕",
                    body=f"Your {trip_type.lower()} cab has been assigned. Please check your trip details.",
                    data={
                        "type": "TRIP_ASSIGNED",
                        "trip_type": trip_type,
                        "route_id": route.id,
                    },
                )

        except Exception as e:
            print("❌ Admin web assign notification error:", e)
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
    weekend_skip_count = 0

    current_date = start_date

    while current_date <= end_date:
        # Saturday = 5, Sunday = 6
        if current_date.weekday() in [5, 6]:
            weekend_skip_count += 1
            current_date += timedelta(days=1)
            continue

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
        messages.success(
            request,
            f"{success_count} routes repeated successfully. Weekend skipped: {weekend_skip_count} day(s).",
        )

    if fail_count:
        messages.warning(
            request,
            f"{fail_count} routes skipped or failed.",
        )

    if not success_count and not fail_count:
        messages.info(
            request,
            f"No routes were repeated. Weekend skipped: {weekend_skip_count} day(s).",
        )

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
def alert_driver_location_api(request, alert_id):
    alert = get_object_or_404(
        EmergencyAlert.objects.select_related(
            "trip",
            "trip__driver",
            "route_run",
            "route_run__driver",
        ),
        id=alert_id,
    )

    trip = alert.trip
    route_run = alert.route_run

    driver = None

    if trip and trip.driver:
        driver = trip.driver
    elif route_run and route_run.driver:
        driver = route_run.driver

    if not driver:
        return JsonResponse(
            {
                "success": False,
                "error": "Driver not available.",
            },
            status=404,
        )

    latest_locations = _get_latest_driver_locations_map()
    location = latest_locations.get(driver.id)

    if not location:
        return JsonResponse(
            {
                "success": False,
                "error": "Driver location not available.",
            },
            status=404,
        )

    return JsonResponse(
        {
            "success": True,
            "driver_id": driver.id,
            "driver_name": driver.username,
            "latitude": _safe_float(location.latitude),
            "longitude": _safe_float(location.longitude),
            "updated_at": (
                location.updated_at.isoformat()
                if location.updated_at
                else ""
            ),
        }
    )
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
    alert = get_object_or_404(
        EmergencyAlert,
        id=alert_id,
    )

    if alert.status == EmergencyAlert.STATUS_RESOLVED:
        messages.info(
            request,
            "This emergency alert is already resolved.",
        )
        return redirect("admin_web:alerts")

    alert.resolve()

    messages.success(
        request,
        "Emergency alert resolved successfully.",
    )

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
    today = timezone.localdate()

    # =========================================================
    # TODAY'S ACTIVE CAB RUNS ONLY
    # =========================================================

    active_runs = (
        RouteRun.objects
        .select_related(
            "driver",
            "vehicle",
            "route_template",
        )
        .prefetch_related(
            "stops",
            "stops__employee",
        )
        .filter(
            run_date=today,
            completed_at__isnull=True,
        )
        .order_by(
            "route_template_id",
            "trip_type",
            "-created_at",
        )
    )

    latest_locations = _get_latest_driver_locations_map()

    data = []

    # =========================================================
    # DUPLICATE PROTECTION
    #
    # Same:
    # route + driver + vehicle + trip type
    #
    # will appear only once.
    #
    # PICKUP and DROP remain separate.
    # =========================================================

    seen_runs = set()

    for run in active_runs:

        duplicate_key = (
            run.route_template_id,
            run.driver_id,
            run.vehicle_id,
            run.trip_type,
        )

        if duplicate_key in seen_runs:
            continue

        seen_runs.add(duplicate_key)

        # =====================================================
        # DRIVER LOCATION
        # =====================================================

        location = latest_locations.get(
            run.driver_id
        )

        # =====================================================
        # STOP COUNTS
        # =====================================================

        total_stops = run.stops.count()

        completed_stops = run.stops.filter(
            is_picked=True
        ).count()

        pending_stops = (
            total_stops -
            completed_stops
        )

        # =====================================================
        # ONLINE / OFFLINE STATUS
        # =====================================================

        online_status = "OFFLINE"
        minutes_ago = None

        if location and location.updated_at:

            diff_minutes = (
                now -
                location.updated_at
            ).total_seconds() / 60

            minutes_ago = round(
                diff_minutes
            )

            if diff_minutes <= 2:
                online_status = "ONLINE"

            elif diff_minutes <= 5:
                online_status = "IDLE"

            else:
                online_status = "OFFLINE"

        # =====================================================
        # DRIVER SPEED
        # =====================================================

        latest_speed = (
            DriverLocationHistory.objects
            .filter(
                driver=run.driver,
                route_run=run,
            )
            .order_by(
                "-recorded_at"
            )
            .first()
        )

        speed = (
            latest_speed.speed_kmph
            if latest_speed
            else 0
        )

        moving_status = (
            "MOVING"
            if speed and speed > 5
            else "STOPPED"
        )

        # Used only for approximate ETA fallback.
        avg_speed = (
            speed
            if speed and speed > 10
            else 25
        )

        # =====================================================
        # NEXT STOP
        # =====================================================

        remaining_stops_qs = (
            run.stops
            .filter(
                is_picked=False
            )
            .order_by(
                "stop_order"
            )
        )

        next_stop = (
            remaining_stops_qs
            .first()
        )

        eta_minutes = None
        eta_label = (
            "Location unavailable"
        )

        next_stop_name = "--"
        next_stop_location = "--"

        if next_stop:

            next_stop_name = (
                next_stop.employee.username
                if next_stop.employee
                else "--"
            )

            next_stop_location = (
                next_stop.pickup_location
                or "--"
            )

            if (
                location
                and location.latitude is not None
                and location.longitude is not None
                and next_stop.pickup_latitude is not None
                and next_stop.pickup_longitude is not None
            ):

                distance_km = (
                    calculate_distance_km(
                        location.latitude,
                        location.longitude,
                        next_stop.pickup_latitude,
                        next_stop.pickup_longitude,
                    )
                )

                eta_minutes = max(
                    1,
                    round(
                        (
                            distance_km /
                            avg_speed
                        ) * 60
                    )
                )

                eta_label = (
                    f"{eta_minutes} mins "
                    "to next stop"
                )

        # =====================================================
        # ESTIMATED COMPLETION
        # =====================================================

        estimated_completion_minutes = None
        estimated_completion_time = "--"

        if pending_stops > 0:

            base_minutes = (
                eta_minutes or 0
            )

            # Existing rule:
            # approx 6 minutes per remaining stop.
            stop_buffer_minutes = (
                pending_stops * 6
            )

            estimated_completion_minutes = (
                base_minutes +
                stop_buffer_minutes
            )

            estimated_completion_time = (
                now +
                timezone.timedelta(
                    minutes=(
                        estimated_completion_minutes
                    )
                )
            ).strftime(
                "%I:%M %p"
            )

        else:

            estimated_completion_minutes = 0

            estimated_completion_time = (
                "Almost completed"
            )

        # =====================================================
        # RESPONSE ROW
        # =====================================================

        data.append(
            {
                "route_run_id":
                    run.id,

                "driver_id":
                    run.driver_id,

                "driver_name": (
                    run.driver.username
                    if run.driver
                    else "--"
                ),

                "vehicle_number": (
                    run.vehicle.vehicle_number
                    if run.vehicle
                    else "--"
                ),

                "vehicle_model": (
                    run.vehicle.vehicle_model
                    if run.vehicle
                    else "--"
                ),

                "route_name": (
                    run.route_template.name
                    if run.route_template
                    else "--"
                ),

                "trip_type":
                    run.trip_type,

                "run_date":
                    str(run.run_date),

                "online_status":
                    online_status,

                "moving_status":
                    moving_status,

                "speed_kmph":
                    round(
                        speed or 0,
                        1,
                    ),

                "total_stops":
                    total_stops,

                "completed_stops":
                    completed_stops,

                "pending_stops":
                    pending_stops,

                "progress_percent": (
                    round(
                        (
                            completed_stops /
                            total_stops
                        ) * 100
                    )
                    if total_stops
                    else 0
                ),

                "latitude": (
                    location.latitude
                    if location
                    else None
                ),

                "longitude": (
                    location.longitude
                    if location
                    else None
                ),

                "last_updated": (
                    location.updated_at.isoformat()
                    if (
                        location
                        and location.updated_at
                    )
                    else ""
                ),

                "minutes_ago":
                    minutes_ago,

                "next_stop_name":
                    next_stop_name,

                "next_stop_location":
                    next_stop_location,

                "eta_minutes":
                    eta_minutes,

                "eta_label":
                    eta_label,

                "estimated_completion_minutes":
                    estimated_completion_minutes,

                "estimated_completion_time":
                    estimated_completion_time,
            }
        )

    # =========================================================
    # FINAL RESPONSE
    # =========================================================

    return JsonResponse(
        {
            "results": data,
            "count": len(data),
            "date": str(today),
        }
    )



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
def leave_report_page(request):
    query = request.GET.get("q", "").strip()
    start_date_value = request.GET.get("start_date", "").strip()
    end_date_value = request.GET.get("end_date", "").strip()

    today = timezone.localdate()

    start_date = (
        parse_date(start_date_value)
        if start_date_value
        else today
    )

    end_date = (
        parse_date(end_date_value)
        if end_date_value
        else start_date
    )

    if end_date < start_date:
        end_date = start_date

    leaves = (
        EmployeeLeave.objects
        .select_related("employee")
        .filter(
            leave_date__range=[
                start_date,
                end_date,
            ]
        )
        .order_by(
            "-leave_date",
            "employee__username",
        )
    )

    if query:
        leaves = leaves.filter(
            Q(
                employee__username__icontains=query
            )
            |
            Q(
                reason__icontains=query
            )
        )

    rows = []

    total_auto_cancelled = 0
    pickup_cancelled_total = 0
    drop_cancelled_total = 0

    for leave in leaves:
        employee = leave.employee

        cancelled_trips = (
            Trip.objects
            .filter(
                employee=employee,
                trip_date=leave.leave_date,
                status=Trip.STATUS_CANCELLED,
            )
        )

        pickup_cancelled = (
            cancelled_trips
            .filter(
                trip_type=Trip.TRIP_TYPE_PICKUP,
            )
            .count()
        )

        drop_cancelled = (
            cancelled_trips
            .filter(
                trip_type=Trip.TRIP_TYPE_DROP,
            )
            .count()
        )

        cancelled_count = (
            pickup_cancelled
            + drop_cancelled
        )

        total_auto_cancelled += cancelled_count
        pickup_cancelled_total += pickup_cancelled
        drop_cancelled_total += drop_cancelled

        rows.append(
            {
                "id": leave.id,

                "employee_name":
                    employee.username,

                "leave_date":
                    leave.leave_date,

                "reason":
                    leave.reason
                    or "No reason provided",

                "created_at":
                    leave.created_at,

                "pickup_cancelled":
                    pickup_cancelled,

                "drop_cancelled":
                    drop_cancelled,

                "cancelled_count":
                    cancelled_count,
            }
        )

    context = {
        "rows": rows,

        "query": query,
        "start_date": str(start_date),
        "end_date": str(end_date),

        "total_leave_records":
            len(rows),

        "pickup_cancelled_total":
            pickup_cancelled_total,

        "drop_cancelled_total":
            drop_cancelled_total,

        "total_auto_cancelled":
            total_auto_cancelled,
    }

    return render(
        request,
        "admin_web/leave_report.html",
        context,
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

    # ==========================================================
    # DATE RANGE
    # ==========================================================

    start_date_value = request.GET.get(
        "start_date",
        "",
    ).strip()

    end_date_value = request.GET.get(
        "end_date",
        "",
    ).strip()

    today = timezone.localdate()

    try:

        start_date_obj = (
            datetime.strptime(
                start_date_value,
                "%Y-%m-%d",
            ).date()
            if start_date_value
            else today
        )

        end_date_obj = (
            datetime.strptime(
                end_date_value,
                "%Y-%m-%d",
            ).date()
            if end_date_value
            else start_date_obj
        )

    except ValueError:

        start_date_obj = today
        end_date_obj = today


    if end_date_obj < start_date_obj:
        end_date_obj = start_date_obj


    # ==========================================================
    # WORKBOOK
    # ==========================================================

    wb = openpyxl.Workbook()

    default_sheet = wb.active
    wb.remove(default_sheet)


    # ==========================================================
    # STYLES
    # ==========================================================

    title_fill = PatternFill(
        "solid",
        fgColor="172554",
    )

    header_fill = PatternFill(
        "solid",
        fgColor="2563EB",
    )

    alternate_fill = PatternFill(
        "solid",
        fgColor="F8FAFC",
    )

    danger_fill = PatternFill(
        "solid",
        fgColor="FEE2E2",
    )

    warning_fill = PatternFill(
        "solid",
        fgColor="FEF3C7",
    )

    success_fill = PatternFill(
        "solid",
        fgColor="DCFCE7",
    )

    title_font = Font(
        color="FFFFFF",
        bold=True,
        size=16,
    )

    header_font = Font(
        color="FFFFFF",
        bold=True,
    )

    subtitle_font = Font(
        color="64748B",
        italic=True,
    )

    thin_border = Border(
        left=Side(
            style="thin",
            color="E5E7EB",
        ),
        right=Side(
            style="thin",
            color="E5E7EB",
        ),
        top=Side(
            style="thin",
            color="E5E7EB",
        ),
        bottom=Side(
            style="thin",
            color="E5E7EB",
        ),
    )


    # ==========================================================
    # HELPERS
    # ==========================================================

    def safe_localtime(value):

        if not value:
            return ""

        try:
            return timezone.localtime(
                value
            ).strftime(
                "%d %b %Y %I:%M:%S %p"
            )
        except Exception:
            return str(value)


    def format_duration(seconds):

        seconds = max(
            0,
            int(seconds or 0),
        )

        hours = seconds // 3600

        minutes = (
            seconds % 3600
        ) // 60

        secs = seconds % 60

        if hours:
            return (
                f"{hours:02d}:"
                f"{minutes:02d}:"
                f"{secs:02d}"
            )

        return (
            f"{minutes:02d}:"
            f"{secs:02d}"
        )


    def create_sheet(
        sheet_name,
        title,
        headers,
    ):

        ws = wb.create_sheet(
            title=sheet_name[:31]
        )

        last_column = get_column_letter(
            len(headers)
        )

        ws.merge_cells(
            f"A1:{last_column}1"
        )

        ws["A1"] = title
        ws["A1"].fill = title_fill
        ws["A1"].font = title_font
        ws["A1"].alignment = Alignment(
            horizontal="left",
            vertical="center",
        )

        ws.row_dimensions[1].height = 28


        ws.merge_cells(
            f"A2:{last_column}2"
        )

        ws["A2"] = (
            "Date Range: "
            f"{start_date_obj.strftime('%d %b %Y')} "
            "to "
            f"{end_date_obj.strftime('%d %b %Y')}"
        )

        ws["A2"].font = subtitle_font


        ws.merge_cells(
            f"A3:{last_column}3"
        )

        ws["A3"] = (
            "Generated: "
            f"{timezone.localtime().strftime('%d %b %Y %I:%M %p')}"
        )

        ws["A3"].font = subtitle_font


        ws.append([])

        ws.append(headers)

        header_row = 5

        for cell in ws[header_row]:

            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(
                horizontal="center",
                vertical="center",
            )

            cell.border = thin_border


        ws.freeze_panes = "A6"

        ws.auto_filter.ref = (
            f"A5:{last_column}5"
        )

        return ws


    def finish_sheet(ws):

        # Data styling
        for row in range(
            6,
            ws.max_row + 1,
        ):

            for cell in ws[row]:

                cell.border = thin_border

                cell.alignment = Alignment(
                    vertical="top",
                    wrap_text=True,
                )

                if row % 2 == 0:
                    cell.fill = alternate_fill


        # Widths
        for column_cells in ws.columns:

            column_letter = (
                get_column_letter(
                    column_cells[0].column
                )
            )

            max_length = 0

            for cell in column_cells:

                if cell.value is None:
                    continue

                max_length = max(
                    max_length,
                    len(str(cell.value)),
                )

            ws.column_dimensions[
                column_letter
            ].width = min(
                max(
                    max_length + 3,
                    12,
                ),
                45,
            )


    # ==========================================================
    # BASE TRIPS
    # ==========================================================

    trips = (
        Trip.objects
        .filter(
            trip_date__range=[
                start_date_obj,
                end_date_obj,
            ]
        )
        .select_related(
            "employee",
            "driver",
            "vehicle",
            "route_run",
            "route_run__route_template",
        )
        .order_by(
            "-trip_date",
            "driver__username",
            "pickup_time",
        )
    )


    # ==========================================================
    # 1. TRIP REPORT BY DRIVER
    # ==========================================================

    ws = create_sheet(
        "Trip Report by Driver",
        "CabMate - Trip Report by Driver",
        [
            "Trip ID",
            "Date",
            "Driver",
            "Employee",
            "Vehicle",
            "Vehicle Model",
            "Route",
            "Trip Type",
            "Pickup Location",
            "Drop Location",
            "Pickup Time",
            "Start Time",
            "End Time",
            "Status",
        ],
    )

    for trip in trips:

        route_name = "Manual Route"

        if (
            trip.route_run
            and trip.route_run.route_template
        ):
            route_name = (
                trip.route_run
                .route_template
                .name
            )

        ws.append([
            trip.id,

            trip.trip_date,

            (
                trip.driver.username
                if trip.driver
                else "Unassigned"
            ),

            (
                trip.employee.username
                if trip.employee
                else ""
            ),

            (
                trip.vehicle.vehicle_number
                if trip.vehicle
                else ""
            ),

            (
                trip.vehicle.vehicle_model
                if trip.vehicle
                else ""
            ),

            route_name,

            trip.trip_type,

            trip.pickup_location or "",

            trip.drop_location or "",

            safe_localtime(
                trip.pickup_time
            ),

            safe_localtime(
                trip.start_time
            ),

            safe_localtime(
                trip.end_time
            ),

            trip.status,
        ])

    finish_sheet(ws)


    # ==========================================================
    # 2. CANCELLED TRIPS
    # ==========================================================

    ws = create_sheet(
        "Cancelled Trips",
        "CabMate - Cancelled Trips",
        [
            "Cancellation ID",
            "Trip ID",
            "Trip Date",
            "Employee",
            "Driver",
            "Vehicle",
            "Route",
            "Trip Type",
            "Pickup Location",
            "Drop Location",
            "Cancelled By",
            "Cancelled By Role",
            "Reason",
            "Declaration Accepted",
            "Declaration Text",
            "Cancelled At",
        ],
    )

    cancellations = (
        TripCancellation.objects
        .select_related(
            "trip",
            "trip__employee",
            "trip__driver",
            "trip__vehicle",
            "trip__route_run",
            "trip__route_run__route_template",
            "cancelled_by",
        )
        .filter(
            trip__status=
                Trip.STATUS_CANCELLED,

            trip__trip_date__range=[
                start_date_obj,
                end_date_obj,
            ],
        )
        .order_by(
            "-cancelled_at"
        )
    )

    for cancellation in cancellations:

        trip = cancellation.trip

        route_name = "Manual Route"

        if (
            trip.route_run
            and trip.route_run.route_template
        ):
            route_name = (
                trip.route_run
                .route_template
                .name
            )

        ws.append([
            cancellation.id,

            trip.id,

            trip.trip_date,

            (
                trip.employee.username
                if trip.employee
                else ""
            ),

            (
                trip.driver.username
                if trip.driver
                else ""
            ),

            (
                trip.vehicle.vehicle_number
                if trip.vehicle
                else ""
            ),

            route_name,

            trip.trip_type,

            trip.pickup_location or "",

            trip.drop_location or "",

            (
                cancellation
                .cancelled_by
                .username
                if cancellation.cancelled_by
                else ""
            ),

            (
                cancellation
                .cancelled_by_role
                or ""
            ),

            (
                cancellation.reason
                or ""
            ),

            (
                "YES"
                if cancellation
                .declaration_accepted
                else "NO"
            ),

            (
                cancellation
                .declaration_text
                or ""
            ),

            safe_localtime(
                cancellation
                .cancelled_at
            ),
        ])

    finish_sheet(ws)


    # ==========================================================
    # 3. NO-SHOW REPORT
    # ==========================================================

    ws = create_sheet(
        "No-show Report",
        "CabMate - No-show Report",
        [
            "Date",
            "Employee",
            "Route",
            "Driver",
            "Vehicle",
            "Trip Type",
            "Stop Order",
            "Driver Arrived",
            "Waiting Started",
            "No Show At",
            "Free Wait",
            "Late Wait",
            "Total Waiting",
            "Result",
        ],
    )

    no_show_stops = (
        RouteRunStop.objects
        .select_related(
            "employee",
            "route_run",
            "route_run__driver",
            "route_run__vehicle",
            "route_run__route_template",
        )
        .filter(
            is_no_show=True,

            route_run__run_date__range=[
                start_date_obj,
                end_date_obj,
            ],
        )
        .order_by(
            "-route_run__run_date",
            "-no_show_at",
        )
    )

    for stop in no_show_stops:

        run = stop.route_run

        waiting_started_at = (
            stop.waiting_started_at
            or stop.arrival_time
        )

        total_wait_seconds = 0

        if (
            waiting_started_at
            and stop.no_show_at
        ):

            total_wait_seconds = max(
                0,
                int(
                    (
                        stop.no_show_at
                        - waiting_started_at
                    ).total_seconds()
                ),
            )


        free_limit_seconds = (
            (
                stop.waiting_minutes
                or 10
            )
            * 60
        )

        free_wait_seconds = min(
            total_wait_seconds,
            free_limit_seconds,
        )

        calculated_late = max(
            0,
            total_wait_seconds
            - free_limit_seconds,
        )

        late_seconds = max(
            stop.late_seconds or 0,
            calculated_late,
        )

        ws.append([
            (
                run.run_date
                if run
                else ""
            ),

            (
                stop.employee.username
                if stop.employee
                else ""
            ),

            (
                run.route_template.name
                if (
                    run
                    and run.route_template
                )
                else "Manual Route"
            ),

            (
                run.driver.username
                if (
                    run
                    and run.driver
                )
                else ""
            ),

            (
                run.vehicle.vehicle_number
                if (
                    run
                    and run.vehicle
                )
                else ""
            ),

            (
                run.trip_type
                if run
                else ""
            ),

            stop.stop_order,

            safe_localtime(
                stop.arrival_time
            ),

            safe_localtime(
                waiting_started_at
            ),

            safe_localtime(
                stop.no_show_at
            ),

            format_duration(
                free_wait_seconds
            ),

            format_duration(
                late_seconds
            ),

            format_duration(
                total_wait_seconds
            ),

            "NO SHOW",
        ])

    finish_sheet(ws)


    # ==========================================================
    # 4. LEAVE REPORT
    # ==========================================================

    ws = create_sheet(
        "Leave Report",
        "CabMate - Employee Leave Report",
        [
            "Employee ID",
            "Employee",
            "Phone",
            "Leave Date",
            "Reason",
            "Pickup Cancelled",
            "Drop Cancelled",
            "Total Auto Cancelled",
            "Created At",
        ],
    )

    leave_reports = (
        EmployeeLeave.objects
        .select_related(
            "employee"
        )
        .filter(
            leave_date__range=[
                start_date_obj,
                end_date_obj,
            ]
        )
        .order_by(
            "-leave_date",
            "employee__username",
        )
    )

    for leave in leave_reports:

        employee = leave.employee

        cancelled_for_leave = (
            Trip.objects
            .filter(
                employee=employee,
                trip_date=leave.leave_date,
                status=
                    Trip.STATUS_CANCELLED,
            )
        )

        pickup_cancelled = (
            cancelled_for_leave
            .filter(
                trip_type=
                    Trip.TRIP_TYPE_PICKUP
            )
            .count()
        )

        drop_cancelled = (
            cancelled_for_leave
            .filter(
                trip_type=
                    Trip.TRIP_TYPE_DROP
            )
            .count()
        )

        ws.append([
            (
                employee.employee_id
                or employee.id
            ),

            employee.username,

            getattr(
                employee,
                "phone_number",
                "",
            ) or "",

            leave.leave_date,

            leave.reason or "",

            pickup_cancelled,

            drop_cancelled,

            (
                pickup_cancelled
                + drop_cancelled
            ),

            safe_localtime(
                leave.created_at
            ),
        ])

    finish_sheet(ws)


    # ==========================================================
    # 5. REQUESTS
    # ==========================================================
    #
    # This combines:
    # - Employee account requests
    # - Driver account requests
    # - Pickup location change requests
    # ==========================================================

    ws = create_sheet(
        "Requests",
        "CabMate - Requests & Approvals",
        [
            "Request Type",
            "User ID",
            "Username",
            "Role",
            "Employee ID",
            "Phone",
            "Status",
            "Requested At",
            "Reviewed At",
            "Reviewed By",
            "Details",
        ],
    )


    # ----------------------------------------------------------
    # Employee / Driver account requests
    # ----------------------------------------------------------

    account_requests = (
        User.objects
        .filter(
            role__in=[
                User.Role.EMPLOYEE,
                User.Role.DRIVER,
            ]
        )
        .exclude(
            account_status=
                User.ACCOUNT_STATUS_APPROVED
        )
        .order_by(
            "-date_joined"
        )
    )

    for user in account_requests:

        joined_date = (
            timezone.localtime(
                user.date_joined
            ).date()
            if user.date_joined
            else None
        )

        if (
            joined_date
            and not (
                start_date_obj
                <= joined_date
                <= end_date_obj
            )
        ):
            continue


        reviewed_by = getattr(
            user,
            "account_reviewed_by",
            None,
        )

        ws.append([
            (
                "Employee Account"
                if (
                    user.role
                    == User.Role.EMPLOYEE
                )
                else "Driver Account"
            ),

            user.id,

            user.username,

            user.role,

            (
                user.employee_id
                or ""
            ),

            (
                user.phone_number
                or ""
            ),

            user.account_status,

            safe_localtime(
                user.date_joined
            ),

            safe_localtime(
                getattr(
                    user,
                    "account_reviewed_at",
                    None,
                )
            ),

            (
                reviewed_by.username
                if reviewed_by
                else ""
            ),

            (
                getattr(
                    user,
                    "account_rejection_reason",
                    "",
                )
                or ""
            ),
        ])


    # ----------------------------------------------------------
    # Pickup location change requests
    # ----------------------------------------------------------

    location_requests = (
        PickupLocationChangeRequest.objects
        .select_related(
            "employee",
            "reviewed_by",
        )
        .filter(
            requested_at__date__range=[
                start_date_obj,
                end_date_obj,
            ]
        )
        .order_by(
            "-requested_at"
        )
    )

    for item in location_requests:

        reviewed_by = getattr(
            item,
            "reviewed_by",
            None,
        )

        new_location = (
            getattr(
                item,
                "new_pickup_location",
                "",
            )
            or getattr(
                item,
                "requested_location",
                "",
            )
            or ""
        )

        ws.append([
            "Pickup Location Change",

            item.id,

            (
                item.employee.username
                if item.employee
                else ""
            ),

            "EMPLOYEE",

            (
                item.employee.employee_id
                if (
                    item.employee
                    and item.employee.employee_id
                )
                else ""
            ),

            (
                item.employee.phone_number
                if item.employee
                else ""
            ),

            item.status,

            safe_localtime(
                item.requested_at
            ),

            safe_localtime(
                getattr(
                    item,
                    "reviewed_at",
                    None,
                )
            ),

            (
                reviewed_by.username
                if reviewed_by
                else ""
            ),

            new_location,
        ])

    finish_sheet(ws)


    # ==========================================================
    # 6. REVIEW REPORT
    # ==========================================================

    ws = create_sheet(
        "Review Report",
        "CabMate - Review Report",
        [
            "Review ID",
            "Date",
            "Employee",
            "Driver",
            "Vehicle",
            "Route",
            "Trip ID",
            "Trip Type",
            "Rating",
            "Feedback",
        ],
    )

    review_reports = (
        Review.objects
        .select_related(
            "employee",
            "trip",
            "trip__driver",
            "trip__vehicle",
            "trip__route_run",
            "trip__route_run__route_template",
        )
        .filter(
            created_at__date__range=[
                start_date_obj,
                end_date_obj,
            ]
        )
        .order_by(
            "-created_at"
        )
    )

    for review in review_reports:

        trip = review.trip

        route_name = "Manual Route"

        if (
            trip
            and trip.route_run
            and trip.route_run.route_template
        ):
            route_name = (
                trip.route_run
                .route_template
                .name
            )

        ws.append([
            review.id,

            safe_localtime(
                review.created_at
            ),

            (
                review.employee.username
                if review.employee
                else ""
            ),

            (
                trip.driver.username
                if (
                    trip
                    and trip.driver
                )
                else ""
            ),

            (
                trip.vehicle.vehicle_number
                if (
                    trip
                    and trip.vehicle
                )
                else ""
            ),

            route_name,

            (
                trip.id
                if trip
                else ""
            ),

            (
                trip.trip_type
                if trip
                else ""
            ),

            review.rating,

            review.comment or "",
        ])

    finish_sheet(ws)


    # ==========================================================
    # 7. LATE EMPLOYEES
    # ==========================================================

    ws = create_sheet(
        "Late Employees",
        "CabMate - Late Employees",
        [
            "Date",
            "Employee",
            "Route",
            "Driver",
            "Vehicle",
            "Trip Type",
            "Stop Order",
            "Arrival Time",
            "Finished At",
            "Late Time",
            "Late Seconds",
            "Result",
        ],
    )

    late_stops = (
        RouteRunStop.objects
        .select_related(
            "employee",
            "route_run",
            "route_run__driver",
            "route_run__vehicle",
            "route_run__route_template",
        )
        .filter(
            late_seconds__gt=0,

            route_run__run_date__range=[
                start_date_obj,
                end_date_obj,
            ],
        )
        .order_by(
            "-route_run__run_date",
            "-late_seconds",
        )
    )

    for stop in late_stops:

        run = stop.route_run

        if stop.is_no_show:

            result = "NO SHOW"
            finished_at = stop.no_show_at

        elif stop.is_picked:

            result = "PICKED"
            finished_at = stop.picked_at

        else:

            result = "WAITING"
            finished_at = None


        late_seconds = (
            stop.late_seconds or 0
        )

        ws.append([
            (
                run.run_date
                if run
                else ""
            ),

            (
                stop.employee.username
                if stop.employee
                else ""
            ),

            (
                run.route_template.name
                if (
                    run
                    and run.route_template
                )
                else "Manual Route"
            ),

            (
                run.driver.username
                if (
                    run
                    and run.driver
                )
                else ""
            ),

            (
                run.vehicle.vehicle_number
                if (
                    run
                    and run.vehicle
                )
                else ""
            ),

            (
                run.trip_type
                if run
                else ""
            ),

            stop.stop_order,

            safe_localtime(
                stop.arrival_time
            ),

            safe_localtime(
                finished_at
            ),

            format_duration(
                late_seconds
            ),

            late_seconds,

            result,
        ])

    finish_sheet(ws)


    # ==========================================================
    # 8. DRIVER PERFORMANCE
    # ==========================================================

    ws = create_sheet(
        "Driver Performance",
        "CabMate - Driver Performance",
        [
            "Driver ID",
            "Driver",
            "Phone",
            "Total Trips",
            "Assigned",
            "Started",
            "Completed",
            "Cancelled",
            "Completion %",
            "Average Speed",
            "Maximum Speed",
            "Overspeed Count",
            "Speed Records",
            "Safety Score",
            "Safety Status",
        ],
    )


    speed_history = (
        DriverLocationHistory.objects
        .filter(
            recorded_at__date__range=[
                start_date_obj,
                end_date_obj,
            ]
        )
    )


    speed_stats = (
        speed_history
        .values(
            "driver_id"
        )
        .annotate(
            avg_speed=Avg(
                "speed_kmph"
            ),

            max_speed=Max(
                "speed_kmph"
            ),

            overspeed_count=Count(
                "id",
                filter=Q(
                    is_overspeed=True
                ),
            ),

            speed_records=Count(
                "id"
            ),
        )
    )


    speed_map = {
        item["driver_id"]:
            item
        for item in speed_stats
    }


    drivers = (
        User.objects
        .filter(
            role=User.Role.DRIVER
        )
        .order_by(
            "username"
        )
    )


    for driver in drivers:

        driver_trips = (
            trips.filter(
                driver=driver
            )
        )

        total_trips = (
            driver_trips.count()
        )

        assigned = (
            driver_trips
            .filter(
                status=
                    Trip.STATUS_ASSIGNED
            )
            .count()
        )

        started = (
            driver_trips
            .filter(
                status=
                    Trip.STATUS_STARTED
            )
            .count()
        )

        completed = (
            driver_trips
            .filter(
                status=
                    Trip.STATUS_COMPLETED
            )
            .count()
        )

        cancelled = (
            driver_trips
            .filter(
                status=
                    Trip.STATUS_CANCELLED
            )
            .count()
        )

        completion_percent = (
            round(
                (
                    completed
                    / total_trips
                ) * 100,
                1,
            )
            if total_trips
            else 0
        )


        speed_data = (
            speed_map.get(
                driver.id,
                {},
            )
        )

        avg_speed = round(
            speed_data.get(
                "avg_speed"
            ) or 0,
            1,
        )

        max_speed = round(
            speed_data.get(
                "max_speed"
            ) or 0,
            1,
        )

        overspeed_count = (
            speed_data.get(
                "overspeed_count"
            )
            or 0
        )

        speed_records = (
            speed_data.get(
                "speed_records"
            )
            or 0
        )


        safety_score = 100

        safety_score -= (
            overspeed_count * 5
        )

        safety_score -= (
            cancelled * 3
        )

        safety_score = max(
            0,
            min(
                100,
                safety_score,
            ),
        )


        if safety_score >= 85:

            safety_status = (
                "Safe Driver"
            )

        elif safety_score >= 60:

            safety_status = (
                "Moderate Risk"
            )

        else:

            safety_status = (
                "Risky Driver"
            )


        ws.append([
            driver.id,

            driver.username,

            (
                getattr(
                    driver,
                    "phone_number",
                    "",
                )
                or ""
            ),

            total_trips,

            assigned,

            started,

            completed,

            cancelled,

            completion_percent,

            avg_speed,

            max_speed,

            overspeed_count,

            speed_records,

            safety_score,

            safety_status,
        ])

    finish_sheet(ws)


    # ==========================================================
    # 9. DRIVER CAB SPEED
    # ==========================================================

    ws = create_sheet(
        "Driver Cab Speed",
        "CabMate - Driver Cab Speed Report",
        [
            "Date",
            "Time",
            "Driver",
            "Vehicle",
            "Route",
            "Trip Type",
            "Speed KM/H",
            "Overspeed",
            "Latitude",
            "Longitude",
            "Route Run ID",
        ],
    )


    speed_records = (
        DriverLocationHistory.objects
        .select_related(
            "driver",
            "route_run",
            "route_run__vehicle",
            "route_run__route_template",
        )
        .filter(
            recorded_at__date__range=[
                start_date_obj,
                end_date_obj,
            ]
        )
        .order_by(
            "-recorded_at"
        )
    )


    for record in speed_records:

        local_time = (
            timezone.localtime(
                record.recorded_at
            )
            if record.recorded_at
            else None
        )

        run = record.route_run


        ws.append([
            (
                local_time.date()
                if local_time
                else ""
            ),

            (
                local_time.strftime(
                    "%I:%M:%S %p"
                )
                if local_time
                else ""
            ),

            (
                record.driver.username
                if record.driver
                else ""
            ),

            (
                run.vehicle.vehicle_number
                if (
                    run
                    and run.vehicle
                )
                else ""
            ),

            (
                run.route_template.name
                if (
                    run
                    and run.route_template
                )
                else "Manual Route"
            ),

            (
                record.trip_type
                or (
                    run.trip_type
                    if run
                    else ""
                )
            ),

            round(
                record.speed_kmph
                or 0,
                1,
            ),

            (
                "YES"
                if record.is_overspeed
                else "NO"
            ),

            record.latitude,

            record.longitude,

            (
                run.id
                if run
                else ""
            ),
        ])


        # Highlight overspeed row
        current_row = ws.max_row

        if record.is_overspeed:

            for cell in ws[
                current_row
            ]:

                cell.fill = danger_fill


    finish_sheet(ws)


    # ==========================================================
    # 10. UNASSIGNED EMPLOYEES
    # ==========================================================
    #
    # Same logic as reports_page:
    # active employee with no trip in selected period.
    # ==========================================================

    ws = create_sheet(
        "Unassigned Employees",
        "CabMate - Unassigned Employees",
        [
            "User ID",
            "Employee ID",
            "Employee",
            "Phone",
            "Pickup Location",
            "Address",
            "Account Status",
        ],
    )


    assigned_employee_ids = set(
        trips.values_list(
            "employee_id",
            flat=True,
        )
    )


    unassigned_employees = (
        User.objects
        .filter(
            role=User.Role.EMPLOYEE,
            is_active=True,
        )
        .exclude(
            id__in=
                assigned_employee_ids
        )
        .order_by(
            "username"
        )
    )


    for employee in (
        unassigned_employees
    ):

        ws.append([
            employee.id,

            (
                employee.employee_id
                or ""
            ),

            employee.username,

            (
                employee.phone_number
                or ""
            ),

            (
                employee.pickup_location
                or ""
            ),

            (
                employee.address
                or ""
            ),

            (
                getattr(
                    employee,
                    "account_status",
                    "",
                )
                or ""
            ),
        ])

    finish_sheet(ws)


    # ==========================================================
    # 11. UNASSIGNED DRIVERS
    # ==========================================================
    #
    # Same logic as reports_page:
    # active driver with no trip in selected period.
    # ==========================================================

    ws = create_sheet(
        "Unassigned Drivers",
        "CabMate - Unassigned Drivers",
        [
            "Driver ID",
            "Driver",
            "Phone",
            "Vehicle Number",
            "Vehicle Model",
            "Seats",
            "Account Status",
        ],
    )


    assigned_driver_ids = set(
        trips.exclude(
            driver_id__isnull=True
        ).values_list(
            "driver_id",
            flat=True,
        )
    )


    unassigned_drivers = (
        User.objects
        .filter(
            role=User.Role.DRIVER,
            is_active=True,
        )
        .exclude(
            id__in=
                assigned_driver_ids
        )
        .order_by(
            "username"
        )
    )


    for driver in (
        unassigned_drivers
    ):

        try:
            vehicle = driver.vehicle
        except Exception:
            vehicle = None


        ws.append([
            driver.id,

            driver.username,

            (
                driver.phone_number
                or ""
            ),

            (
                vehicle.vehicle_number
                if vehicle
                else ""
            ),

            (
                vehicle.vehicle_model
                if vehicle
                else ""
            ),

            (
                vehicle.seat_count
                if vehicle
                else ""
            ),

            (
                getattr(
                    driver,
                    "account_status",
                    "",
                )
                or ""
            ),
        ])

    finish_sheet(ws)


    # ==========================================================
    # 12. ROUTE ANALYTICS
    # ==========================================================
    #
    # Uses same calculation as your route_analytics_page().
    # ==========================================================

    ws = create_sheet(
        "Route Analytics",
        "CabMate - Route Analytics",
        [
            "Date",
            "Route Run ID",
            "Route",
            "Trip Type",
            "Driver",
            "Vehicle",
            "Total Stops",
            "Completed Stops",
            "No Shows",
            "Stop Completion %",
            "Total Trips",
            "Completed Trips",
            "Cancelled Trips",
            "Trip Completion %",
            "Duration Minutes",
            "Efficiency Score",
            "Health",
        ],
    )


    route_runs = (
        RouteRun.objects
        .select_related(
            "route_template",
            "driver",
            "vehicle",
        )
        .prefetch_related(
            "stops__employee",
            "trips",
        )
        .filter(
            run_date__range=[
                start_date_obj,
                end_date_obj,
            ]
        )
        .order_by(
            "-run_date",
            "trip_type",
            "route_template__name",
        )
    )


    for run in route_runs:

        run_stops = list(
            run.stops.all()
        )

        run_trips = list(
            run.trips.all()
        )

        total_stops = len(
            run_stops
        )

        completed_stops = len([
            stop
            for stop in run_stops
            if getattr(
                stop,
                "is_picked",
                False,
            )
        ])

        no_show_count = len([
            stop
            for stop in run_stops
            if getattr(
                stop,
                "is_no_show",
                False,
            )
        ])

        total_run_trips = len(
            run_trips
        )

        completed_run_trips = len([
            trip
            for trip in run_trips
            if (
                trip.status
                == Trip.STATUS_COMPLETED
            )
        ])

        cancelled_run_trips = len([
            trip
            for trip in run_trips
            if (
                trip.status
                == Trip.STATUS_CANCELLED
            )
        ])


        stop_completion_percent = (
            round(
                (
                    completed_stops
                    / total_stops
                ) * 100,
                1,
            )
            if total_stops
            else 0
        )


        trip_completion_percent = (
            round(
                (
                    completed_run_trips
                    / total_run_trips
                ) * 100,
                1,
            )
            if total_run_trips
            else 0
        )


        duration_minutes = 0

        if (
            run.started_at
            and run.completed_at
        ):

            duration_minutes = int(
                (
                    run.completed_at
                    - run.started_at
                ).total_seconds()
                / 60
            )


        efficiency_score = 100

        efficiency_score -= (
            no_show_count * 8
        )

        efficiency_score -= (
            cancelled_run_trips
            * 5
        )


        if duration_minutes:

            duration_limit = (
                120
                if (
                    run.trip_type
                    == Trip.TRIP_TYPE_PICKUP
                )
                else 90
            )

            if (
                duration_minutes
                > duration_limit
            ):

                efficiency_score -= 15


        efficiency_score = max(
            0,
            min(
                100,
                efficiency_score,
            ),
        )


        if efficiency_score >= 85:

            health = "EXCELLENT"

        elif efficiency_score >= 60:

            health = "MODERATE"

        else:

            health = "CRITICAL"


        ws.append([
            run.run_date,

            run.id,

            (
                run.route_template.name
                if run.route_template
                else "Manual Route"
            ),

            run.trip_type,

            (
                run.driver.username
                if run.driver
                else ""
            ),

            (
                run.vehicle.vehicle_number
                if run.vehicle
                else ""
            ),

            total_stops,

            completed_stops,

            no_show_count,

            stop_completion_percent,

            total_run_trips,

            completed_run_trips,

            cancelled_run_trips,

            trip_completion_percent,

            duration_minutes,

            efficiency_score,

            health,
        ])


        current_row = ws.max_row

        if health == "CRITICAL":

            for cell in ws[
                current_row
            ]:
                cell.fill = danger_fill

        elif health == "MODERATE":

            for cell in ws[
                current_row
            ]:
                cell.fill = warning_fill

        else:

            for cell in ws[
                current_row
            ]:
                cell.fill = success_fill


    finish_sheet(ws)


    # ==========================================================
    # RESPONSE
    # ==========================================================

    filename = (
        "CabMate_All_Reports_"
        f"{start_date_obj}_to_"
        f"{end_date_obj}.xlsx"
    )


    response = HttpResponse(
        content_type=(
            "application/"
            "vnd.openxmlformats-"
            "officedocument."
            "spreadsheetml.sheet"
        )
    )


    response[
        "Content-Disposition"
    ] = (
        f'attachment; '
        f'filename="{filename}"'
    )


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

# ============================================================
# REVIEW REPORT
# ============================================================

@login_required
@admin_required
@require_GET
def review_report_page(request):

    today = timezone.localdate()

    # --------------------------------------------------------
    # FILTER VALUES
    # --------------------------------------------------------

    query = request.GET.get("q", "").strip()

    driver_filter = request.GET.get(
        "driver",
        "",
    ).strip()

    employee_filter = request.GET.get(
        "employee",
        "",
    ).strip()

    rating_filter = request.GET.get(
        "rating",
        "",
    ).strip()

    trip_type_filter = request.GET.get(
        "trip_type",
        "",
    ).strip().upper()

    start_date_value = request.GET.get(
        "start_date",
        "",
    ).strip()

    end_date_value = request.GET.get(
        "end_date",
        "",
    ).strip()

    # --------------------------------------------------------
    # CURRENT MONTH
    # --------------------------------------------------------

    month_start = today.replace(day=1)

    # --------------------------------------------------------
    # BASE REVIEW QUERY
    # --------------------------------------------------------

    all_reviews = (
        Review.objects
        .select_related(
            "employee",
            "trip",
            "trip__driver",
            "trip__vehicle",
            "trip__route_run",
            "trip__route_run__route_template",
        )
        .order_by("-created_at")
    )

    # --------------------------------------------------------
    # TODAY REVIEWS
    # --------------------------------------------------------

    today_reviews = all_reviews.filter(
        created_at__date=today
    )

    today_review_count = today_reviews.count()

    today_average = (
        today_reviews.aggregate(
            average=Avg("rating")
        )["average"]
        or 0
    )

    # --------------------------------------------------------
    # CURRENT MONTH REVIEWS
    # --------------------------------------------------------

    month_reviews = all_reviews.filter(
        created_at__date__gte=month_start,
        created_at__date__lte=today,
    )

    month_review_count = month_reviews.count()

    month_average = (
        month_reviews.aggregate(
            average=Avg("rating")
        )["average"]
        or 0
    )

    # --------------------------------------------------------
    # OVERALL REVIEW STATS
    # --------------------------------------------------------

    total_reviews = all_reviews.count()

    overall_average = (
        all_reviews.aggregate(
            average=Avg("rating")
        )["average"]
        or 0
    )

    five_star_count = all_reviews.filter(
        rating=5
    ).count()

    four_star_count = all_reviews.filter(
        rating=4
    ).count()

    three_star_count = all_reviews.filter(
        rating=3
    ).count()

    low_rating_count = all_reviews.filter(
        rating__lte=2
    ).count()

    # --------------------------------------------------------
    # DRIVER MONTHLY PERFORMANCE
    # --------------------------------------------------------

    driver_month_stats = (
        month_reviews
        .filter(
            trip__driver__isnull=False
        )
        .values(
            "trip__driver_id",
            "trip__driver__username",
        )
        .annotate(
            total_reviews=Count("id"),
            average_rating=Avg("rating"),
        )
        .order_by(
            "-average_rating",
            "-total_reviews",
        )
    )

    # --------------------------------------------------------
    # TODAY DRIVER PERFORMANCE
    # --------------------------------------------------------

    driver_today_stats = (
        today_reviews
        .filter(
            trip__driver__isnull=False
        )
        .values(
            "trip__driver_id",
        )
        .annotate(
            today_reviews=Count("id"),
            today_average=Avg("rating"),
        )
    )

    today_driver_map = {
        item["trip__driver_id"]: item
        for item in driver_today_stats
    }

    # --------------------------------------------------------
    # BUILD DRIVER SUMMARY
    # --------------------------------------------------------

    driver_summary = []

    for driver in driver_month_stats:

        driver_id = driver[
            "trip__driver_id"
        ]

        today_data = today_driver_map.get(
            driver_id,
            {},
        )

        driver_summary.append({
            "driver_id":
                driver_id,

            "driver_name":
                driver[
                    "trip__driver__username"
                ],

            "today_reviews":
                today_data.get(
                    "today_reviews",
                    0,
                ),

            "today_average":
                round(
                    today_data.get(
                        "today_average",
                        0,
                    ) or 0,
                    2,
                ),

            "month_reviews":
                driver[
                    "total_reviews"
                ],

            "month_average":
                round(
                    driver[
                        "average_rating"
                    ] or 0,
                    2,
                ),
        })

    # --------------------------------------------------------
    # FILTERED DETAILED REVIEW LIST
    # --------------------------------------------------------

    reviews = all_reviews

    parsed_start_date = (
        parse_date(start_date_value)
        if start_date_value
        else None
    )

    parsed_end_date = (
        parse_date(end_date_value)
        if end_date_value
        else None
    )

    if parsed_start_date:
        reviews = reviews.filter(
            created_at__date__gte=parsed_start_date
        )

    if parsed_end_date:
        reviews = reviews.filter(
            created_at__date__lte=parsed_end_date
        )

    # --------------------------------------------------------
    # SEARCH
    # Employee / Driver / Vehicle / Comment / Route
    # --------------------------------------------------------

    if query:

        reviews = reviews.filter(

            Q(
                employee__username__icontains=query
            )

            |

            Q(
                trip__driver__username__icontains=query
            )

            |

            Q(
                trip__vehicle__vehicle_number__icontains=query
            )

            |

            Q(
                comment__icontains=query
            )

            |

            Q(
                trip__route_run__route_template__name__icontains=query
            )

        )

    # --------------------------------------------------------
    # DRIVER FILTER
    # --------------------------------------------------------

    if driver_filter:

        reviews = reviews.filter(
            trip__driver_id=driver_filter
        )

    # --------------------------------------------------------
    # EMPLOYEE FILTER
    # --------------------------------------------------------

    if employee_filter:

        reviews = reviews.filter(
            employee_id=employee_filter
        )

    # --------------------------------------------------------
    # RATING FILTER
    # --------------------------------------------------------

    if rating_filter in [
        "1",
        "2",
        "3",
        "4",
        "5",
    ]:

        reviews = reviews.filter(
            rating=int(rating_filter)
        )

    # --------------------------------------------------------
    # TRIP TYPE FILTER
    # --------------------------------------------------------

    if trip_type_filter in [
        "PICKUP",
        "DROP",
    ]:

        reviews = reviews.filter(
            trip__trip_type=trip_type_filter
        )

    # --------------------------------------------------------
    # BUILD DETAIL ROWS
    # --------------------------------------------------------

    review_rows = []

    for review in reviews:

        trip = review.trip

        driver = (
            trip.driver
            if trip
            else None
        )

        vehicle = (
            trip.vehicle
            if trip
            else None
        )

        route_run = (
            trip.route_run
            if trip
            else None
        )

        route_name = "--"

        if (
            route_run
            and route_run.route_template
        ):
            route_name = (
                route_run
                .route_template
                .name
            )

        review_rows.append({

            "id":
                review.id,

            "date":
                timezone.localtime(
                    review.created_at
                ).date(),

            "time":
                timezone.localtime(
                    review.created_at
                ).time(),

            "employee":
                review.employee.username
                if review.employee
                else "--",

            "driver":
                driver.username
                if driver
                else "--",

            "driver_id":
                driver.id
                if driver
                else None,

            "vehicle":
                vehicle.vehicle_number
                if vehicle
                else "--",

            "route_name":
                route_name,

            "trip_type":
                trip.trip_type
                if trip
                else "--",

            "trip_id":
                trip.id
                if trip
                else None,

            "rating":
                review.rating,

            "comment":
                review.comment
                or "No feedback comment",

        })

    # --------------------------------------------------------
    # DROPDOWN DATA
    # --------------------------------------------------------

    drivers = (
        User.objects
        .filter(
            role="DRIVER",
            is_active=True,
        )
        .order_by("username")
    )

    employees = (
        User.objects
        .filter(
            role="EMPLOYEE",
            is_active=True,
        )
        .order_by("username")
    )

    # --------------------------------------------------------
    # CONTEXT
    # --------------------------------------------------------

    context = {

        "today":
            today,

        "month_start":
            month_start,

        # Main summary
        "total_reviews":
            total_reviews,

        "overall_average":
            round(
                overall_average,
                2,
            ),

        "today_review_count":
            today_review_count,

        "today_average":
            round(
                today_average,
                2,
            ),

        "month_review_count":
            month_review_count,

        "month_average":
            round(
                month_average,
                2,
            ),

        # Rating counts
        "five_star_count":
            five_star_count,

        "four_star_count":
            four_star_count,

        "three_star_count":
            three_star_count,

        "low_rating_count":
            low_rating_count,

        # Driver report
        "driver_summary":
            driver_summary,

        # Detailed report
        "review_rows":
            review_rows,

        "filtered_review_count":
            len(review_rows),

        # Dropdowns
        "drivers":
            drivers,

        "employees":
            employees,

        # Preserve filters
        "query":
            query,

        "driver_filter":
            driver_filter,

        "employee_filter":
            employee_filter,

        "rating_filter":
            rating_filter,

        "trip_type_filter":
            trip_type_filter,

        "start_date_value":
            start_date_value,

        "end_date_value":
            end_date_value,
    }

    return render(
        request,
        "admin_web/review_report.html",
        context,
    )

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

    # -------------------------
    # DATE FILTER
    # -------------------------
    parsed_date = parse_date(date_filter) if date_filter else None

    if parsed_date:
        runs = runs.filter(run_date=parsed_date)

    # -------------------------
    # TRIP TYPE FILTER
    # -------------------------
    if trip_type_filter in ["PICKUP", "DROP"]:
        runs = runs.filter(trip_type=trip_type_filter)

    # -------------------------
    # SEARCH
    # -------------------------
    if query:
        runs = runs.filter(
            Q(route_template__name__icontains=query)
            | Q(driver__username__icontains=query)
            | Q(vehicle__vehicle_number__icontains=query)
            | Q(stops__employee__username__icontains=query)
            | Q(stops__pickup_location__icontains=query)
        ).distinct()

    runs = runs.order_by(
        "run_date",
        "trip_type",
        "route_template__name",
    )

    grouped = OrderedDict()

    total_assigned = 0
    total_started = 0

    # ==========================================================
    # BUILD ROUTE RUN CARDS
    # ==========================================================

    for run in runs:

        active_trips = list(
            run.trips.filter(
                status__in=[
                    Trip.STATUS_ASSIGNED,
                    Trip.STATUS_STARTED,
                ]
            ).select_related("employee")
        )

        if not active_trips:
            continue

        trip_map = {
            trip.employee_id: trip
            for trip in active_trips
        }

        # -------------------------
        # COUNTS
        # -------------------------

        assigned_count = sum(
            1
            for trip in active_trips
            if trip.status == Trip.STATUS_ASSIGNED
        )

        started_count = sum(
            1
            for trip in active_trips
            if trip.status == Trip.STATUS_STARTED
        )

        total_assigned += assigned_count
        total_started += started_count

        # -------------------------
        # STOPS
        # -------------------------

        stops = list(
            run.stops
            .select_related("employee")
            .order_by("stop_order")
        )

        completed_employees = len([
            stop
            for stop in stops
            if (
                stop.employee_id in trip_map
                and getattr(stop, "is_picked", False)
            )
        ])

        total_employees = len([
            stop
            for stop in stops
            if stop.employee_id in trip_map
        ])

        completion_percent = (
            int(
                (completed_employees / total_employees) * 100
            )
            if total_employees
            else 0
        )

        # -------------------------
        # CURRENT STOP
        # -------------------------

        current_stop = next(
            (
                stop
                for stop in stops
                if (
                    stop.employee_id in trip_map
                    and not getattr(
                        stop,
                        "is_picked",
                        False,
                    )
                )
            ),
            None,
        )

        if current_stop and current_stop.employee:
            current_stop_name = (
                current_stop.employee.username
            )

        elif completion_percent == 100:
            current_stop_name = "Completed"

        else:
            current_stop_name = "Not started"

        # -------------------------
        # ETA
        # -------------------------

        eta_text = "--"

        if started_count:
            remaining = (
                total_employees
                - completed_employees
            )

            eta_text = (
                f"{remaining * 7} min"
                if remaining > 0
                else "Completed"
            )

        # -------------------------
        # EMPLOYEE LIST
        # -------------------------

        employees = []

        for stop in stops:

            trip = trip_map.get(
                stop.employee_id
            )

            if not trip:
                continue

            employees.append({
                "trip_id": trip.id,

                "employee_name": (
                    stop.employee.username
                    if stop.employee
                    else "--"
                ),

                "pickup_location": (
                    stop.pickup_location
                    or "--"
                ),

                "drop_location": (
                    trip.drop_location
                    or "Office"
                ),

                "status": trip.status,

                "is_picked": getattr(
                    stop,
                    "is_picked",
                    False,
                ),

                "is_no_show": getattr(
                    stop,
                    "is_no_show",
                    False,
                ),

                "is_current": bool(
                    current_stop
                    and stop.id
                    == current_stop.id
                ),

                "stop_order": (
                    stop.stop_order
                ),
            })

        # -------------------------
        # FIRST TRIP
        # -------------------------

        first_trip = (
            active_trips[0]
            if active_trips
            else None
        )

        # ======================================================
        # LIVE / HEALTH VALUES
        # ======================================================

        is_live = started_count > 0

        # Currently these are static.
        # Later we can calculate these using
        # actual driver location / ETA data.

        is_delayed = False
        is_overspeed = False

        if is_overspeed:
            health_status = "CRITICAL"

        elif is_delayed:
            health_status = "MODERATE"

        else:
            health_status = "EXCELLENT"

        # ======================================================
        # CREATE CARD
        # ======================================================

        card = {
            "id": run.id,

            "route_name": (
                run.route_template.name
                if run.route_template
                else "Manual Route"
            ),

            "driver_name": (
                run.driver.username
                if run.driver
                else "--"
            ),

            "vehicle_number": (
                run.vehicle.vehicle_number
                if run.vehicle
                else "--"
            ),

            "trip_type": run.trip_type,

            "run_date": run.run_date,

            "pickup_time": (
                first_trip.pickup_time
                if first_trip
                else None
            ),

            "status": (
                Trip.STATUS_STARTED
                if started_count
                else Trip.STATUS_ASSIGNED
            ),

            "total_employees": (
                total_employees
            ),

            "assigned_count": (
                assigned_count
            ),

            "started_count": (
                started_count
            ),

            "completed_employees": (
                completed_employees
            ),

            "completion_percent": (
                completion_percent
            ),

            "current_stop_name": (
                current_stop_name
            ),

            "eta_text": (
                eta_text
            ),

            "employees": (
                employees
            ),

            "health_status": (
                health_status
            ),

            "is_live": (
                is_live
            ),

            "is_delayed": (
                is_delayed
            ),

            "is_overspeed": (
                is_overspeed
            ),
        }

        # ======================================================
        # DATE GROUP
        # ======================================================

        if run.run_date == today:
            label = "Today's Date"

        elif run.run_date == tomorrow:
            label = "Tomorrow's Date"

        else:
            label = run.run_date.strftime(
                "%d %b %Y"
            )

        if label not in grouped:

            grouped[label] = {
                "date_value": run.run_date,
                "pickup_cards": [],
                "drop_cards": [],
            }

        # -------------------------
        # PICKUP / DROP
        # -------------------------

        if run.trip_type == Trip.TRIP_TYPE_PICKUP:

            grouped[label][
                "pickup_cards"
            ].append(card)

        else:

            grouped[label][
                "drop_cards"
            ].append(card)

    # ==========================================================
    # TEMPLATE CONTEXT
    # ==========================================================

    context = {
        "date_sections": grouped.items(),

        "query": query,

        "date_filter": date_filter,

        "trip_type_filter": (
            trip_type_filter
        ),

        "total_assigned": (
            total_assigned
        ),

        "total_started": (
            total_started
        ),

        "total_active": (
            total_assigned
            + total_started
        ),
    }

    return render(
        request,
        "admin_web/assigned_trips.html",
        context,
    )
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
@require_GET
def cancelled_trips_page(request):
    query = request.GET.get("q", "").strip()
    date_filter = request.GET.get("date", "").strip()
    trip_type_filter = request.GET.get(
        "trip_type",
        "",
    ).strip().upper()

    cancelled_by_filter = request.GET.get(
        "cancelled_by",
        "",
    ).strip().upper()

    declaration_filter = request.GET.get(
        "declaration",
        "",
    ).strip().upper()

    # ============================================================
    # BASE QUERY
    # ============================================================

    cancellations = (
        TripCancellation.objects
        .select_related(
            "trip",
            "trip__employee",
            "trip__driver",
            "trip__vehicle",
            "trip__route_run",
            "trip__route_run__route_template",
            "cancelled_by",
        )
        .filter(
            trip__status=Trip.STATUS_CANCELLED,
        )
        .order_by(
            "-cancelled_at",
        )
    )

    # ============================================================
    # SEARCH
    # ============================================================

    if query:
        cancellations = cancellations.filter(
            Q(
                trip__employee__username__icontains=query
            )
            |
            Q(
                trip__driver__username__icontains=query
            )
            |
            Q(
                trip__vehicle__vehicle_number__icontains=query
            )
            |
            Q(
                trip__route_run__route_template__name__icontains=query
            )
            |
            Q(
                reason__icontains=query
            )
        )

    # ============================================================
    # DATE FILTER
    # ============================================================

    parsed_date = (
        parse_date(date_filter)
        if date_filter
        else None
    )

    if parsed_date:
        cancellations = cancellations.filter(
            trip__trip_date=parsed_date,
        )

    # ============================================================
    # TRIP TYPE FILTER
    # ============================================================

    if trip_type_filter in [
        Trip.TRIP_TYPE_PICKUP,
        Trip.TRIP_TYPE_DROP,
    ]:
        cancellations = cancellations.filter(
            trip__trip_type=trip_type_filter,
        )

    # ============================================================
    # CANCELLED BY FILTER
    # ============================================================

    if cancelled_by_filter in [
        "EMPLOYEE",
        "ADMIN",
        "DRIVER",
    ]:
        cancellations = cancellations.filter(
            cancelled_by_role=cancelled_by_filter,
        )

    # ============================================================
    # DECLARATION FILTER
    # ============================================================

    if declaration_filter == "SUBMITTED":
        cancellations = cancellations.filter(
            declaration_accepted=True,
        )

    elif declaration_filter == "NOT_SUBMITTED":
        cancellations = cancellations.filter(
            declaration_accepted=False,
        )

    # ============================================================
    # SUMMARY COUNTS
    # These are based on all cancellation records.
    # ============================================================

    today = timezone.localdate()

    all_cancellations = TripCancellation.objects.filter(
        trip__status=Trip.STATUS_CANCELLED,
    )

    total_cancelled = all_cancellations.count()

    today_cancelled = all_cancellations.filter(
        cancelled_at__date=today,
    ).count()

    employee_cancelled = all_cancellations.filter(
        cancelled_by_role="EMPLOYEE",
    ).count()

    admin_cancelled = all_cancellations.filter(
        cancelled_by_role="ADMIN",
    ).count()

    declarations_submitted = all_cancellations.filter(
        declaration_accepted=True,
    ).count()

    # ============================================================
    # BUILD ROWS
    # ============================================================

    rows = []

    for cancellation in cancellations:
        trip = cancellation.trip
        route_run = trip.route_run

        route_name = "Manual Route"

        if (
            route_run
            and route_run.route_template
        ):
            route_name = (
                route_run.route_template.name
            )

        rows.append(
            {
                "id": cancellation.id,

                "trip_id": trip.id,

                "employee_name": (
                    trip.employee.username
                    if trip.employee
                    else "--"
                ),

                "trip_type": trip.trip_type,

                "trip_date": trip.trip_date,

                "route_name": route_name,

                "driver_name": (
                    trip.driver.username
                    if trip.driver
                    else "--"
                ),

                "vehicle_number": (
                    trip.vehicle.vehicle_number
                    if trip.vehicle
                    else "--"
                ),

                "pickup_location": (
                    trip.pickup_location
                    or "--"
                ),

                "drop_location": (
                    trip.drop_location
                    or "--"
                ),

                "cancelled_by": (
                    cancellation.cancelled_by.username
                    if cancellation.cancelled_by
                    else "--"
                ),

                "cancelled_by_role": (
                    cancellation.cancelled_by_role
                    or "--"
                ),

                "reason": (
                    cancellation.reason
                    or "No reason provided"
                ),

                "cancelled_at": (
                    cancellation.cancelled_at
                ),

                "declaration_accepted": (
                    cancellation.declaration_accepted
                ),

                "declaration_text": (
                    cancellation.declaration_text
                    or ""
                ),
            }
        )

    # ============================================================
    # CONTEXT
    # ============================================================

    context = {
        "rows": rows,

        "query": query,
        "date_filter": date_filter,
        "trip_type_filter": trip_type_filter,
        "cancelled_by_filter": cancelled_by_filter,
        "declaration_filter": declaration_filter,

        "total_cancelled": total_cancelled,
        "today_cancelled": today_cancelled,
        "employee_cancelled": employee_cancelled,
        "admin_cancelled": admin_cancelled,
        "declarations_submitted": declarations_submitted,
    }

    return render(
        request,
        "admin_web/cancelled_trips.html",
        context,
    )

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