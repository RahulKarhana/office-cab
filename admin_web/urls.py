from django.urls import path
from . import views

app_name = "admin_web"

urlpatterns = [
    path("dashboard/", views.dashboard, name="dashboard"),
    path("employees/", views.employees_page, name="employees"),
    path("drivers/", views.drivers_page, name="drivers"),

    path("routes/", views.routes_page, name="routes"),
    path("routes/create/", views.create_route, name="create_route"),
    path("routes/<int:route_id>/edit/", views.edit_route, name="edit_route"),
    path("routes/<int:route_id>/delete/", views.delete_route, name="delete_route"),
    path("routes/<int:route_id>/assign/<str:trip_type>/", views.assign_route_trip, name="assign_route_trip"),
    path("routes/<int:route_id>/repeat/", views.repeat_route_action, name="repeat_route_action"),
    path("routes/repeat-all/", views.repeat_all_routes_action, name="repeat_all_routes_action"),

    path("assigned-trips/", views.assigned_trips_page, name="assigned_trips"),
    path("trip-history/", views.trip_history_page, name="trip_history"),
    path("notifications/", views.notifications_page, name="notifications"),
    path("trips/", views.trips_page, name="trips"),
    path("trips/<int:trip_id>/cancel/", views.cancel_trip, name="cancel_trip"),
    path("trips/<int:trip_id>/restore/", views.restore_trip, name="restore_trip"),
    path("alerts/", views.alerts_page, name="alerts"),
    path("alerts/data/", views.alerts_data_api, name="alerts_data_api"),
    path("alerts/<int:alert_id>/resolve/", views.resolve_alert, name="resolve_alert"),
    path("trips/<int:trip_id>/start/", views.start_trip, name="start_trip"),
    path("trips/<int:trip_id>/complete/", views.complete_trip, name="complete_trip"),

    path("reports/", views.reports_page, name="reports"),
    path("reports/export/", views.export_reports_excel, name="export_reports_excel"),
    path("reports/drivers/", views.driver_performance_page, name="driver_performance"),
    path("assigned-trips/<int:route_run_id>/cancel-all/", views.cancel_route_run_trips, name="cancel_route_run_trips"),
    path("route-run/<int:route_run_id>/cancel/", views.cancel_route_run_trips, name="cancel_route_run_trips"),
    path(
        "employees/<int:employee_id>/route-search/",
        views.employee_route_search,
        name="employee_route_search",
    ),
    path(
        "reports/route-analytics/",
        views.route_analytics_page,
        name="route_analytics",
    ),
    path(
        "employees/<int:employee_id>/assign-route/<int:route_id>/",
        views.assign_employee_to_route,
        name="assign_employee_to_route",
    ),
    path("tracking/", views.live_tracking, name="tracking"),
    path(
        "live-cab-cards/",
        views.live_cab_cards_api,
        name="live_cab_cards_api"
    ),
    path("assigned-trips/cancel-date/<str:date>/", views.cancel_date_trips, name="cancel_date_trips"),
    path("assigned-trips/", views.assigned_trips_page, name="assigned_trips"),
    path("assigned-trips/cancel-date/<str:date_value>/", views.cancel_date_trips, name="cancel_date_trips"),
    path("assigned-trips/cancel-route-run/<int:route_run_id>/", views.cancel_route_run_trips, name="    cancel_route_run_trips      "),
]   