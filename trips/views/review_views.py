from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied

from trips.models import Review
from trips.serializers import ReviewSerializer


class ReviewViewSet(viewsets.ModelViewSet):
    serializer_class = ReviewSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'post']  # No update/delete allowed

    def get_queryset(self):
        user = self.request.user

        if user.role == "ADMIN":
            return Review.objects.all().order_by("-created_at")

        if user.role == "EMPLOYEE":
            return Review.objects.filter(employee=user).order_by("-created_at")

        # Drivers should not see reviews
        return Review.objects.none()

    def perform_create(self, serializer):
        user = self.request.user

        # Only employee can create review
        if user.role != "EMPLOYEE":
            raise PermissionDenied("Only employees can submit reviews.")

        trip = serializer.validated_data.get("trip")

        # Ensure trip belongs to employee
        if trip.employee != user:
            raise PermissionDenied("You can only review your own trips.")

        # Ensure trip is completed
        if trip.status != trip.STATUS_COMPLETED:
            raise PermissionDenied("Trip must be completed before review.")

        # Prevent duplicate review
        if hasattr(trip, "review"):
            raise PermissionDenied("Review already submitted for this trip.")

        serializer.save(employee=user)