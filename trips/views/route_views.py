import requests
from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework import status


class RouteAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        origin_lat = request.data.get("origin_lat")
        origin_lng = request.data.get("origin_lng")
        dest_lat = request.data.get("dest_lat")
        dest_lng = request.data.get("dest_lng")

        if None in [origin_lat, origin_lng, dest_lat, dest_lng]:
            return Response(
                {
                    "error": "origin_lat, origin_lng, dest_lat, dest_lng are required"
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        url = "https://routes.googleapis.com/directions/v2:computeRoutes"

        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": settings.GOOGLE_MAPS_API_KEY,
            "X-Goog-FieldMask": "routes.duration,routes.distanceMeters,routes.polyline.encodedPolyline",
        }

        payload = {
            "origin": {
                "location": {
                    "latLng": {
                        "latitude": float(origin_lat),
                        "longitude": float(origin_lng),
                    }
                }
            },
            "destination": {
                "location": {
                    "latLng": {
                        "latitude": float(dest_lat),
                        "longitude": float(dest_lng),
                    }
                }
            },
            "travelMode": "DRIVE",
            "routingPreference": "TRAFFIC_AWARE",
        }

        google_response = requests.post(url, json=payload, headers=headers, timeout=20)

        if google_response.status_code != 200:
            return Response(
                {
                    "error": "Failed to fetch route",
                    "details": google_response.text,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        data = google_response.json()
        routes = data.get("routes", [])

        if not routes:
            return Response(
                {"error": "No route found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        route = routes[0]

        return Response(
            {
                "encoded_polyline": route.get("polyline", {}).get("encodedPolyline"),
                "distance_meters": route.get("distanceMeters"),
                "duration": route.get("duration"),
            },
            status=status.HTTP_200_OK,
        )