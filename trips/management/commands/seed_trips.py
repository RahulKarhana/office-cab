from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from trips.models import Vehicle

User = get_user_model()


class Command(BaseCommand):
    help = "Seed admin, drivers, employees, vehicles, and sample employee locations only"

    def handle(self, *args, **kwargs):
        self.stdout.write("Seeding data...")

        # =========================
        # DELETE OLD SAMPLE USERS
        # =========================
        old_usernames = [
            "driver1", "driver2",
            "employee1", "employee2", "employee3", "employee4"
        ]

        Vehicle.objects.filter(driver__username__in=old_usernames).delete()
        User.objects.filter(username__in=old_usernames).delete()

        self.stdout.write(self.style.WARNING("Old sample users deleted if they existed."))

        # =========================
        # ADMIN
        # =========================
        admin, created = User.objects.get_or_create(
            username="admin",
            defaults={
                "role": "ADMIN",
                "first_name": "Office",
                "last_name": "Admin",
                "is_staff": True,
                "is_superuser": True,
            },
        )
        admin.set_password("admin123")
        admin.is_staff = True
        admin.is_superuser = True
        if hasattr(admin, "role"):
            admin.role = "ADMIN"
        admin.save()

        if created:
            self.stdout.write(self.style.SUCCESS("Admin created: admin / admin123"))
        else:
            self.stdout.write(self.style.WARNING("Admin already exists: admin / admin123"))

        # =========================
        # DRIVERS
        # =========================
        drivers_data = [
            {
                "username": "mahesh_driver",
                "password": "driver123",
                "first_name": "Mahesh",
                "last_name": "Yadav",
                "phone_number": "9000000001",
                "vehicle_number": "HR26AB1001",
                "vehicle_model": "Swift Dzire",
            },
            {
                "username": "sunil_driver",
                "password": "driver123",
                "first_name": "Sunil",
                "last_name": "Chauhan",
                "phone_number": "9000000002",
                "vehicle_number": "HR26CD2002",
                "vehicle_model": "Ertiga",
            },
            {
                "username": "ravi_driver",
                "password": "driver123",
                "first_name": "Ravi",
                "last_name": "Kumar",
                "phone_number": "9000000003",
                "vehicle_number": "HR26EF3003",
                "vehicle_model": "Innova",
            },
            {
                "username": "amit_driver",
                "password": "driver123",
                "first_name": "Amit",
                "last_name": "Singh",
                "phone_number": "9000000004",
                "vehicle_number": "HR26GH4004",
                "vehicle_model": "Xylo",
            },
            {
                "username": "rohit_driver",
                "password": "driver123",
                "first_name": "Rohit",
                "last_name": "Verma",
                "phone_number": "9000000005",
                "vehicle_number": "HR26IJ5005",
                "vehicle_model": "WagonR",
            },
            {
                "username": "karan_driver",
                "password": "driver123",
                "first_name": "Karan",
                "last_name": "Mehta",
                "phone_number": "9000000006",
                "vehicle_number": "HR26KL6006",
                "vehicle_model": "Baleno",
            },
            {
                "username": "vijay_driver",
                "password": "driver123",
                "first_name": "Vijay",
                "last_name": "Thakur",
                "phone_number": "9000000007",
                "vehicle_number": "HR26MN7007",
                "vehicle_model": "Ciaz",
            },
            {
                "username": "deep_driver",
                "password": "driver123",
                "first_name": "Deep",
                "last_name": "Sharma",
                "phone_number": "9000000008",
                "vehicle_number": "HR26PQ8008",
                "vehicle_model": "Bolero",
            },
        ]

        for item in drivers_data:
            driver, created = User.objects.get_or_create(
                username=item["username"],
                defaults={
                    "role": "DRIVER",
                    "first_name": item["first_name"],
                    "last_name": item["last_name"],
                },
            )

            driver.set_password(item["password"])

            if hasattr(driver, "role"):
                driver.role = "DRIVER"
            if hasattr(driver, "first_name"):
                driver.first_name = item["first_name"]
            if hasattr(driver, "last_name"):
                driver.last_name = item["last_name"]
            if hasattr(driver, "phone_number"):
                driver.phone_number = item["phone_number"]

            driver.save()

            if created:
                self.stdout.write(
                    self.style.SUCCESS(
                        f'Driver created: {item["username"]} / {item["password"]}'
                    )
                )
            else:
                self.stdout.write(
                    self.style.WARNING(
                        f'Driver already exists/updated: {item["username"]} / {item["password"]}'
                    )
                )

            vehicle, vehicle_created = Vehicle.objects.get_or_create(
                vehicle_number=item["vehicle_number"],
                defaults={
                    "driver": driver,
                    "vehicle_model": item["vehicle_model"],
                    "seat_count": 4,
                },
            )

            if not vehicle_created:
                vehicle.driver = driver
                vehicle.vehicle_model = item["vehicle_model"]
                if hasattr(vehicle, "seat_count"):
                    vehicle.seat_count = 4
                vehicle.save()

        # =========================
        # EMPLOYEES WITH LOCATIONS
        # =========================
        employees_data = [
            {
                "username": "pooja",
                "password": "emp123",
                "first_name": "Pooja",
                "last_name": "Sharma",
                "phone_number": "9100000001",
                "pickup_location": "DLF Phase 1, Gurgaon",
                "latitude": 28.4676,
                "longitude": 77.0896,
            },
            {
                "username": "anuj",
                "password": "emp123",
                "first_name": "Anuj",
                "last_name": "Kumar",
                "phone_number": "9100000002",
                "pickup_location": "DLF Phase 2, Gurgaon",
                "latitude": 28.4916,
                "longitude": 77.0880,
            },
            {
                "username": "gourav",
                "password": "emp123",
                "first_name": "Gourav",
                "last_name": "Singh",
                "phone_number": "9100000003",
                "pickup_location": "DLF Phase 3, Gurgaon",
                "latitude": 28.4944,
                "longitude": 77.0945,
            },
            {
                "username": "neha",
                "password": "emp123",
                "first_name": "Neha",
                "last_name": "Verma",
                "phone_number": "9100000004",
                "pickup_location": "DLF Phase 4, Gurgaon",
                "latitude": 28.4730,
                "longitude": 77.0830,
            },
            {
                "username": "rahul",
                "password": "emp123",
                "first_name": "Rahul",
                "last_name": "Yadav",
                "phone_number": "9100000005",
                "pickup_location": "DLF Phase 5, Gurgaon",
                "latitude": 28.4595,
                "longitude": 77.0720,
            },
            {
                "username": "sneha",
                "password": "emp123",
                "first_name": "Sneha",
                "last_name": "Gupta",
                "phone_number": "9100000006",
                "pickup_location": "Cyber City, Gurgaon",
                "latitude": 28.4950,
                "longitude": 77.0890,
            },
            {
                "username": "vikas",
                "password": "emp123",
                "first_name": "Vikas",
                "last_name": "Chauhan",
                "phone_number": "9100000007",
                "pickup_location": "Udyog Vihar, Gurgaon",
                "latitude": 28.5070,
                "longitude": 77.0800,
            },
            {
                "username": "rani",
                "password": "emp123",
                "first_name": "Rani",
                "last_name": "Mehta",
                "phone_number": "9100000008",
                "pickup_location": "Sector 14, Gurgaon",
                "latitude": 28.4590,
                "longitude": 77.0410,
            },
            {
                "username": "deepak",
                "password": "emp123",
                "first_name": "Deepak",
                "last_name": "Sharma",
                "phone_number": "9100000009",
                "pickup_location": "Palam Vihar, Gurgaon",
                "latitude": 28.5030,
                "longitude": 77.0370,
            },
            {
                "username": "kiran",
                "password": "emp123",
                "first_name": "Kiran",
                "last_name": "Patel",
                "phone_number": "9100000010",
                "pickup_location": "MG Road, Gurgaon",
                "latitude": 28.4790,
                "longitude": 77.0810,
            },
            {
                "username": "aman",
                "password": "emp123",
                "first_name": "Aman",
                "last_name": "Saini",
                "phone_number": "9100000011",
                "pickup_location": "Sohna Road, Gurgaon",
                "latitude": 28.4220,
                "longitude": 77.0628,
            },
            {
                "username": "rohit",
                "password": "emp123",
                "first_name": "Rohit",
                "last_name": "Bhardwaj",
                "phone_number": "9100000012",
                "pickup_location": "Sector 21, Gurgaon",
                "latitude": 28.5155,
                "longitude": 77.0480,
            },
            {
                "username": "nisha",
                "password": "emp123",
                "first_name": "Nisha",
                "last_name": "Arora",
                "phone_number": "9100000013",
                "pickup_location": "Golf Course Road, Gurgaon",
                "latitude": 28.4382,
                "longitude": 77.1015,
            },
            {
                "username": "komal",
                "password": "emp123",
                "first_name": "Komal",
                "last_name": "Malik",
                "phone_number": "9100000014",
                "pickup_location": "Sector 56, Gurgaon",
                "latitude": 28.4219,
                "longitude": 77.1056,
            },
            {
                "username": "arjun",
                "password": "emp123",
                "first_name": "Arjun",
                "last_name": "Rawat",
                "phone_number": "9100000015",
                "pickup_location": "Sector 45, Gurgaon",
                "latitude": 28.4389,
                "longitude": 77.0637,
            },
            {
                "username": "priya",
                "password": "emp123",
                "first_name": "Priya",
                "last_name": "Kapoor",
                "phone_number": "9100000016",
                "pickup_location": "New Colony, Gurgaon",
                "latitude": 28.4729,
                "longitude": 77.0285,
            },
            {
                "username": "sachin",
                "password": "emp123",
                "first_name": "Sachin",
                "last_name": "Tomar",
                "phone_number": "9100000017",
                "pickup_location": "Sector 31, Gurgaon",
                "latitude": 28.4506,
                "longitude": 77.0502,
            },
            {
                "username": "meena",
                "password": "emp123",
                "first_name": "Meena",
                "last_name": "Joshi",
                "phone_number": "9100000018",
                "pickup_location": "Sector 40, Gurgaon",
                "latitude": 28.4548,
                "longitude": 77.0662,
            },
            {
                "username": "tarun",
                "password": "emp123",
                "first_name": "Tarun",
                "last_name": "Negi",
                "phone_number": "9100000019",
                "pickup_location": "South City 1, Gurgaon",
                "latitude": 28.4484,
                "longitude": 77.0821,
            },
            {
                "username": "shivam",
                "password": "emp123",
                "first_name": "Shivam",
                "last_name": "Mishra",
                "phone_number": "9100000020",
                "pickup_location": "Nirvana Country, Gurgaon",
                "latitude": 28.4175,
                "longitude": 77.0548,
            },
        ]

        for item in employees_data:
            employee, created = User.objects.get_or_create(
                username=item["username"],
                defaults={
                    "role": "EMPLOYEE",
                    "first_name": item["first_name"],
                    "last_name": item["last_name"],
                },
            )

            employee.set_password(item["password"])

            if hasattr(employee, "role"):
                employee.role = "EMPLOYEE"
            if hasattr(employee, "first_name"):
                employee.first_name = item["first_name"]
            if hasattr(employee, "last_name"):
                employee.last_name = item["last_name"]
            if hasattr(employee, "phone_number"):
                employee.phone_number = item["phone_number"]

            # location fields: support both naming styles
            if hasattr(employee, "pickup_location"):
                employee.pickup_location = item["pickup_location"]

            if hasattr(employee, "latitude"):
                employee.latitude = item["latitude"]
            if hasattr(employee, "longitude"):
                employee.longitude = item["longitude"]

            if hasattr(employee, "pickup_latitude"):
                employee.pickup_latitude = item["latitude"]
            if hasattr(employee, "pickup_longitude"):
                employee.pickup_longitude = item["longitude"]

            employee.save()

            if created:
                self.stdout.write(
                    self.style.SUCCESS(
                        f'Employee created: {item["username"]} / {item["password"]}'
                    )
                )
            else:
                self.stdout.write(
                    self.style.WARNING(
                        f'Employee already exists/updated: {item["username"]} / {item["password"]}'
                    )
                )

        self.stdout.write(self.style.SUCCESS("Seed data completed successfully."))