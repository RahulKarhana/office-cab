from rest_framework.routers import DefaultRouter
from django.urls import path
from trips.views.route_run_views import RouteRunViewSet
from trips.views.cancellation_views import TripCancellationViewSet
from trips.views.trip_views import TripViewSet
from trips.views.review_views import ReviewViewSet
from trips.views.notification_views import NotificationViewSet
from trips.views.location_views import DriverLocationViewSet
from trips.views.dashboard_views import DashboardAPIView
from trips.views.route_views import RouteAPIView
from trips.views.route_template_views import RouteTemplateViewSet, RouteStopViewSet
from trips.emergency_views import EmergencyAlertViewSet

router = DefaultRouter()
router.register(r"trips", TripViewSet, basename="trip")
router.register(r"reviews", ReviewViewSet, basename="review")
router.register(r"notifications", NotificationViewSet, basename="notification")
router.register(r"emergency-alerts", EmergencyAlertViewSet, basename="emergency-alerts")
router.register(r"routes", RouteTemplateViewSet, basename="routes")
router.register(r"route-stops", RouteStopViewSet, basename="route-stops")
router.register(r"locations", DriverLocationViewSet, basename="location")
router.register(r"cancellations", TripCancellationViewSet, basename="cancellations")
router.register(r"route-runs", RouteRunViewSet, basename="route-runs")

urlpatterns = router.urls + [
    path("dashboard/", DashboardAPIView.as_view(), name="dashboard"),
    path("route/", RouteAPIView.as_view(), name="route"),
]