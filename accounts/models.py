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

    def _str_(self):
        return self.username