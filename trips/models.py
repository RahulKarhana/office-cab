from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone


class Vehicle(models.Model):
    driver = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        limit_choices_to={"role": "DRIVER"},
        related_name="vehicle",
    )
    vehicle_number = models.CharField(max_length=20, unique=True)
    vehicle_model = models.CharField(max_length=100)
    seat_count = models.PositiveIntegerField(default=4)

    def __str__(self):
        return f"{self.vehicle_number} - {self.driver.username}"


class Trip(models.Model):
    STATUS_ASSIGNED = "ASSIGNED"
    STATUS_STARTED = "STARTED"
    STATUS_COMPLETED = "COMPLETED"
    STATUS_CANCELLED = "CANCELLED"

    STATUS_CHOICES = [
        (STATUS_ASSIGNED, "Assigned"),
        (STATUS_STARTED, "Started"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    TRIP_TYPE_PICKUP = "PICKUP"
    TRIP_TYPE_DROP = "DROP"

    TRIP_TYPE_CHOICES = [
        (TRIP_TYPE_PICKUP, "Pickup"),
        (TRIP_TYPE_DROP, "Drop"),
    ]

    employee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="employee_trips",
        limit_choices_to={"role": "EMPLOYEE"},
    )

    driver = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="driver_trips",
        limit_choices_to={"role": "DRIVER"},
    )

    vehicle = models.ForeignKey(
        "Vehicle",
        on_delete=models.CASCADE,
        related_name="trips",
    )

    route_run = models.ForeignKey(
        "RouteRun",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="trips",
    )

    pickup_location = models.CharField(max_length=255)
    drop_location = models.CharField(max_length=255)

    trip_type = models.CharField(
        max_length=20,
        choices=TRIP_TYPE_CHOICES,
        default=TRIP_TYPE_PICKUP,
    )

    pickup_latitude = models.FloatField(null=True, blank=True)
    pickup_longitude = models.FloatField(null=True, blank=True)

    drop_latitude = models.FloatField(null=True, blank=True)
    drop_longitude = models.FloatField(null=True, blank=True)

    pickup_time = models.DateTimeField()
    trip_date = models.DateField(null=True, blank=True, db_index=True)

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_ASSIGNED,
        db_index=True,
    )

    notification_sent = models.BooleanField(default=False)
    start_time = models.DateTimeField(null=True, blank=True)
    end_time = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if self.pickup_time:
            self.trip_date = self.pickup_time.date()
        super().save(*args, **kwargs)

    def start(self):
        if self.status != self.STATUS_ASSIGNED:
            raise ValueError("Trip must be ASSIGNED before starting.")
        self.status = self.STATUS_STARTED
        self.start_time = timezone.now()
        self.save(update_fields=["status", "start_time"])

    def complete(self):
        if self.status != self.STATUS_STARTED:
            raise ValueError("Trip must be STARTED before completing.")
        self.status = self.STATUS_COMPLETED
        self.end_time = timezone.now()
        self.save(update_fields=["status", "end_time"])

    def cancel(self):
        if self.status == self.STATUS_COMPLETED:
            raise ValueError("Completed trip cannot be cancelled.")
        if self.status == self.STATUS_CANCELLED:
            raise ValueError("Trip is already cancelled.")
        self.status = self.STATUS_CANCELLED
        self.save(update_fields=["status"])

    def __str__(self):
        return f"Trip {self.id} - {self.trip_type} - {self.status}"


class Review(models.Model):
    trip = models.OneToOneField(
        Trip,
        on_delete=models.CASCADE,
        related_name="review",
    )
    employee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
    )
    rating = models.IntegerField()
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Review for Trip {self.trip_id} by {self.employee.username}"


class Notification(models.Model):
    PRIORITY_LOW = "LOW"
    PRIORITY_MEDIUM = "MEDIUM"
    PRIORITY_HIGH = "HIGH"
    PRIORITY_CRITICAL = "CRITICAL"

    PRIORITY_CHOICES = [
        (PRIORITY_LOW, "Low"),
        (PRIORITY_MEDIUM, "Medium"),
        (PRIORITY_HIGH, "High"),
        (PRIORITY_CRITICAL, "Critical"),
    ]

    TYPE_INFO = "INFO"
    TYPE_ROUTE_DELAY = "ROUTE_DELAY"
    TYPE_OVERSPEED = "OVERSPEED"
    TYPE_NO_SHOW = "NO_SHOW"
    TYPE_ROUTE_COMPLETED = "ROUTE_COMPLETED"
    TYPE_SOS = "SOS"

    TYPE_CHOICES = [
        (TYPE_INFO, "Info"),
        (TYPE_ROUTE_DELAY, "Route Delay"),
        (TYPE_OVERSPEED, "Overspeed"),
        (TYPE_NO_SHOW, "No-show"),
        (TYPE_ROUTE_COMPLETED, "Route Completed"),
        (TYPE_SOS, "SOS"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )

    title = models.CharField(max_length=255)
    message = models.TextField()

    notification_type = models.CharField(
        max_length=50,
        choices=TYPE_CHOICES,
        default=TYPE_INFO,
    )

    priority = models.CharField(
        max_length=20,
        choices=PRIORITY_CHOICES,
        default=PRIORITY_LOW,
    )

    is_read = models.BooleanField(default=False)

    trip = models.ForeignKey(
        "Trip",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    route_run = models.ForeignKey(
        "RouteRun",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    driver = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="driver_notifications",
    )

    employee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="employee_notifications",
    )

    nearby_alert_sent = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.title} - {self.user.username}"

class DriverLocation(models.Model):
    driver = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="location",
        limit_choices_to={"role": "DRIVER"},
    )
    latitude = models.FloatField()
    longitude = models.FloatField()
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.driver.username} @ {self.latitude}, {self.longitude}"


class TripCancellation(models.Model):
    trip = models.ForeignKey(
        Trip,
        on_delete=models.CASCADE,
        related_name="cancellations",
    )
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
    )
    reason = models.TextField(blank=True)

    # ✅ STEP 8: Employee declaration form fields
    declaration_accepted = models.BooleanField(default=False)

    declaration_text = models.TextField(
        blank=True,
        default="",
    )

    cancelled_by_role = models.CharField(
        max_length=20,
        blank=True,
        default="",
    )

    cancelled_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Trip {self.trip_id} cancelled by {self.cancelled_by.username}"
    
class EmployeeLeave(models.Model):
    employee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="cab_leaves",
        limit_choices_to={"role": "EMPLOYEE"},
    )

    leave_date = models.DateField()
    reason = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("employee", "leave_date")
        ordering = ["-leave_date"]

    def __str__(self):
        return f"{self.employee.username} leave on {self.leave_date}"
    
class RouteTemplate(models.Model):
    name = models.CharField(max_length=200)

    driver = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        limit_choices_to={"role": "DRIVER"},
    )

    vehicle = models.ForeignKey(
        Vehicle,
        on_delete=models.CASCADE,
        related_name="routes",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def clean(self):
        if self.vehicle and self.driver:
            if self.vehicle.driver != self.driver:
                raise ValidationError("Vehicle must belong to the selected driver.")

    def __str__(self):
        return self.name


class RouteStop(models.Model):
    route = models.ForeignKey(
        RouteTemplate,
        related_name="stops",
        on_delete=models.CASCADE,
    )

    employee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        limit_choices_to={"role": "EMPLOYEE"},
    )

    pickup_location = models.CharField(max_length=255)
    pickup_latitude = models.FloatField(null=True, blank=True)
    pickup_longitude = models.FloatField(null=True, blank=True)

    stop_order = models.PositiveIntegerField()

    class Meta:
        ordering = ["stop_order"]

    def __str__(self):
        return f"{self.route.name} - Stop {self.stop_order} ({self.employee.username})"


class RouteRun(models.Model):
    route_template = models.ForeignKey(
        RouteTemplate,
        on_delete=models.CASCADE,
        related_name="runs",
    )

    driver = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="route_runs",
    )

    vehicle = models.ForeignKey(
        Vehicle,
        on_delete=models.CASCADE,
        related_name="route_runs",
    )

    trip_type = models.CharField(
        max_length=10,
        choices=Trip.TRIP_TYPE_CHOICES,
    )
    run_date = models.DateField()

    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    current_stop_order = models.IntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.route_template.name} - {self.trip_type} - {self.run_date}"


class RouteRunStop(models.Model):
    route_run = models.ForeignKey(
        RouteRun,
        on_delete=models.CASCADE,
        related_name="stops",
    )

    employee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
    )

    pickup_location = models.CharField(max_length=255)
    pickup_latitude = models.FloatField(null=True, blank=True)
    pickup_longitude = models.FloatField(null=True, blank=True)

    stop_order = models.IntegerField()
    is_picked = models.BooleanField(default=False)
    picked_at = models.DateTimeField(null=True, blank=True)
    delay_warning_sent = models.BooleanField(default=False)
    arrival_time = models.DateTimeField(null=True, blank=True)
    waiting_started_at = models.DateTimeField(null=True, blank=True)
    is_no_show = models.BooleanField(default=False)
    no_show_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["stop_order"]

    def __str__(self):
        return f"{self.employee.username} - Stop {self.stop_order}"

class DeviceToken(models.Model):
    DEVICE_TYPE_CHOICES = (
        ("ANDROID", "Android"),
        ("IOS", "iOS"),
        ("WEB", "Web"),
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="device_tokens"
    )
    token = models.TextField(unique=True)

    device_type = models.CharField(
        max_length=20,
        choices=DEVICE_TYPE_CHOICES,
        default="ANDROID"
    )

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username} - {self.device_type}"
    
class EmergencyAlert(models.Model):
    STATUS_PENDING = "PENDING"
    STATUS_READ = "READ"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_READ, "Read"),
    ]

    employee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="emergency_alerts",
        limit_choices_to={"role": "EMPLOYEE"},
    )
    trip = models.ForeignKey(
        Trip,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="emergency_alerts",
    )
    route_run = models.ForeignKey(
        "RouteRun",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="emergency_alerts",
    )

    title = models.CharField(max_length=255, default="Emergency Alert")
    message = models.TextField()

    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    pickup_location = models.CharField(max_length=255, blank=True, default="")
    drop_location = models.CharField(max_length=255, blank=True, default="")

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    def mark_as_read(self):
        self.status = self.STATUS_READ
        self.read_at = timezone.now()
        self.save(update_fields=["status", "read_at"])

    def __str__(self):
        return f"EmergencyAlert #{self.id} - {self.employee.username}"


class DriverLocationHistory(models.Model):
    driver = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="location_history",
        limit_choices_to={"role": "DRIVER"},
    )

    route_run = models.ForeignKey(
        "RouteRun",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="driver_speed_history",
    )

    trip_type = models.CharField(max_length=20, blank=True, null=True)

    latitude = models.FloatField()
    longitude = models.FloatField()

    speed_kmph = models.FloatField(default=0)
    is_overspeed = models.BooleanField(default=False)

    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-recorded_at"]

    def __str__(self):
        return f"{self.driver} - {self.speed_kmph} km/h"