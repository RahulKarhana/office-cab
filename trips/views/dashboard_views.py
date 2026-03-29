from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from trips.models import Trip, RouteRun


class DashboardAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        today = timezone.localdate()

        if user.role == "ADMIN":
            today_trips = Trip.objects.filter(trip_date=today)

            total_trips = today_trips.count()
            completed = today_trips.filter(status=Trip.STATUS_COMPLETED).count()
            ongoing = today_trips.filter(status=Trip.STATUS_STARTED).count()
            assigned = today_trips.filter(status=Trip.STATUS_ASSIGNED).count()
            cancelled = today_trips.filter(status=Trip.STATUS_CANCELLED).count()

            # -----------------------------
            # OPERATIONAL COUNTS
            # -----------------------------
            today_route_runs = RouteRun.objects.filter(run_date=today)

            # Active cab = started route runs not yet completed
            active_cabs = today_route_runs.filter(
                started_at__isnull=False,
                completed_at__isnull=True,
            ).count()

            # Assigned route groups = created for today but not started yet
            assigned_routes = today_route_runs.filter(
                started_at__isnull=True,
                completed_at__isnull=True,
            ).count()

            # Drivers on duty = distinct drivers who have any route run today
            drivers_on_duty = (
                today_route_runs.values("driver_id").distinct().count()
            )

            # Completed route runs
            completed_routes = today_route_runs.filter(
                completed_at__isnull=False
            ).count()

            # Employee trips currently onboard / running
            employees_onboard = ongoing

            # Completion %
            completion_rate = round((completed / total_trips) * 100) if total_trips > 0 else 0

            return Response({
                "scope": "today",
                "date": str(today),

                # Employee trip stats
                "total_trips": total_trips,
                "completed": completed,
                "ongoing": ongoing,
                "assigned": assigned,
                "cancelled": cancelled,
                "completion_rate": completion_rate,

                # Operational stats
                "active_cabs": active_cabs,
                "assigned_routes": assigned_routes,
                "completed_routes": completed_routes,
                "drivers_on_duty": drivers_on_duty,
                "employees_onboard": employees_onboard,
            })

        elif user.role == "EMPLOYEE":
            my_today_trips = Trip.objects.filter(
                employee=user,
                trip_date=today,
            )

            total_my_trips = my_today_trips.count()
            completed = my_today_trips.filter(
                status=Trip.STATUS_COMPLETED
            ).count()
            ongoing = my_today_trips.filter(
                status=Trip.STATUS_STARTED
            ).count()
            assigned = my_today_trips.filter(
                status=Trip.STATUS_ASSIGNED
            ).count()
            cancelled = my_today_trips.filter(
                status=Trip.STATUS_CANCELLED
            ).count()

            completion_rate = round((completed / total_my_trips) * 100) if total_my_trips > 0 else 0

            return Response({
                "scope": "today",
                "date": str(today),
                "my_trips": total_my_trips,
                "completed": completed,
                "ongoing": ongoing,
                "assigned": assigned,
                "cancelled": cancelled,
                "completion_rate": completion_rate,
            })

        elif user.role == "DRIVER":
            my_today_trips = Trip.objects.filter(
                driver=user,
                trip_date=today,
            )

            my_route_runs = RouteRun.objects.filter(
                driver=user,
                run_date=today,
            )

            assigned = my_today_trips.filter(
                status=Trip.STATUS_ASSIGNED
            ).count()
            ongoing = my_today_trips.filter(
                status=Trip.STATUS_STARTED
            ).count()
            completed = my_today_trips.filter(
                status=Trip.STATUS_COMPLETED
            ).count()
            cancelled = my_today_trips.filter(
                status=Trip.STATUS_CANCELLED
            ).count()

            active_routes = my_route_runs.filter(
                started_at__isnull=False,
                completed_at__isnull=True,
            ).count()

            assigned_routes = my_route_runs.filter(
                started_at__isnull=True,
                completed_at__isnull=True,
            ).count()

            completed_routes = my_route_runs.filter(
                completed_at__isnull=False
            ).count()

            total_my_trips = my_today_trips.count()
            completion_rate = round((completed / total_my_trips) * 100) if total_my_trips > 0 else 0

            return Response({
                "scope": "today",
                "date": str(today),

                # Employee-trip based stats
                "assigned": assigned,
                "ongoing": ongoing,
                "completed": completed,
                "cancelled": cancelled,
                "total_trips": total_my_trips,
                "completion_rate": completion_rate,

                # Route/cab operational stats
                "active_routes": active_routes,
                "assigned_routes": assigned_routes,
                "completed_routes": completed_routes,
            })

        return Response({
            "detail": "Invalid role"
        }, status=400)