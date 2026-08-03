import json
import urllib.parse
import urllib.request

from django.conf import settings


class GoogleMapsService:
    BASE_URL = "https://maps.googleapis.com/maps/api"

    @staticmethod
    def _api_key():
        return getattr(settings, "GOOGLE_MAPS_API_KEY", None)

    @staticmethod
    def _get_json(path, params):
        api_key = GoogleMapsService._api_key()
        if not api_key:
            return None

        params["key"] = api_key
        url = f"{GoogleMapsService.BASE_URL}/{path}?{urllib.parse.urlencode(params)}"

        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as e:
            print("GOOGLE MAPS ERROR:", e)
            return None

    @staticmethod
    def get_distance_matrix(origin_lat, origin_lng, dest_lat, dest_lng):
        if None in [origin_lat, origin_lng, dest_lat, dest_lng]:
            return None

        data = GoogleMapsService._get_json(
            "distancematrix/json",
            {
                "origins": f"{origin_lat},{origin_lng}",
                "destinations": f"{dest_lat},{dest_lng}",
                "mode": "driving",
                "departure_time": "now",
                "traffic_model": "best_guess",
            },
        )

        if not data or data.get("status") != "OK":
            return None

        rows = data.get("rows") or []
        if not rows:
            return None

        elements = rows[0].get("elements") or []
        if not elements:
            return None

        element = elements[0]
        if element.get("status") != "OK":
            return None

        duration = element.get("duration_in_traffic") or element.get("duration") or {}
        distance = element.get("distance") or {}
        seconds = duration.get("value")
        meters = distance.get("value")

        return {
            "distance_meters": meters,
            "distance_text": distance.get("text"),
            "duration_seconds": seconds,
            "duration_text": duration.get("text"),
            "eta_minutes": max(1, round(seconds / 60)) if seconds is not None else None,
            "distance_km": round(meters / 1000, 2) if meters is not None else None,
        }

    @staticmethod
    def get_directions(origin_lat, origin_lng, destination_lat, destination_lng, waypoints=None):
        if None in [origin_lat, origin_lng, destination_lat, destination_lng]:
            return None

        params = {
            "origin": f"{origin_lat},{origin_lng}",
            "destination": f"{destination_lat},{destination_lng}",
            "mode": "driving",
            "departure_time": "now",
            "traffic_model": "best_guess",
        }

        if waypoints:
            params["waypoints"] = "|".join([f"{lat},{lng}" for lat, lng in waypoints])

        data = GoogleMapsService._get_json("directions/json", params)

        if not data or data.get("status") != "OK":
            return None

        routes = data.get("routes") or []
        if not routes:
            return None

        route = routes[0]
        legs = route.get("legs") or []

        total_seconds = 0
        total_meters = 0

        for leg in legs:
            duration = leg.get("duration_in_traffic") or leg.get("duration") or {}
            distance = leg.get("distance") or {}
            total_seconds += duration.get("value") or 0
            total_meters += distance.get("value") or 0

        eta_minutes = max(1, round(total_seconds / 60)) if total_seconds else None
        distance_km = round(total_meters / 1000, 2) if total_meters else None

        return {
            "polyline": (route.get("overview_polyline") or {}).get("points"),
            "eta_minutes": eta_minutes,
            "eta_text": GoogleMapsService.format_eta_text(eta_minutes),
            "distance_km": distance_km,
            "distance_text": GoogleMapsService.format_distance_text(distance_km),
            "legs": legs,
        }

    @staticmethod
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

    @staticmethod
    def format_distance_text(distance_km):
        if distance_km is None:
            return None
        if distance_km < 1:
            return f"{int(distance_km * 1000)} m"
        return f"{round(distance_km, 1)} km"
