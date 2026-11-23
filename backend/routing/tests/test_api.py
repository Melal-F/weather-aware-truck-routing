import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django
from django.conf import settings

if not settings.configured:
    django.setup()

from django.http import HttpResponse
from django.middleware.gzip import GZipMiddleware
from django.test import Client, RequestFactory, SimpleTestCase, override_settings

from config.env import load_env


@override_settings(ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"])
class PlanApiTest(SimpleTestCase):
    def setUp(self):
        super().setUp()
        patcher = patch(
            "routing.services._utcnow",
            return_value=datetime(2026, 11, 18, 11, 0, tzinfo=timezone.utc),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    @patch.dict(os.environ, {"ORS_API_KEY": ""})
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

    @patch("routing.views.location_timezone", return_value="America/New_York")
    def test_timezone_returns_the_iana_name(self, lookup):
        response = Client().get("/api/timezone", {"lat": "33.75", "lng": "-84.39"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"tz": "America/New_York"})
        lookup.assert_called_once_with(33.75, -84.39)

    def test_timezone_requires_coordinates(self):
        response = Client().get("/api/timezone")
        self.assertEqual(response.status_code, 400)

    def test_geocode_short_query_is_empty(self):
        response = Client().get("/api/geocode", {"q": "a"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"], [])

    @patch("routing.views.ors_autocomplete", return_value=[{"name": "Chicago", "lat": 41.8, "lng": -87.6}])
    def test_geocode_returns_results(self, _search):
        response = Client().get("/api/geocode", {"q": "Chicago"})
        self.assertEqual(response.json()["results"][0]["name"], "Chicago")

    def test_referrer_policy_header(self):
        response = Client().get("/")
        self.assertEqual(response["Referrer-Policy"], "strict-origin-when-cross-origin")

    def test_gzip_content_encoding(self):
        self.assertEqual(settings.MIDDLEWARE[0], "django.middleware.gzip.GZipMiddleware")
        request = RequestFactory().get("/", HTTP_ACCEPT_ENCODING="gzip")
        response = GZipMiddleware(lambda _request: HttpResponse(b"x" * 2048))(request)
        self.assertEqual(response["Content-Encoding"], "gzip")


_ENV_SAMPLE = "A=1\n# c\nB = 'two'\nC=x=y\n"


class LoadEnvTest(SimpleTestCase):
    def _load_sample(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(_ENV_SAMPLE, encoding="utf-8")
            load_env(path)

    def _restore(self, snapshot):
        for key in ("A", "B", "C"):
            if key in snapshot:
                os.environ[key] = snapshot[key]
            else:
                os.environ.pop(key, None)

    def test_load_env_does_not_override_existing(self):
        snapshot = {key: os.environ[key] for key in ("A", "B", "C") if key in os.environ}
        os.environ["A"] = "preset"
        try:
            self._load_sample()
            self.assertEqual(os.environ["A"], "preset")
        finally:
            self._restore(snapshot)

    def test_load_env_parses_assignments(self):
        snapshot = {key: os.environ[key] for key in ("A", "B", "C") if key in os.environ}
        for key in ("A", "B", "C"):
            os.environ.pop(key, None)
        try:
            self._load_sample()
            self.assertEqual(os.environ["A"], "1")
            self.assertEqual(os.environ["B"], "two")
            self.assertEqual(os.environ["C"], "x=y")
        finally:
            self._restore(snapshot)
