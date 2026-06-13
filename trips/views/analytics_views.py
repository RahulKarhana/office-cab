from django.db.models import Count, Avg, Q, F
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from trips.models import (
    Trip,
    RouteRun,
    RouteRunStop,
    EmergencyAlert,
    Review,
)

from django.contrib.auth import get_user_model

User = get_user_model()


@api_view(["GET"])
@permission_classes([])
def analytics_dashboard(request):

    today = timezone.localdate()

    completed_runs = RouteRun.objects.filter(
        completed_at__isnull=False
    )

    total_completed_routes = completed_runs.count()

    total_cancelled_trips = Trip.objects.filter(
        status="CANCELLED"
    ).count()

    active_sos = EmergencyAlert.objects.filter(
        status="ACTIVE"
    ).count()

    # =========================
    # DRIVER PUNCTUALITY
    # =========================

    punctual_drivers = []

    drivers = User.objects.filter(role="DRIVER")

    for driver in drivers:

        driver_runs = RouteRun.objects.filter(
            driver=driver,
            completed_at__isnull=False
        )

        total_runs = driver_runs.count()

        on_time_runs = driver_runs.filter(
            started_at__isnull=False
        ).count()

        punctuality_score = 0

        if total_runs > 0:
            punctuality_score = round(
                (on_time_runs / total_runs) * 100,
                2
            )

        punctual_drivers.append({
            "driver_id": driver.id,
            "driver_name": driver.username,
            "total_runs": total_runs,
            "on_time_runs": on_time_runs,
            "punctuality_score": punctuality_score,
        })

    punctual_drivers = sorted(
        punctual_drivers,
        key=lambda x: x["punctuality_score"],
        reverse=True
    )[:5]

    # =========================
    # MOST DELAYED ROUTES
    # =========================

    delayed_routes = []

    route_runs = RouteRun.objects.filter(
        completed_at__isnull=False
    )

    for run in route_runs:

        if not run.started_at or not run.completed_at:
            continue

        duration_minutes = (
            run.completed_at - run.started_at
        ).total_seconds() / 60

        delayed_routes.append({
            "route_name": run.route_template.name if run.route_template else "Unknown",
            "driver_name": run.driver.username if run.driver else "--",
            "trip_type": run.trip_type,
            "duration_minutes": round(duration_minutes, 2),
        })

    delayed_routes = sorted(
        delayed_routes,
        key=lambda x: x["duration_minutes"],
        reverse=True
    )[:5]

    # =========================
    # REVIEW ANALYTICS
    # =========================

    avg_rating = Review.objects.aggregate(
        avg=Avg("rating")
    )["avg"] or 0

    low_rating_reviews = Review.objects.filter(
        rating__lte=3
    ).count()

    # =========================
    # RESPONSE
    # =========================

    return Response({

        "summary": {
            "completed_routes": total_completed_routes,
            "cancelled_trips": total_cancelled_trips,
            "active_sos": active_sos,
            "average_rating": round(avg_rating, 2),
            "low_rating_reviews": low_rating_reviews,
        },

        "top_punctual_drivers": punctual_drivers,

        "most_delayed_routes": delayed_routes,

    })