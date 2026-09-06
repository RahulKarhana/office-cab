from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = "ADMIN", "Admin"
        EMPLOYEE = "EMPLOYEE", "Employee"
        DRIVER = "DRIVER", "Driver"

    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.EMPLOYEE,
    )

    phone_number = models.CharField(
        max_length=15,
        blank=True,
        null=True,
        unique=True,
    )

    address = models.TextField(
        blank=True,
        null=True,
    )

    pickup_location = models.CharField(
        max_length=255,
        blank=True,
        null=True,
    )
    
    pickup_latitude = models.FloatField(
        blank=True,
        null=True,
    )

    pickup_longitude = models.FloatField(
        blank=True,
        null=True,
    )
    is_female = models.BooleanField(default=False)
    
    def __str__(self):
        return self.username

class PickupLocationChangeRequest(models.Model):
    STATUS_PENDING = "PENDING"
    STATUS_APPROVED = "APPROVED"
    STATUS_REJECTED = "REJECTED"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_REJECTED, "Rejected"),
    ]

    employee = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="pickup_location_change_requests",
    )

    old_pickup_location = models.CharField(
        max_length=255,
        blank=True,
        null=True,
    )

    old_pickup_latitude = models.FloatField(
        blank=True,
        null=True,
    )

    old_pickup_longitude = models.FloatField(
        blank=True,
        null=True,
    )

    requested_pickup_location = models.CharField(
        max_length=255,
    )

    requested_pickup_latitude = models.FloatField()

    requested_pickup_longitude = models.FloatField()

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
    )

    requested_at = models.DateTimeField(
        auto_now_add=True,
    )

    reviewed_at = models.DateTimeField(
        blank=True,
        null=True,
    )

    reviewed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="reviewed_pickup_location_requests",
    )

    admin_note = models.TextField(
        blank=True,
    )

    class Meta:
        ordering = ["-requested_at"]

        constraints = [
            models.UniqueConstraint(
                fields=["employee"],
                condition=models.Q(status="PENDING"),
                name="one_pending_pickup_location_request_per_employee",
            ),
        ]

    def __str__(self):
        return (
            f"{self.employee.username} - "
            f"{self.status}"
        )