from django.contrib.auth import get_user_model
from rest_framework import serializers
from trips.models import Vehicle

User = get_user_model()


class SignupSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=6)
    address = serializers.CharField(required=False, allow_blank=True)

    vehicle_number = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    vehicle_model = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    seat_count = serializers.IntegerField(required=False, allow_null=True)

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "password",
            "role",
            "phone_number",
            "address",
            "vehicle_number",
            "vehicle_model",
            "seat_count",
        ]

    def validate_role(self, value):
        allowed = {"EMPLOYEE", "DRIVER"}
        if value not in allowed:
            raise serializers.ValidationError("Role must be EMPLOYEE or DRIVER.")
        return value

    def validate(self, attrs):
        role = attrs.get("role")

        if role == "DRIVER":
            if not attrs.get("vehicle_number"):
                raise serializers.ValidationError(
                    {"vehicle_number": "Vehicle number is required for driver."}
                )
            if not attrs.get("vehicle_model"):
                raise serializers.ValidationError(
                    {"vehicle_model": "Vehicle model is required for driver."}
                )
            if not attrs.get("seat_count"):
                raise serializers.ValidationError(
                    {"seat_count": "Seat count is required for driver."}
                )

        return attrs

    def create(self, validated_data):
        password = validated_data.pop("password")

        vehicle_number = validated_data.pop("vehicle_number", None)
        vehicle_model = validated_data.pop("vehicle_model", None)
        seat_count = validated_data.pop("seat_count", None)

        user = User(**validated_data)
        user.set_password(password)
        user.save()

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