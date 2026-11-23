import os
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django
from django.conf import settings

if not settings.configured:
    django.setup()

from django.core.cache import cache
from django.test import Client, SimpleTestCase, override_settings

from routing.services import ors_autocomplete


def _response(status, payload):
    class Response:
        status_code = status

        def json(self):
            return payload

    return Response()


def _feature(label, lng, lat):
    return {
        "properties": {"label": label},
        "geometry": {"coordinates": [lng, lat]},
    }


@override_settings(ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"])
class OrsAutocompleteTest(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    @patch.dict(os.environ, {"ORS_API_KEY": "test-key"})
    @patch("routing.services.requests.get")
    def test_parses_label_and_lng_lat(self, get):
        get.return_value = _response(
            200,
            {"features": [_feature("Dallas, TX, USA", -96.797, 32.777)]},
        )
        self.assertEqual(
            ors_autocomplete("Dallas"),
            [{"name": "Dallas, TX, USA", "lat": 32.777, "lng": -96.797}],
        )
        self.assertEqual(get.call_args.kwargs["timeout"], (5, 20))

    @patch.dict(os.environ, {"ORS_API_KEY": "test-key"})
    @patch("routing.services.requests.get")
    def test_keeps_only_the_first_five_features(self, get):
        features = [_feature(f"Place {i}", -96.0 - i, 32.0 + i) for i in range(7)]
        get.return_value = _response(200, {"features": features})
        results = ors_autocomplete("Dallas")
        self.assertEqual(len(results), 5)
        self.assertEqual([row["name"] for row in results], [f"Place {i}" for i in range(5)])

    @patch.dict(os.environ, {"ORS_API_KEY": "test-key"})
    @patch("routing.services.requests.get")
    def test_cache_reuses_case_and_whitespace(self, get):
        get.return_value = _response(
            200,
            {"features": [_feature("Dallas, TX, USA", -96.797, 32.777)]},
        )
        expected = [{"name": "Dallas, TX, USA", "lat": 32.777, "lng": -96.797}]
        first = ors_autocomplete("Dallas")
        second = ors_autocomplete("  dallas ")
        self.assertEqual(get.call_count, 1)
        self.assertEqual(first, expected)
        self.assertEqual(second, expected)

    @patch("routing.services.requests.get")
    def test_two_characters_skips_http(self, get):
        self.assertEqual(ors_autocomplete("ab"), [])
        self.assertEqual(get.call_count, 0)

    @patch.dict(os.environ, {"ORS_API_KEY": "test-key"})
    @patch("routing.services.requests.get")
    def test_rate_limit_is_busy_message(self, get):
        get.return_value = _response(429, {})
        response = Client().get("/api/geocode", {"q": "Dallas"})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["error"], "Geocoding is busy. Wait a second and try again.")
