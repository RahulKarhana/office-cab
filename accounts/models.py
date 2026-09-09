from django.contrib.auth.models import AbstractUser
from django.db import models
from django.core.validators import RegexValidator


class User(AbstractUser):

    # ============================================================
    # ROLE
    # ============================================================

    class Role(models.TextChoices):
        ADMIN = "ADMIN", "Admin"
        EMPLOYEE = "EMPLOYEE", "Employee"
        DRIVER = "DRIVER", "Driver"

    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.EMPLOYEE,
    )

    # ============================================================
    # BASIC DETAILS
    # ============================================================

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

    # ============================================================
    # EMPLOYEE PICKUP LOCATION
    # ============================================================

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

    # ============================================================
    # EMPLOYEE ID
    #
    # Format:
    # E0001
    # E0282
    # E0999
    # ============================================================

    employee_id_validator = RegexValidator(
        regex=r"^E\d{4}$",
        message=(
            "Employee ID must start with capital E "
            "followed by exactly 4 digits. Example: E0282."
        ),
    )

    employee_id = models.CharField(
        max_length=5,
        unique=True,
        null=True,
        blank=True,
        validators=[employee_id_validator],
    )

    # ============================================================
    # GENDER
    # ============================================================

    GENDER_CHOICES = [
        ("MALE", "Male"),
        ("FEMALE", "Female"),
        ("OTHER", "Other"),
        (
            "PREFER_NOT_TO_SAY",
            "Prefer not to say",
        ),
    ]

    gender = models.CharField(
        max_length=20,
        choices=GENDER_CHOICES,
        blank=True,
        default="",
    )

    # Existing field - keep for compatibility for now.
    is_female = models.BooleanField(
        default=False,
    )

    # ============================================================
    # ACCOUNT APPROVAL
    #
    # IMPORTANT:
    # Default is APPROVED so existing users stay working
    # after migration.
    #
    # SignupSerializer will explicitly create NEW users
    # as PENDING.
    # ============================================================

    ACCOUNT_STATUS_PENDING = "PENDING"
    ACCOUNT_STATUS_APPROVED = "APPROVED"
    ACCOUNT_STATUS_REJECTED = "REJECTED"

    ACCOUNT_STATUS_CHOICES = [
        (
            ACCOUNT_STATUS_PENDING,
            "Pending",
        ),
        (
            ACCOUNT_STATUS_APPROVED,
            "Approved",
        ),
        (
            ACCOUNT_STATUS_REJECTED,
            "Rejected",
        ),
    ]

    account_status = models.CharField(
        max_length=20,
        choices=ACCOUNT_STATUS_CHOICES,

        # IMPORTANT:
        # Existing database users will become APPROVED.
        default=ACCOUNT_STATUS_APPROVED,
    )

    account_reviewed_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    account_reviewed_by = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_accounts",
    )

    account_rejection_reason = models.TextField(
        blank=True,
        default="",
    )

    # ============================================================
    # DISPLAY
    # ============================================================

    def __str__(self):
        return self.username


# ================================================================
# PICKUP LOCATION CHANGE REQUEST
# ================================================================

class PickupLocationChangeRequest(models.Model):

    STATUS_PENDING = "PENDING"
    STATUS_APPROVED = "APPROVED"
    STATUS_REJECTED = "REJECTED"

    STATUS_CHOICES = [
        (
            STATUS_PENDING,
            "Pending",
        ),
        (
            STATUS_APPROVED,
            "Approved",
        ),
        (
            STATUS_REJECTED,
            "Rejected",
        ),
    ]

    employee = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name=(
            "pickup_location_change_requests"
        ),
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
        related_name=(
            "reviewed_pickup_location_requests"
        ),
    )

    admin_note = models.TextField(
        blank=True,
    )

    class Meta:
        ordering = [
            "-requested_at"
        ]

        constraints = [
            models.UniqueConstraint(
                fields=[
                    "employee"
                ],
                condition=models.Q(
                    status="PENDING"
                ),
                name=(
                    "one_pending_pickup_location_"
                    "request_per_employee"
                ),
            ),
        ]

    def __str__(self):
        return (
            f"{self.employee.username} - "
            f"{self.status}"
        )