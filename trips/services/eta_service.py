from math import radians, cos, sin, asin, sqrt

from trips.models import DriverLocation, Trip
from trips.services.google_maps_service import GoogleMapsService
from trips.services.route_service import RouteService
from trips.models import PickupChat


class ETAService:
    @staticmethod
    def calculate_distance_km(lat1, lon1, lat2, lon2):
        if None in [lat1, lon1, lat2, lon2]:
            return None

        lon1, lat1, lon2, lat2 = map(float, [lon1, lat1, lon2, lat2])
        lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])

        dlon = lon2 - lon1
        dlat = lat2 - lat1
        a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
        c = 2 * asin(sqrt(a))
        r = 6371
        return c * r

    @staticmethod
    def estimate_eta_minutes(distance_km, avg_speed_kmph=25):
        if distance_km is None:
            return None
        eta = int((distance_km / avg_speed_kmph) * 60)
        return max(1, eta)

    @staticmethod
    def format_eta_text(eta_minutes):
        if eta_minutes is None:
            return None
        if eta_minutes < 60:
            return f"{eta_minutes} min"
        hours = eta_minutes // 60
        minutes = eta_minutes % 60
        if minutes == 0:
            return f"{hours} hr"
        return f"{hours} hr {minutes} min"

    @staticmethod
    def format_distance_text(distance_km):
        if distance_km is None:
            return None
        if distance_km < 1:
            return f"{int(distance_km * 1000)} m"
        return f"{round(distance_km, 1)} km"

    @staticmethod
    def _duration_between(origin_lat, origin_lng, dest_lat, dest_lng):
        google_result = GoogleMapsService.get_distance_matrix(
            origin_lat,
            origin_lng,
            dest_lat,
            dest_lng,
        )

        if google_result:
            return google_result

        distance_km = ETAService.calculate_distance_km(
            origin_lat,
            origin_lng,
            dest_lat,
            dest_lng,
        )
        eta_minutes = ETAService.estimate_eta_minutes(distance_km)

        return {
            "distance_km": round(distance_km, 2) if distance_km is not None else None,
            "distance_text": ETAService.format_distance_text(distance_km),
            "eta_minutes": eta_minutes,
            "duration_text": ETAService.format_eta_text(eta_minutes),
        }

    @staticmethod
    def _get_driver_location(driver):
        if not driver:
            return None
        return DriverLocation.objects.filter(driver=driver).order_by("-updated_at").first()

    @staticmethod
    def build_live_status(route_run, employee_user=None):
        stops = list(RouteService.get_ordered_stops(route_run))
        total_stops = len(stops)
        completed_stops = len([s for s in stops if s.is_picked or s.is_no_show])
        remaining_stops = len([s for s in stops if not s.is_picked and not s.is_no_show])

        current_stop = next((s for s in stops if not s.is_picked and not s.is_no_show), None)
        my_stop = next((s for s in stops if employee_user and s.employee_id == employee_user.id), None)
        next_stop = RouteService.get_next_stop_after_current(route_run, current_stop) if current_stop else None

        driver_location = ETAService._get_driver_location(route_run.driver)
        driver_latitude = driver_location.latitude if driver_location else None
        driver_longitude = driver_location.longitude if driver_location else None
        last_updated = driver_location.updated_at if driver_location else None

        live_stops = []
        cumulative_eta = 0
        previous_lat = driver_latitude
        previous_lng = driver_longitude
        current_index = stops.index(current_stop) if current_stop in stops else -1

        for index, stop in enumerate(stops):
            stop_status = "UPCOMING"
            if stop.is_no_show:
            
                stop_status = "NO_SHOW"
            elif stop.is_picked:
                stop_status = "COMPLETED"
            elif current_stop and stop.id == current_stop.id:
                stop_status = "CURRENT"
            elif next_stop and stop.id == next_stop.id:
                stop_status = "NEXT"
            elif my_stop and stop.id == my_stop.id:
                stop_status = "YOUR_STOP"

            distance_km = None
            distance_text = None
            eta_minutes = None

            should_calculate = (
                previous_lat is not None
                and previous_lng is not None
                and stop.pickup_latitude is not None
                and stop.pickup_longitude is not None
                and not stop.is_picked
                and not stop.is_no_show
                and index >= current_index
            )

            if should_calculate:
                segment = ETAService._duration_between(
                    previous_lat,
                    previous_lng,
                    stop.pickup_latitude,
                    stop.pickup_longitude,
                )
                segment_eta = segment.get("eta_minutes") or 0
                cumulative_eta += segment_eta
                distance_km = segment.get("distance_km")
                distance_text = segment.get("distance_text")
                eta_minutes = cumulative_eta
                previous_lat = stop.pickup_latitude
                previous_lng = stop.pickup_longitude

            waiting_minutes = getattr(stop, "waiting_minutes", 10) or 10
            countdown_seconds = None
            driver_has_arrived = bool(getattr(stop, "arrival_time", None))
            chat_enabled = driver_has_arrived

            if getattr(stop, "waiting_started_at", None):
                from django.utils import timezone

                elapsed = int((timezone.now() - stop.waiting_started_at).total_seconds())
                countdown_seconds = max(0, (waiting_minutes * 60) - elapsed)

            chat = None

            if driver_has_arrived:
                chat = PickupChat.objects.filter(
                    route_run=route_run,
                    stop=stop,
                    is_active=True,
                ).first()

            live_stops.append({
                "id": stop.id,
                "stop_order": stop.stop_order,
                "display_order": index + 1,
                "employee_name": stop.employee.username if stop.employee else "",
                "pickup_location": stop.pickup_location,
                "pickup_latitude": stop.pickup_latitude,
                "pickup_longitude": stop.pickup_longitude,
                "is_current_stop": bool(current_stop and stop.id == current_stop.id),
                "is_next_stop": bool(next_stop and stop.id == next_stop.id),
                "show_chat_option": chat_enabled,
                "chat_id": chat.id if chat else None,
                "driver_has_arrived": driver_has_arrived,
                "chat_enabled": chat_enabled,
                "waiting_started_at": getattr(stop, "waiting_started_at", None),
                "countdown_seconds": countdown_seconds,
                "is_picked": stop.is_picked,
                "is_no_show": stop.is_no_show,
                "status": stop_status,
                "distance_km": round(distance_km, 2) if distance_km is not None else None,
                "distance_text": distance_text,
                "eta_minutes": eta_minutes,
                "eta_text": ETAService.format_eta_text(eta_minutes),
                "eta_display_text": (
                    "Pickup Done"
                    if stop.is_picked
                    else "No Show"
                    if stop.is_no_show
                    else ETAService.format_eta_text(eta_minutes)
                ),
                

            })

        current_stop_data = next((x for x in live_stops if current_stop and x["id"] == current_stop.id), None)
        next_stop_data = next((x for x in live_stops if next_stop and x["id"] == next_stop.id), None)
        my_stop_data = next((x for x in live_stops if my_stop and x["id"] == my_stop.id), None)

        route_polyline = None
        pending_with_coords = [
            s for s in stops
            if not s.is_picked
            and not s.is_no_show
            and s.pickup_latitude is not None
            and s.pickup_longitude is not None
        ]

        directions = None
        if driver_latitude is not None and driver_longitude is not None and pending_with_coords:
            destination = pending_with_coords[-1]
            waypoints = [(s.pickup_latitude, s.pickup_longitude) for s in pending_with_coords[:-1]]
            directions = GoogleMapsService.get_directions(
                driver_latitude,
                driver_longitude,
                destination.pickup_latitude,
                destination.pickup_longitude,
                waypoints=waypoints,
            )
            if directions:
                route_polyline = directions.get("polyline")

        route_word = "drop" if route_run.trip_type == Trip.TRIP_TYPE_DROP else "pickup"
        if route_run.completed_at is not None:
            status_text = "Route completed successfully."
        elif my_stop and my_stop.is_picked:
            status_text = "You have been dropped successfully." if route_run.trip_type == Trip.TRIP_TYPE_DROP else "You have already been picked up. Cab is continuing on route."
        elif current_stop and my_stop and current_stop.id == my_stop.id:
            status_text = f"Cab is currently coming for your {route_word}."
        elif next_stop and my_stop and next_stop.id == my_stop.id:
            status_text = f"Current {route_word} is {current_stop.employee.username}. You are next."
        elif my_stop:
            status_text = f"Current {route_word} is {current_stop.employee.username}. Your {route_word} will come later in route."
        else:
            status_text = f"Live {route_word} route is active."

        return {
            "route_run_id": route_run.id,
            "route_name": route_run.route_template.name if route_run.route_template else f"{route_word.capitalize()} Route",
            "driver_name": route_run.driver.username if route_run.driver else None,
            "vehicle_number": route_run.vehicle.vehicle_number if route_run.vehicle else None,
            "trip_type": route_run.trip_type,
            "current_stop_order": current_stop.stop_order if current_stop else None,
            "remaining_stops": remaining_stops,
            "completed_stops": completed_stops,
            "total_stops": total_stops,
            "status_text": status_text,
            "driver_latitude": driver_latitude,
            "driver_longitude": driver_longitude,
            "last_updated": last_updated,
            "current_stop": current_stop_data,
            "next_stop": next_stop_data,
            "my_stop": my_stop_data,
            "office_eta_minutes": directions.get("eta_minutes") if directions else None,
            "office_eta_text": directions.get("eta_text") if directions else None,
            "polyline": route_polyline,
            "stops": live_stops,
        }

    @staticmethod
    def build_employee_live_pickup_status(trip, employee_user):
        data = ETAService.build_live_status(trip.route_run, employee_user=employee_user)
        my_stop = data.get("my_stop") or {}
        current_stop = data.get("current_stop") or {}
        next_stop = data.get("next_stop") or {}

        data.update({
            "trip_id": trip.id,
            "trip_status": trip.status,
            "your_stop_order": my_stop.get("stop_order"),
            "your_status": my_stop.get("status"),
            "your_eta_minutes": my_stop.get("eta_minutes"),
            "your_eta_text": my_stop.get("eta_text"),
            "your_distance_km": my_stop.get("distance_km"),
            "your_distance_text": my_stop.get("distance_text"),
            "driver_distance_text": my_stop.get("distance_text"),
            "current_stop_name": current_stop.get("employee_name"),
            "next_stop_name": next_stop.get("employee_name"),
            "driver_has_arrived": my_stop.get("driver_has_arrived", False),
            "chat_id": my_stop.get("chat_id"),
            "chat_enabled": my_stop.get("chat_enabled", False),
            "countdown_seconds": my_stop.get("countdown_seconds"),
        })
        return data
