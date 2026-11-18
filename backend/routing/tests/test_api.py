import json
from unittest.mock import patch

from django.test import Client, SimpleTestCase


class PlanApiTest(SimpleTestCase):
    def test_missing_key_is_a_clear_error(self):
        response = Client().post(
            "/api/plan",
            data=json.dumps(
                {
                    "origin": {"name": "A", "lat": 41.8, "lng": -87.6},
                    "destination": {"name": "B", "lat": 39.7, "lng": -104.9},
                    "departure": "2026-11-18T18:00:00Z",
                    "load_lb": 42000,
                    "spacing_miles": 25,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 500)
        self.assertIn("ORS_API_KEY", response.json()["error"])

    def test_geocode_short_query_is_empty(self):
        response = Client().get("/api/geocode", {"q": "a"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"], [])

    @patch("routing.views.nominatim_search", return_value=[{"name": "Chicago", "lat": 41.8, "lng": -87.6}])
    def test_geocode_returns_results(self, _search):
        response = Client().get("/api/geocode", {"q": "Chicago"})
        self.assertEqual(response.json()["results"][0]["name"], "Chicago")
