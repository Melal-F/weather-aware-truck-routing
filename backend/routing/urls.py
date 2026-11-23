from django.urls import path

from routing.views import GeocodeView, PlanView, TimezoneView

urlpatterns = [
    path("geocode", GeocodeView.as_view()),
    path("plan", PlanView.as_view()),
    path("timezone", TimezoneView.as_view()),
]
