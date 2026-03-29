from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from trips.models import Vehicle

User = get_user_model()


class Command(BaseCommand):
    help = "Seed initial admin, drivers, employees, and vehicles"

    def handle(self, *args, **options):
        self.stdout.write(self.style.WARNING("Seeding data..."))

        # Admin
        if not User.objects.filter(username="admin").exists():
            User.objects.create_superuser(
                username="admin",
                password="admin123",
                role="ADMIN",
            )
            self.stdout.write(self.style.SUCCESS("Admin created: admin / admin123"))
        else:
            self.stdout.write("Admin already exists")

        # Drivers
        drivers_data = [
            {
                "username": "driver1",
                "password": "driver123",
                "phone_number": "9000000001",
                "address": "Gurgaon Sector 21",
                "vehicle_number": "HR26AB1001",
                "vehicle_model": "Swift Dzire",
                "seat_count": 4,
            },
            {
                "username": "driver2",
                "password": "driver123",
                "phone_number": "9000000002",
                "address": "Gurgaon Sector 22",
                "vehicle_number": "HR26AB1002",
                "vehicle_model": "Ertiga",
                "seat_count": 6,
            },
        ]

        for data in drivers_data:
            username = data["username"]
            if User.objects.filter(username=username).exists():
                self.stdout.write(f"Driver already exists: {username}")
                continue

            driver = User.objects.create_user(
                username=username,
                password=data["password"],
                role="DRIVER",
                phone_number=data["phone_number"],
                address=data["address"],
            )

            Vehicle.objects.create(
                driver=driver,
                vehicle_number=data["vehicle_number"],
                vehicle_model=data["vehicle_model"],
                seat_count=data["seat_count"],
            )

            self.stdout.write(self.style.SUCCESS(f"Driver created: {username} / {data['password']}"))

        # Employees
        employees_data = [
            {
                "username": "employee1",
                "password": "emp123",
                "phone_number": "9100000001",
                "address": "DLF Phase 1, Gurgaon",
                "pickup_location": "DLF Phase 1, Gurgaon",
                "pickup_latitude": 28.4750,
                "pickup_longitude": 77.0785,
            },
            {
                "username": "employee2",
                "password": "emp123",
                "phone_number": "9100000002",
                "address": "Sector 14, Gurgaon",
                "pickup_location": "Sector 14, Gurgaon",
                "pickup_latitude": 28.4601,
                "pickup_longitude": 77.0460,
            },
            {
                "username": "employee3",
                "password": "emp123",
                "phone_number": "9100000003",
                "address": "Palam Vihar, Gurgaon",
                "pickup_location": "Palam Vihar, Gurgaon",
                "pickup_latitude": 28.5040,
                "pickup_longitude": 77.0370,
            },
            {
                "username": "employee4",
                "password": "emp123",
                "phone_number": "9100000004",
                "address": "Udyog Vihar, Gurgaon",
                "pickup_location": "Udyog Vihar, Gurgaon",
                "pickup_latitude": 28.4977,
                "pickup_longitude": 77.0833,
            },
        ]

        for data in employees_data:
            username = data["username"]
            if User.objects.filter(username=username).exists():
                self.stdout.write(f"Employee already exists: {username}")
                continue

            User.objects.create_user(
                username=username,
                password=data["password"],
                role="EMPLOYEE",
                phone_number=data["phone_number"],
                address=data["address"],
                pickup_location=data["pickup_location"],
                pickup_latitude=data["pickup_latitude"],
                pickup_longitude=data["pickup_longitude"],
            )

            self.stdout.write(self.style.SUCCESS(f"Employee created: {username} / {data['password']}"))

        self.stdout.write(self.style.SUCCESS("Seed data completed successfully."))