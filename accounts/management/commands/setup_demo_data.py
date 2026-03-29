from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.db import transaction

from trips.models import Vehicle

User = get_user_model()


class Command(BaseCommand):
    help = "Create demo admin, drivers, employees, vehicles, and employee pickup locations."

    @transaction.atomic
    def handle(self, *args, **options):
        self.stdout.write(self.style.WARNING("Starting demo data setup..."))

        self.create_admin()
        self.create_drivers()
        self.create_employees()

        self.stdout.write(self.style.SUCCESS("Demo data setup completed successfully."))

    def create_admin(self):
        username = "admin"
        password = "admin123"

        admin_user, created = User.objects.get_or_create(
            username=username,
            defaults={
                "role": "ADMIN",
                "phone_number": "9999999999",
                "address": "Main Office Admin",
                "is_staff": True,
                "is_superuser": True,
            },
        )

        admin_user.role = "ADMIN"
        admin_user.phone_number = "9999999999"
        admin_user.address = "Main Office Admin"
        admin_user.is_staff = True
        admin_user.is_superuser = True
        admin_user.set_password(password)
        admin_user.save()

        if created:
            self.stdout.write(self.style.SUCCESS(f"Created admin: {username} / {password}"))
        else:
            self.stdout.write(self.style.SUCCESS(f"Updated admin: {username} / {password}"))

    def create_drivers(self):
        for i in range(1, 9):
            username = f"driver{i}"
            password = f"driver123{i}"
            vehicle_number = f"DL01CAB{i:03d}"
            vehicle_model = f"Cab Model {i}"
            seat_count = 4 + (i % 3)

            driver_user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    "role": "DRIVER",
                    "phone_number": f"90000000{i:02d}",
                    "address": f"Driver Address {i}",
                },
            )

            driver_user.role = "DRIVER"
            driver_user.phone_number = f"90000000{i:02d}"
            driver_user.address = f"Driver Address {i}"
            driver_user.set_password(password)
            driver_user.save()

            vehicle, vehicle_created = Vehicle.objects.get_or_create(
                driver=driver_user,
                defaults={
                    "vehicle_number": vehicle_number,
                    "vehicle_model": vehicle_model,
                    "seat_count": seat_count,
                },
            )

            if not vehicle_created:
                vehicle.vehicle_number = vehicle_number
                vehicle.vehicle_model = vehicle_model
                vehicle.seat_count = seat_count
                vehicle.save()

            if created:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Created driver: {username} / {password} | Vehicle: {vehicle_number}"
                    )
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Updated driver: {username} / {password} | Vehicle: {vehicle_number}"
                    )
                )

    def create_employees(self):
        # Gurgaon / Delhi NCR-like sample coordinates
        employee_locations = [
            ("Sector 11, Gurgaon", 28.4711, 77.0178),
            ("Sector 12, Gurgaon", 28.4728, 77.0220),
            ("Sector 14, Gurgaon", 28.4669, 77.0415),
            ("Sector 15, Gurgaon", 28.4606, 77.0458),
            ("Sector 17, Gurgaon", 28.4631, 77.0572),
            ("Sector 21, Gurgaon", 28.5164, 77.0732),
            ("Sector 22, Gurgaon", 28.5187, 77.0813),
            ("Sector 23, Gurgaon", 28.5142, 77.0891),
            ("Sector 28, Gurgaon", 28.4675, 77.0889),
            ("Sector 29, Gurgaon", 28.4689, 77.0727),
            ("Sector 31, Gurgaon", 28.4518, 77.0502),
            ("Sector 38, Gurgaon", 28.4334, 77.0538),
            ("Sector 40, Gurgaon", 28.4451, 77.0589),
            ("Sector 43, Gurgaon", 28.4598, 77.0964),
            ("Sector 45, Gurgaon", 28.4402, 77.1028),
            ("Sector 46, Gurgaon", 28.4353, 77.0697),
            ("DLF Phase 1, Gurgaon", 28.4754, 77.1026),
            ("DLF Phase 2, Gurgaon", 28.4973, 77.0886),
            ("DLF Phase 3, Gurgaon", 28.4949, 77.1011),
            ("Sushant Lok, Gurgaon", 28.4671, 77.0759),
        ]

        for i in range(1, 21):
            username = f"employee{i}"
            password = f"emp123{i}"
            phone_number = f"80000000{i:02d}"
            address = f"Employee Address {i}"

            pickup_location, pickup_latitude, pickup_longitude = employee_locations[i - 1]

            employee_user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    "role": "EMPLOYEE",
                    "phone_number": phone_number,
                    "address": address,
                    "pickup_location": pickup_location,
                    "pickup_latitude": pickup_latitude,
                    "pickup_longitude": pickup_longitude,
                },
            )

            employee_user.role = "EMPLOYEE"
            employee_user.phone_number = phone_number
            employee_user.address = address
            employee_user.pickup_location = pickup_location
            employee_user.pickup_latitude = pickup_latitude
            employee_user.pickup_longitude = pickup_longitude
            employee_user.set_password(password)
            employee_user.save()

            if created:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Created employee: {username} / {password} | "
                        f"Location: {pickup_location} ({pickup_latitude}, {pickup_longitude})"
                    )
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Updated employee: {username} / {password} | "
                        f"Location: {pickup_location} ({pickup_latitude}, {pickup_longitude})"
                    )
                )