import logging

import requests
from rest_framework.response import Response
from rest_framework.views import APIView

from routing.services import PlanError, build_plan, location_timezone, open_meteo, ors_autocomplete, ors_routes

logger = logging.getLogger(__name__)


class GeocodeView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        try:
            results = ors_autocomplete(request.GET.get("q", ""))
        except PlanError as exc:
            return Response({"error": str(exc)}, status=exc.status)
        except requests.RequestException:
            return Response({"error": "Geocoding is unavailable right now."}, status=502)
        return Response({"results": results})


class TimezoneView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        try:
            lat = float(request.GET["lat"])
            lng = float(request.GET["lng"])
        except (KeyError, TypeError, ValueError):
            return Response({"error": "Latitude and longitude are required."}, status=400)
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            return Response({"error": "That place is outside the map."}, status=400)
        try:
            tz = location_timezone(lat, lng)
        except PlanError as exc:
            return Response({"error": str(exc)}, status=exc.status)
        except requests.RequestException:
            return Response({"error": "The weather service is unavailable right now."}, status=502)
        return Response({"tz": tz})


class PlanView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        try:
            plan = build_plan(request.data, ors_routes, open_meteo)
        except PlanError as exc:
            return Response({"error": str(exc)}, status=exc.status)
        except requests.RequestException:
            logger.exception("plan upstream failure")
            return Response({"error": "A routing or weather service is unavailable right now."}, status=502)
        return Response(plan)
