from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth import get_user_model

User = get_user_model()


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    # Show these columns in user list
    list_display = ("username", "email", "role", "is_staff", "is_active")
    list_filter = ("role", "is_staff", "is_active")

    # Add "role" and "phone_number" in user edit page
    fieldsets = BaseUserAdmin.fieldsets + (
        ("Extra Info", {"fields": ("role", "phone_number")}),
    )

    # Add "role" and "phone_number" in add-user page
    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        ("Extra Info", {"fields": ("role", "phone_number")}),
    )

    search_fields = ("username", "email", "phone_number")
    ordering = ("username",)

# Register your models here.
