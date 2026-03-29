from django import forms
from .models import Trip

class TripForm(forms.ModelForm):
    class Meta:
        model = Trip
        fields = [
            "employee",
            "driver",
            "vehicle",
            "pickup_location",
            "drop_location",
            "pickup_time",
        ]