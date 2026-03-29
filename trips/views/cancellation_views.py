from django.utils import timezone
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied

from trips.models import TripCancellation
from trips.serializers import TripCancellationSerializer


class TripCancellationViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = TripCancellationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.role != "ADMIN":
            raise PermissionDenied("Only admin can view cancellations.")

        qs = TripCancellation.objects.all().order_by("-cancelled_at")

        # Filter: today's cancellations
        today_flag = self.request.query_params.get("today")
        if today_flag and today_flag.lower() in ["1", "true", "yes"]:
            today = timezone.localdate()
            qs = qs.filter(cancelled_at__date=today)

        return qs