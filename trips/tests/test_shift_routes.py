"""CabMate operational-shift regression tests.
Place at trips/tests/test_shift_routes.py and run:
    python manage.py test trips.tests.test_shift_routes
"""
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from trips.models import RouteRun, RouteRunStop, RouteTemplate, Trip, Vehicle
from trips.views.route_run_views import RouteRunViewSet


@override_settings(TIME_ZONE="Asia/Kolkata", USE_TZ=True)
class ShiftRouteTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.driver = User.objects.create_user(username="shift_driver", password="test-pass", role="DRIVER")
        cls.other_driver = User.objects.create_user(username="other_shift_driver", password="test-pass", role="DRIVER")
        cls.employee = User.objects.create_user(username="shift_employee", password="test-pass", role="EMPLOYEE")
        cls.vehicle = Vehicle.objects.create(driver=cls.driver, vehicle_number="TEST-SHIFT-01", vehicle_model="Test Cab", seat_count=4)
        cls.route = RouteTemplate.objects.create(name="Shift Test Route", driver=cls.driver, vehicle=cls.vehicle)
        cls.factory = APIRequestFactory()

    def local_dt(self, day, hour, minute=0):
        return datetime(day.year, day.month, day.day, hour, minute, tzinfo=ZoneInfo("Asia/Kolkata"))

    def make_run(self, shift_day, trip_type, departure, *, started=None, completed=None, trip_status=Trip.STATUS_ASSIGNED):
        run = RouteRun.objects.create(route_template=self.route, driver=self.driver, vehicle=self.vehicle,
                                      trip_type=trip_type, run_date=shift_day,
                                      started_at=started, completed_at=completed)
        RouteRunStop.objects.create(
            route_run=run, employee=self.employee, pickup_location="Home",
            stop_order=1,
        )
        Trip.objects.create(employee=self.employee, driver=self.driver, vehicle=self.vehicle, route_run=run,
                            trip_type=trip_type, pickup_location="Home", drop_location="Office",
                            pickup_time=departure, status=trip_status)
        return run

    def call(self, method, action, user, *, run=None):
        view = RouteRunViewSet.as_view({method.lower(): action})
        path = f"/trips/route-runs/{run.pk}/{action}/" if run else f"/trips/route-runs/{action}/"
        req = getattr(self.factory, method.lower())(path)
        force_authenticate(req, user=user)
        return view(req, pk=run.pk) if run else view(req)

    def test_future_route_cannot_start(self):
        now = self.local_dt(timezone.localdate(), 12)
        future_day = now.date() + timedelta(days=7)
        run = self.make_run(future_day, Trip.TRIP_TYPE_PICKUP, self.local_dt(future_day, 9))
        with patch("trips.views.route_run_views.timezone.now", return_value=now):
            response = self.call("post", "start_run", self.driver, run=run)
        self.assertEqual(response.status_code, 400)
        run.refresh_from_db()
        self.assertIsNone(run.started_at)

    def test_start_within_window(self):
        day = timezone.localdate()
        departure = self.local_dt(day, 10)
        run = self.make_run(day, Trip.TRIP_TYPE_PICKUP, departure)
        # Stub outbound push notification; verify route and trip transitions.
        with patch("trips.views.route_run_views.timezone.now", return_value=self.local_dt(day, 9)), \
             patch.object(RouteRunViewSet, "_send_pickup_start_notifications"):
            response = self.call("post", "start_run", self.driver, run=run)
        self.assertEqual(response.status_code, 200, response.data)
        run.refresh_from_db()
        self.assertIsNotNone(run.started_at)
        self.assertEqual(run.trips.first().status, Trip.STATUS_STARTED)

    def test_overnight_drop_keeps_previous_shift(self):
        day = timezone.localdate()
        prior = day - timedelta(days=1)
        run = self.make_run(prior, Trip.TRIP_TYPE_DROP, self.local_dt(day, 1))
        with patch("trips.views.route_run_views.timezone.now", return_value=self.local_dt(day, 1, 30)):
            response = self.call("get", "today_active", self.driver)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["drop"]["id"], run.pk)
        self.assertEqual(response.data["operational_date"], prior.isoformat())

    def test_started_overnight_drop_remains_visible_next_day(self):
        day = timezone.localdate()
        prior = day - timedelta(days=2)
        run = self.make_run(prior, Trip.TRIP_TYPE_DROP, self.local_dt(prior + timedelta(days=1), 1),
                            started=self.local_dt(prior + timedelta(days=1), 1))
        with patch("trips.views.route_run_views.timezone.now", return_value=self.local_dt(day, 14)):
            response = self.call("get", "today_active", self.driver)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["drop"]["id"], run.pk)

    def test_completed_route_not_active(self):
        day = timezone.localdate()
        run = self.make_run(day, Trip.TRIP_TYPE_PICKUP, self.local_dt(day, 9),
                            started=self.local_dt(day, 9), completed=self.local_dt(day, 10))
        with patch("trips.views.route_run_views.timezone.now", return_value=self.local_dt(day, 11)):
            response = self.call("get", "today_active", self.driver)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIsNone(response.data["pickup"])

    def test_cancelled_route_cannot_start(self):
        day = timezone.localdate()
        run = self.make_run(day, Trip.TRIP_TYPE_PICKUP, self.local_dt(day, 9), trip_status=Trip.STATUS_CANCELLED)
        with patch("trips.views.route_run_views.timezone.now", return_value=self.local_dt(day, 9)):
            response = self.call("post", "start_run", self.driver, run=run)
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIsNone(RouteRun.objects.get(pk=run.pk).started_at)

    def test_other_driver_cannot_start_route(self):
        day = timezone.localdate()
        run = self.make_run(day, Trip.TRIP_TYPE_PICKUP, self.local_dt(day, 9))
        with patch("trips.views.route_run_views.timezone.now", return_value=self.local_dt(day, 9)):
            response = self.call("post", "start_run", self.other_driver, run=run)
        self.assertIn(response.status_code, (403, 404))
        self.assertIsNone(RouteRun.objects.get(pk=run.pk).started_at)
