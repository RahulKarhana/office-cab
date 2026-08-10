from django.contrib import admin
from trips.models import Trip, Vehicle, Review, Notification, DriverLocation, TripCancellation, DeviceToken


@admin.register(Trip)
class TripAdmin(admin.ModelAdmin):
    list_display = ("id", "employee", "driver", "status", "pickup_time")
    list_filter = ("status",)
    search_fields = ("pickup_location", "drop_location", "employee_username", "driver_username")



admin.site.register(Vehicle)
admin.site.register(Review)
admin.site.register(Notification)
admin.site.register(DriverLocation)
admin.site.register(TripCancellation)

@admin.register(DeviceToken)
class DeviceTokenAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "token")
    list_filter = ("user",)
    search_fields = ("token",)