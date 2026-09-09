from django.contrib.auth import get_user_model
from rest_framework import serializers
from trips.models import Vehicle
from .models import PickupLocationChangeRequest
from rest_framework_simplejwt.serializers import (
    TokenObtainPairSerializer,
)

User = get_user_model()


class SignupSerializer(serializers.ModelSerializer):
    password = serializers.CharField(
        write_only=True,
        min_length=6,
    )

    address = serializers.CharField(
        required=False,
        allow_blank=True,
    )

    # ============================================================
    # EMPLOYEE FIELDS
    # ============================================================

    employee_id = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=5,
    )

    gender = serializers.ChoiceField(
        choices=User.GENDER_CHOICES,
        required=False,
        allow_blank=True,
    )

    # ============================================================
    # DRIVER VEHICLE FIELDS
    # ============================================================

    vehicle_number = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )

    vehicle_model = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
    )

    seat_count = serializers.IntegerField(
        required=False,
        allow_null=True,
    )

    class Meta:
        model = User

        fields = [
            "id",
            "username",
            "password",
            "role",
            "phone_number",
            "address",

            "employee_id",
            "gender",
            "account_status",

            "vehicle_number",
            "vehicle_model",
            "seat_count",
        ]

        read_only_fields = [
            "account_status",
        ]

    # ============================================================
    # ROLE VALIDATION
    # ============================================================

    def validate_role(self, value):
        allowed = {
            "EMPLOYEE",
            "DRIVER",
        }

        if value not in allowed:
            raise serializers.ValidationError(
                "Role must be EMPLOYEE or DRIVER."
            )

        return value

    # ============================================================
    # EMPLOYEE ID VALIDATION
    # Format must be:
    #
    # E0001
    # E0282
    # E0999
    #
    # E + exactly 4 numbers
    # ============================================================

    def validate_employee_id(self, value):

        if not value:
            return value

        value = value.strip().upper()

        if len(value) != 5:
            raise serializers.ValidationError(
                "Employee ID must be exactly 5 characters. "
                "Example: E0282."
            )

        if value[0] != "E":
            raise serializers.ValidationError(
                "Employee ID must start with capital E. "
                "Example: E0282."
            )

        if not value[1:].isdigit():
            raise serializers.ValidationError(
                "Employee ID must contain E followed by "
                "exactly 4 numbers."
            )

        if User.objects.filter(
            employee_id=value
        ).exists():
            raise serializers.ValidationError(
                "This Employee ID is already registered."
            )

        return value

    # ============================================================
    # COMPLETE SIGNUP VALIDATION
    # ============================================================

    def validate(self, attrs):

        role = attrs.get("role")

        # --------------------------------------------------------
        # EMPLOYEE
        # --------------------------------------------------------

        if role == "EMPLOYEE":

            employee_id = attrs.get(
                "employee_id",
                "",
            )

            gender = attrs.get(
                "gender",
                "",
            )

            if not employee_id:
                raise serializers.ValidationError({
                    "employee_id":
                        "Employee ID is required."
                })

            if not gender:
                raise serializers.ValidationError({
                    "gender":
                        "Gender is required."
                })

        # --------------------------------------------------------
        # DRIVER
        # --------------------------------------------------------

        elif role == "DRIVER":

            if not attrs.get("vehicle_number"):
                raise serializers.ValidationError({
                    "vehicle_number":
                        "Vehicle number is required for driver."
                })

            if not attrs.get("vehicle_model"):
                raise serializers.ValidationError({
                    "vehicle_model":
                        "Vehicle model is required for driver."
                })

            if not attrs.get("seat_count"):
                raise serializers.ValidationError({
                    "seat_count":
                        "Seat count is required for driver."
                })

            # Driver does not use employee-only fields.
            attrs["employee_id"] = None
            attrs["gender"] = ""

        return attrs

    # ============================================================
    # CREATE USER
    # New signup = PENDING until Admin approval
    # ============================================================

    def create(self, validated_data):

        password = validated_data.pop(
            "password"
        )

        vehicle_number = validated_data.pop(
            "vehicle_number",
            None,
        )

        vehicle_model = validated_data.pop(
            "vehicle_model",
            None,
        )

        seat_count = validated_data.pop(
            "seat_count",
            None,
        )

        # ========================================================
        # NEW REGISTRATION MUST WAIT FOR ADMIN APPROVAL
        # ========================================================

        validated_data["account_status"] = (
            User.ACCOUNT_STATUS_PENDING
        )

        validated_data["is_active"] = False

        # No review information yet.
        validated_data["account_reviewed_at"] = None
        validated_data["account_reviewed_by"] = None
        validated_data["account_rejection_reason"] = ""

        user = User(
            **validated_data
        )

        user.set_password(
            password
        )

        user.save()

        # ========================================================
        # CREATE VEHICLE FOR DRIVER
        # ========================================================

        if user.role == "DRIVER":

            Vehicle.objects.create(
                driver=user,
                vehicle_number=vehicle_number,
                vehicle_model=vehicle_model,
                seat_count=seat_count,
            )

        return user

    
class MeSerializer(serializers.ModelSerializer):
    vehicle_number = serializers.SerializerMethodField()
    vehicle_model = serializers.SerializerMethodField()
    seat_count = serializers.SerializerMethodField()

    class Meta:
        model = User

        fields = [
            "id",
            "username",
            "role",

            "employee_id",
            "gender",
            "account_status",

            "phone_number",
            "address",
            "pickup_location",
            "pickup_latitude",
            "pickup_longitude",

            "vehicle_number",
            "vehicle_model",
            "seat_count",
        ]

    def get_vehicle_number(self, obj):
        if hasattr(obj, "vehicle"):
            return obj.vehicle.vehicle_number
        return None

    def get_vehicle_model(self, obj):
        if hasattr(obj, "vehicle"):
            return obj.vehicle.vehicle_model
        return None

    def get_seat_count(self, obj):
        if hasattr(obj, "vehicle"):
            return obj.vehicle.seat_count
        return None

class UpdatePickupLocationSerializer(serializers.Serializer):
    pickup_location = serializers.CharField(required=False, allow_blank=True)
    latitude = serializers.FloatField()
    longitude = serializers.FloatField()

    def validate(self, attrs):
        lat = attrs["latitude"]
        lng = attrs["longitude"]

        if lat < -90 or lat > 90:
            raise serializers.ValidationError(
                {"latitude": "Latitude must be between -90 and 90."}
            )

        if lng < -180 or lng > 180:
            raise serializers.ValidationError(
                {"longitude": "Longitude must be between -180 and 180."}
            )

        return attrs

class CustomTokenObtainPairSerializer(
    TokenObtainPairSerializer
):
    def validate(self, attrs):
        username = attrs.get("username", "").strip()
        password = attrs.get("password", "")

        # ========================================================
        # FIND USER EVEN WHEN is_active=False
        # ========================================================

        try:
            user = User.objects.get(
                username=username
            )
        except User.DoesNotExist:
            raise serializers.ValidationError({
                "detail":
                    "Invalid username or password."
            })

        # ========================================================
        # PASSWORD CHECK
        # ========================================================

        if not user.check_password(password):
            raise serializers.ValidationError({
                "detail":
                    "Invalid username or password."
            })

        # ========================================================
        # ADMIN APPROVAL STATUS
        # ========================================================

        if (
            user.account_status
            == User.ACCOUNT_STATUS_PENDING
        ):
            raise serializers.ValidationError({
                "account_status": "PENDING",
                "detail": (
                    "Your account is waiting for "
                    "Admin approval."
                ),
            })

        if (
            user.account_status
            == User.ACCOUNT_STATUS_REJECTED
        ):
            reason = (
                user.account_rejection_reason
                or "No reason was provided."
            )

            raise serializers.ValidationError({
                "account_status": "REJECTED",
                "detail": (
                    "Your registration was rejected."
                ),
                "reason": reason,
            })

        # ========================================================
        # APPROVED BUT DISABLED
        # ========================================================

        if not user.is_active:
            raise serializers.ValidationError({
                "detail":
                    "Your account is currently disabled."
            })

        # ========================================================
        # NORMAL JWT LOGIN
        # ========================================================

        data = super().validate(attrs)

        data["user_id"] = user.id
        data["username"] = user.username
        data["role"] = user.role
        data["account_status"] = (
            user.account_status
        )

        if user.role == "EMPLOYEE":
            data["employee_id"] = (
                user.employee_id
            )

            data["gender"] = (
                user.gender
            )

        return data
    
class PickupLocationChangeRequestSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(
        source="employee.username",
        read_only=True,
    )

    class Meta:
        model = PickupLocationChangeRequest
        fields = [
            "id",
            "employee",
            "employee_name",
            "old_pickup_location",
            "old_pickup_latitude",
            "old_pickup_longitude",
            "requested_pickup_location",
            "requested_pickup_latitude",
            "requested_pickup_longitude",
            "status",
            "requested_at",
            "reviewed_at",
            "reviewed_by",
            "admin_note",
        ]

        read_only_fields = [
            "employee",
            "old_pickup_location",
            "old_pickup_latitude",
            "old_pickup_longitude",
            "status",
            "requested_at",
            "reviewed_at",
            "reviewed_by",
            "admin_note",
        ]