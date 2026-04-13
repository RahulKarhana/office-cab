from math import radians, cos, sin, asin, sqrt
from django.utils import timezone
from rest_framework import serializers
from .models import EmergencyAlert
from .models import (
    Trip,
    Review,
    Notification,
    DriverLocation,
    TripCancellation,
    Vehicle,
    RouteTemplate,
    RouteStop,
    RouteRun,
    RouteRunStop,
)


def calculate_distance_km(lat1, lon1, lat2, lon2):
    lon1, lat1, lon2, lat2 = map(float, [lon1, lat1, lon2, lat2])
    lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])

    dlon = lon2 - lon1
    dlat = lat2 - lat1

    a = sin(dlat / 2) * 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) * 2
    c = 2 * asin(sqrt(a))
    r = 6371

    return c * r


def estimate_eta_minutes(distance_km, avg_speed_kmph=25):
    if distance_km is None:
        return None
    eta = int((distance_km / avg_speed_kmph) * 60)
    return max(1, eta)


def format_eta_text(minutes):
    if minutes is None:
        return None

    if minutes < 60:
        return f"{minutes} min"

    hours = minutes // 60
    mins = minutes % 60

    if mins == 0:
        return f"{hours} hr"

    return f"{hours} hr {mins} min"


def format_distance_text(distance_km):
    if distance_km is None:
        return None

    if distance_km < 1:
        return f"{int(distance_km * 1000)} m"

    return f"{round(distance_km, 1)} km"


class RouteRunStopSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source="employee.username", read_only=True)

    class Meta:
        model = RouteRunStop
        fields = [
            "id",
            "employee",
            "employee_name",
            "pickup_location",
            "pickup_latitude",
            "pickup_longitude",
            "stop_order",
            "is_picked",
            "picked_at",
        ]


class RouteRunSerializer(serializers.ModelSerializer):
    route_name = serializers.CharField(source="route_template.name", read_only=True)
    driver_name = serializers.CharField(source="driver.username", read_only=True)
    vehicle_number = serializers.CharField(source="vehicle.vehicle_number", read_only=True)
    stops = RouteRunStopSerializer(many=True, read_only=True)

    class Meta:
        model = RouteRun
        fields = [
            "id",
            "route_template",
            "route_name",
            "driver",
            "driver_name",
            "vehicle",
            "vehicle_number",
            "trip_type",
            "run_date",
            "started_at",
            "completed_at",
            "current_stop_order",
            "created_at",
            "stops",
        ]


class TripSerializer(serializers.ModelSerializer):
    driver_name = serializers.CharField(source="driver.username", read_only=True)
    employee_name = serializers.CharField(source="employee.username", read_only=True)
    vehicle_number = serializers.CharField(source="vehicle.vehicle_number", read_only=True)
    vehicle_model = serializers.CharField(source="vehicle.vehicle_model", read_only=True)

    route_run = RouteRunSerializer(read_only=True)
    driver_latitude = serializers.SerializerMethodField()
    driver_longitude = serializers.SerializerMethodField()

    class Meta:
        model = Trip
        fields = [
            "id",
            "employee",
            "employee_name",
            "driver",
            "driver_name",
            "vehicle",
            "vehicle_number",
            "vehicle_model",
            "trip_type",
            "pickup_location",
            "drop_location",
            "pickup_latitude",
            "pickup_longitude",
            "drop_latitude",
            "drop_longitude",
            "pickup_time",
            "trip_date",
            "status",
            "notification_sent",
            "start_time",
            "end_time",
            "created_at",
            "route_run",
            "driver_latitude",
            "driver_longitude",
        ]
        read_only_fields = [
            "trip_date",
            "status",
            "notification_sent",
            "start_time",
            "end_time",
            "created_at",
            "route_run",
            "driver_latitude",
            "driver_longitude",
        ]

    def validate(self, attrs):
        employee = attrs.get("employee", getattr(self.instance, "employee", None))
        trip_type = attrs.get("trip_type", getattr(self.instance, "trip_type", None))
        pickup_time = attrs.get("pickup_time", getattr(self.instance, "pickup_time", None))

        if employee and trip_type and pickup_time:
            trip_date = pickup_time.date()

            qs = Trip.objects.filter(
                employee=employee,
                trip_type=trip_type,
                trip_date=trip_date,
            )

            if self.instance:
                qs = qs.exclude(id=self.instance.id)

            if qs.exists():
                raise serializers.ValidationError(
                    f"{employee.username} already has a {trip_type.lower()} trip on {trip_date}."
                )

        return attrs

    def get_driver_latitude(self, obj):
        location = getattr(obj.driver, "location", None)
        if location:
            return location.latitude
        return None

    def get_driver_longitude(self, obj):
        location = getattr(obj.driver, "location", None)
        if location:
            return location.longitude
        return None


class ReviewSerializer(serializers.ModelSerializer):
    class Meta:
        model = Review
        fields = "_all_"


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ["id", "title", "message", "is_read", "created_at"]


class AssignedCabSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source="employee.username", read_only=True)
    driver_name = serializers.CharField(source="driver.username", read_only=True)
    vehicle_number = serializers.CharField(source="vehicle.vehicle_number", read_only=True)
    route_name = serializers.CharField(source="route_run.route_template.name", read_only=True)
    route_run_id = serializers.IntegerField(source="route_run.id", read_only=True)

    class Meta:
        model = Trip
        fields = [
            "id",
            "employee",
            "employee_name",
            "driver",
            "driver_name",
            "vehicle",
            "vehicle_number",
            "route_run",
            "route_run_id",
            "route_name",
            "trip_type",
            "pickup_location",
            "drop_location",
            "status",
            "created_at",
            "start_time",
            "end_time",
        ]


class DriverLocationSerializer(serializers.ModelSerializer):
    driver_name = serializers.CharField(source="driver.username", read_only=True)
    active_trip = serializers.SerializerMethodField()
    distance_km = serializers.SerializerMethodField()
    distance_text = serializers.SerializerMethodField()
    eta_minutes = serializers.SerializerMethodField()
    eta_text = serializers.SerializerMethodField()
    current_stop_name = serializers.SerializerMethodField()
    current_stop_latitude = serializers.SerializerMethodField()
    current_stop_longitude = serializers.SerializerMethodField()
    alert_level = serializers.SerializerMethodField()

    class Meta:
        model = DriverLocation
        fields = [
            "id",
            "driver",
            "driver_name",
            "latitude",
            "longitude",
            "updated_at",
            "active_trip",
            "distance_km",
            "distance_text",
            "eta_minutes",
            "eta_text",
            "current_stop_name",
            "current_stop_latitude",
            "current_stop_longitude",
            "alert_level",
        ]

    def _get_employee_started_trip(self, obj):
        request = self.context.get("request")
        if not request or not hasattr(request, "user"):
            return None

        user = request.user
        if getattr(user, "role", None) != "EMPLOYEE":
            return None

        return Trip.objects.select_related(
            "route_run",
            "driver",
            "vehicle",
        ).filter(
            employee=user,
            driver=obj.driver,
            status=Trip.STATUS_STARTED,
        ).order_by("-created_at").first()

    def _get_current_stop(self, obj):
        trip = self._get_employee_started_trip(obj)
        if not trip or not trip.route_run:
            return None

        return trip.route_run.stops.filter(
            is_picked=False
        ).order_by("stop_order").first()

    def _get_distance_km_value(self, obj):
        current_stop = self._get_current_stop(obj)
        if not current_stop:
            return None

        if current_stop.pickup_latitude is None or current_stop.pickup_longitude is None:
            return None

        return calculate_distance_km(
            obj.latitude,
            obj.longitude,
            current_stop.pickup_latitude,
            current_stop.pickup_longitude,
        )

    def get_active_trip(self, obj):
        trip = Trip.objects.filter(
            driver=obj.driver,
            status__in=[Trip.STATUS_ASSIGNED, Trip.STATUS_STARTED],
        ).order_by("-created_at").first()

        if not trip:
            return None

        return {
            "id": trip.id,
            "employee_name": trip.employee.username,
            "pickup_location": trip.pickup_location,
            "drop_location": trip.drop_location,
            "trip_type": trip.trip_type,
            "status": trip.status,
        }

    def get_distance_km(self, obj):
        distance = self._get_distance_km_value(obj)
        if distance is None:
            return None
        return round(distance, 2)

    def get_distance_text(self, obj):
        distance = self._get_distance_km_value(obj)
        if distance is None:
            return None

        if distance < 1:
            meters = int(distance * 1000)
            return f"{meters} m"

        return f"{round(distance, 1)} km"

    def get_eta_minutes(self, obj):
        distance = self._get_distance_km_value(obj)
        if distance is None:
            return None

        eta = int((distance / 25) * 60)
        return max(1, eta)

    def get_eta_text(self, obj):
        eta = self.get_eta_minutes(obj)
        if eta is None:
            return None
        return f"{eta} min"

    def get_current_stop_name(self, obj):
        current_stop = self._get_current_stop(obj)
        if not current_stop:
            return None
        return current_stop.employee.username

    def get_current_stop_latitude(self, obj):
        current_stop = self._get_current_stop(obj)
        if not current_stop:
            return None
        return current_stop.pickup_latitude

    def get_current_stop_longitude(self, obj):
        current_stop = self._get_current_stop(obj)
        if not current_stop:
            return None
        return current_stop.pickup_longitude

    def get_alert_level(self, obj):
        distance = self._get_distance_km_value(obj)
        if distance is None:
            return None

        if distance <= 0.15:
            return "ARRIVED"
        if distance <= 0.5:
            return "NEAR_500M"
        if distance <= 1.0:
            return "NEAR_1KM"
        return None


class TripCancellationSerializer(serializers.ModelSerializer):
    trip_id = serializers.IntegerField(source="trip.id", read_only=True)
    employee_name = serializers.CharField(
        source="cancelled_by.username",
        read_only=True,
    )

    class Meta:
        model = TripCancellation
        fields = [
            "id",
            "trip_id",
            "employee_name",
            "reason",
            "cancelled_at",
        ]


class UserOptionSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    username = serializers.CharField()
    phone_number = serializers.CharField(allow_null=True, required=False)
    pickup_location = serializers.CharField(allow_null=True, required=False)
    pickup_latitude = serializers.FloatField(allow_null=True, required=False)
    pickup_longitude = serializers.FloatField(allow_null=True, required=False)
    has_saved_route = serializers.BooleanField(required=False, default=False)
    is_selectable = serializers.BooleanField(required=False, default=True)


class VehicleOptionSerializer(serializers.ModelSerializer):
    driver_name = serializers.CharField(source="driver.username", read_only=True)

    class Meta:
        model = Vehicle
        fields = [
            "id",
            "vehicle_number",
            "vehicle_model",
            "seat_count",
            "driver",
            "driver_name",
        ]


class RouteStopSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(
        source="employee.username",
        read_only=True,
    )

    class Meta:
        model = RouteStop
        fields = [
            "id",
            "employee",
            "employee_name",
            "pickup_location",
            "pickup_latitude",
            "pickup_longitude",
            "stop_order",
        ]


class AssignedCabEmployeeSerializer(serializers.Serializer):
    trip_id = serializers.IntegerField()
    employee_id = serializers.IntegerField()
    employee_name = serializers.CharField()
    pickup_location = serializers.CharField(allow_null=True, required=False)
    drop_location = serializers.CharField(allow_null=True, required=False)
    status = serializers.CharField()


class AssignedCabGroupSerializer(serializers.Serializer):
    route_run_id = serializers.IntegerField(allow_null=True)
    route_name = serializers.CharField()
    trip_type = serializers.CharField()
    driver_id = serializers.IntegerField(allow_null=True)
    driver_name = serializers.CharField(allow_null=True)
    vehicle_id = serializers.IntegerField(allow_null=True)
    vehicle_number = serializers.CharField(allow_null=True)
    pickup_time = serializers.DateTimeField(allow_null=True)
    status = serializers.CharField()
    total_employees = serializers.IntegerField()
    employees = AssignedCabEmployeeSerializer(many=True)

class EmergencyAlertSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source="employee.username", read_only=True)
    trip_type = serializers.CharField(source="trip.trip_type", read_only=True)
    route_name = serializers.CharField(source="route_run.route_template.name", read_only=True)
    vehicle_number = serializers.CharField(source="trip.vehicle.vehicle_number", read_only=True)

    class Meta:
        model = EmergencyAlert
        fields = [
            "id",
            "employee",
            "employee_name",
            "trip",
            "trip_type",
            "route_run",
            "route_name",
            "vehicle_number",
            "title",
            "message",
            "latitude",
            "longitude",
            "pickup_location",
            "drop_location",
            "status",
            "created_at",
            "read_at",
        ]
class RouteTemplateSerializer(serializers.ModelSerializer):
    driver_name = serializers.CharField(source="driver.username", read_only=True)
    vehicle_number = serializers.CharField(source="vehicle.vehicle_number", read_only=True)
    stops = RouteStopSerializer(many=True, required=False)

    pickup_assigned = serializers.SerializerMethodField()
    drop_assigned = serializers.SerializerMethodField()

    class Meta:
        model = RouteTemplate
        fields = [
            "id",
            "name",
            "driver",
            "driver_name",
            "vehicle",
            "vehicle_number",
            "created_at",
            "stops",
            "pickup_assigned",
            "drop_assigned",
        ]
        read_only_fields = [
            "vehicle",
            "vehicle_number",
            "driver_name",
            "created_at",
            "pickup_assigned",
            "drop_assigned",
        ]

    def _get_requested_date(self):
        request = self.context.get("request")
        if request:
            date_str = request.query_params.get("date")
            if date_str:
                try:
                    return timezone.datetime.strptime(date_str, "%Y-%m-%d").date()
                except ValueError:
                    pass
        return timezone.localdate()

    def _is_trip_type_assigned_for_date(self, obj, trip_type):
        requested_date = self._get_requested_date()

        return Trip.objects.filter(
            route_run__route_template=obj,
            trip_date=requested_date,
            trip_type=trip_type,
        ).exclude(
            status=Trip.STATUS_CANCELLED,
        ).exists()

    def get_pickup_assigned(self, obj):
        return self._is_trip_type_assigned_for_date(obj, Trip.TRIP_TYPE_PICKUP)

    def get_drop_assigned(self, obj):
        return self._is_trip_type_assigned_for_date(obj, Trip.TRIP_TYPE_DROP)

    def validate_name(self, value):
        qs = RouteTemplate.objects.filter(name__iexact=value.strip())

        if self.instance:
            qs = qs.exclude(id=self.instance.id)

        if qs.exists():
            raise serializers.ValidationError(
                "A route with this name already exists."
            )

        return value.strip()

    def validate_driver(self, driver):
        qs = RouteTemplate.objects.filter(driver=driver)

        if self.instance:
            qs = qs.exclude(id=self.instance.id)

        if qs.exists():
            raise serializers.ValidationError(
                "This driver is already assigned to another saved route."
            )

        return driver

    def validate_stops(self, stops_data):
        employee_ids = [stop["employee"].id for stop in stops_data]

        if len(employee_ids) != len(set(employee_ids)):
            raise serializers.ValidationError(
                "Duplicate employees are not allowed in the same route."
            )

        qs = RouteStop.objects.filter(employee_id__in=employee_ids)

        if self.instance:
            qs = qs.exclude(route=self.instance)

        already_assigned_ids = set(qs.values_list("employee_id", flat=True))

        if already_assigned_ids:
            already_assigned_users = [
                str(stop["employee"].username)
                for stop in stops_data
                if stop["employee"].id in already_assigned_ids
            ]
            raise serializers.ValidationError(
                {
                    "stops": (
                        "These employees are already assigned in another saved route: "
                        + ", ".join(already_assigned_users)
                    )
                }
            )

        return stops_data

    def create(self, validated_data):
        stops_data = validated_data.pop("stops", [])
        driver = validated_data["driver"]

        try:
            vehicle = driver.vehicle
        except Vehicle.DoesNotExist:
            raise serializers.ValidationError(
                {"driver": "Selected driver does not have a vehicle."}
            )

        if len(stops_data) > vehicle.seat_count:
            raise serializers.ValidationError(
                {
                    "stops": (
                        f"Vehicle seat capacity is {vehicle.seat_count}. "
                        f"You tried to assign {len(stops_data)} employees."
                    )
                }
            )

        route = RouteTemplate.objects.create(
            vehicle=vehicle,
            **validated_data,
        )

        for stop in stops_data:
            RouteStop.objects.create(route=route, **stop)

        return route

    def update(self, instance, validated_data):
        stops_data = validated_data.pop("stops", None)
        driver = validated_data.get("driver", instance.driver)

        try:
            vehicle = driver.vehicle
        except Vehicle.DoesNotExist:
            raise serializers.ValidationError(
                {"driver": "Selected driver does not have a vehicle."}
            )

        instance.name = validated_data.get("name", instance.name).strip()
        instance.driver = driver
        instance.vehicle = vehicle

        if stops_data is not None:
            if len(stops_data) > vehicle.seat_count:
                raise serializers.ValidationError(
                    {
                        "stops": (
                            f"Vehicle seat capacity is {vehicle.seat_count}. "
                            f"You tried to assign {len(stops_data)} employees."
                        )
                    }
                )

            employee_ids = [stop["employee"].id for stop in stops_data]
            if len(employee_ids) != len(set(employee_ids)):
                raise serializers.ValidationError(
                    {"stops": "Duplicate employees are not allowed in the same route."}
                )

            qs = RouteStop.objects.filter(employee_id__in=employee_ids).exclude(
                route=instance
            )
            already_assigned_ids = set(qs.values_list("employee_id", flat=True))

            if already_assigned_ids:
                already_assigned_users = [
                    str(stop["employee"].username)
                    for stop in stops_data
                    if stop["employee"].id in already_assigned_ids
                ]
                raise serializers.ValidationError(
                    {
                        "stops": (
                            "These employees are already assigned in another saved route: "
                            + ", ".join(already_assigned_users)
                        )
                    }
                )

            instance.stops.all().delete()

            for stop in stops_data:
                RouteStop.objects.create(route=instance, **stop)

        instance.save()
        return instance
    
class RouteRunLiveStopSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    stop_order = serializers.IntegerField()
    employee_name = serializers.CharField()
    pickup_location = serializers.CharField(allow_null=True, required=False)
    pickup_latitude = serializers.FloatField(allow_null=True, required=False)
    pickup_longitude = serializers.FloatField(allow_null=True, required=False)
    is_picked = serializers.BooleanField()
    status = serializers.CharField()
    distance_km = serializers.FloatField(allow_null=True)
    distance_text = serializers.CharField(allow_null=True)
    eta_minutes = serializers.IntegerField(allow_null=True)
    eta_text = serializers.CharField(allow_null=True)


class RouteRunLiveStatusSerializer(serializers.Serializer):
    route_run_id = serializers.IntegerField()
    route_name = serializers.CharField()
    driver_name = serializers.CharField(allow_null=True)
    vehicle_number = serializers.CharField(allow_null=True)
    trip_type = serializers.CharField()
    current_stop_order = serializers.IntegerField(allow_null=True)
    remaining_stops = serializers.IntegerField()
    completed_stops = serializers.IntegerField()
    total_stops = serializers.IntegerField()
    status_text = serializers.CharField()
    driver_latitude = serializers.FloatField(allow_null=True)
    driver_longitude = serializers.FloatField(allow_null=True)
    last_updated = serializers.DateTimeField(allow_null=True)
    current_stop = serializers.DictField(allow_null=True)
    next_stop = serializers.DictField(allow_null=True)
    stops = RouteRunLiveStopSerializer(many=True)


class EmployeeLivePickupStatusSerializer(serializers.Serializer):
    route_run_id = serializers.IntegerField(allow_null=True)
    route_name = serializers.CharField(allow_null=True)
    vehicle_number = serializers.CharField(allow_null=True)
    driver_name = serializers.CharField(allow_null=True)

    your_stop_order = serializers.IntegerField(allow_null=True)
    current_stop_order = serializers.IntegerField(allow_null=True)
    stops_before_you = serializers.IntegerField(default=0)

    your_status = serializers.CharField()
    status_text = serializers.CharField()

    your_eta_minutes = serializers.IntegerField(allow_null=True)
    your_eta_text = serializers.CharField(allow_null=True)
    your_distance_km = serializers.FloatField(allow_null=True)
    your_distance_text = serializers.CharField(allow_null=True)

    current_stop_name = serializers.CharField(allow_null=True)
    next_stop_name = serializers.CharField(allow_null=True)

    driver_latitude = serializers.FloatField(allow_null=True)
    driver_longitude = serializers.FloatField(allow_null=True)
    last_updated = serializers.DateTimeField(allow_null=True)