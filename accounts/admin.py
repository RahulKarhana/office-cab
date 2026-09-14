from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth import get_user_model

User = get_user_model()


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    # Show these columns in user list
    list_display = (
        "username",
        "full_name",
        "email",
        "phone_number",
        "role",
        "is_staff",
        "is_active",
        "is_female",
    )

    list_filter = (
        "role",
        "is_staff",
        "is_active",
    )

    # Keep Django username for Admin/login compatibility.
    # Full Name is the human-facing Employee/Driver name.
    fieldsets = BaseUserAdmin.fieldsets + (
        (
            "Extra Info",
            {
                "fields": (
                    "full_name",
                    "role",
                    "phone_number",
                    "is_female",
                )
            },
        ),
    )

    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        (
            "Extra Info",
            {
                "fields": (
                    "full_name",
                    "role",
                    "phone_number",
                )
            },
        ),
    )

    search_fields = (
        "username",
        "full_name",
        "email",
        "phone_number",
    )

    ordering = (
        "full_name",
        "username",
    )


# Register your models here.
