from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied, ValidationError

from trips.models import Review
from trips.serializers import ReviewSerializer


class ReviewViewSet(viewsets.ModelViewSet):
    serializer_class = ReviewSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "post"]

    def get_queryset(self):
        user = self.request.user

        if getattr(user, "role", "") == "ADMIN":
            return Review.objects.all().order_by("-created_at")

        if getattr(user, "role", "") == "EMPLOYEE":
            return Review.objects.filter(
                employee=user
            ).order_by("-created_at")

        return Review.objects.none()

    def perform_create(self, serializer):
        user = self.request.user

        if getattr(user, "role", "") != "EMPLOYEE":
            raise PermissionDenied(
                "Only employees can submit reviews."
            )

        trip = serializer.validated_data.get("trip")

        if trip is None:
            raise ValidationError({
                "trip": "Trip is required."
            })

        if trip.employee_id != user.id:
            raise PermissionDenied(
                "You can only review your own trips."
            )

        if trip.status != trip.STATUS_COMPLETED:
            raise PermissionDenied(
                "Trip must be completed before review."
            )

        if Review.objects.filter(trip=trip).exists():
            raise ValidationError({
                "trip": "Review already submitted for this trip."
            })

        serializer.save(employee=user)