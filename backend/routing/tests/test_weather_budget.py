import math
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from django.core.cache import cache

from routing.geo import EARTH_MILES, haversine_miles
from routing.services import PlanError, build_plan, open_meteo


DEPARTURE = "2026-11-18T12:30:00+00:00"
NOW = datetime(2026, 11, 18, 11, 0, tzinfo=timezone.utc)
HOURS = []
_cursor = datetime(2026, 11, 18, tzinfo=timezone.utc)
for _ in range(96):
    HOURS.append(_cursor.strftime("%Y-%m-%dT%H:%M"))
    _cursor += timedelta(hours=1)


def _response(status, payload):
    class Response:
        status_code = status

        def json(self):
            return payload

    return Response()


def _north_south(lng, miles=1500.0):
    span = miles / (EARTH_MILES * math.pi / 180.0)
    steps = 30
    return [(25.0 + span * index / steps, lng) for index in range(steps + 1)]


def _long_routes(_origin, _destination, _load_lb=None):
    return [
        {"coords": _north_south(lng), "duration_seconds": 20 * 3600}
        for lng in (-100.0, -100.03, -99.97)
    ]


def _short_route(_origin, _destination, _load_lb=None):
    # 0.004° apart, so both ends round to the same 0.01° checkpoint.
    return [{"coords": [(41.0, -87.6), (41.004, -87.6)], "duration_seconds": 600}]


def _split_route(_origin, _destination, _load_lb=None):
    # 0.04° apart, so each end keeps its own 0.01° checkpoint.
    return [{"coords": [(41.0, -87.6), (41.04, -87.6)], "duration_seconds": 600}]


def _body(**overrides):
    body = {
        "origin": {"name": "South yard", "lat": 25.0, "lng": -100.0},
        "destination": {"name": "North dock", "lat": 46.7, "lng": -100.0},
        "departure": DEPARTURE,
        "load_lb": 42000,
        "spacing_miles": 10,
    }
    body.update(overrides)
    return body


class WeatherBudgetTest(unittest.TestCase):
    def setUp(self):
        cache.clear()
        patcher = patch("routing.services._utcnow", return_value=NOW)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _stub_forecast(self, get, sent):
        def forecast(url, params=None, timeout=None):
            lats = [part for part in str(params["latitude"]).split(",") if part]
            lngs = [part for part in str(params["longitude"]).split(",") if part]
            sent.extend(zip(lats, lngs))
            count = len(HOURS)
            rows = []
            for lat in lats:
                wind = 12.0 if abs(float(lat) - 41.0) < 1e-6 else 10.0
                rain = 0.0 if abs(float(lat) - 41.0) < 1e-6 else 1.0
                rows.append(
                    {
                        "hourly": {
                            "time": HOURS,
                            "wind_speed_10m": [wind] * count,
                            "rain": [rain] * count,
                            "showers": [0.0] * count,
                            "snowfall": [0.0] * count,
                        }
                    }
                )
            return _response(200, rows[0] if len(rows) == 1 else rows)

        get.side_effect = forecast

    @patch("routing.services.requests.get")
    def test_long_trip_stays_under_cap_and_second_plan_is_cached(self, get):
        sent = []
        self._stub_forecast(get, sent)
        plan = build_plan(_body(), _long_routes, open_meteo)
        unique = set(sent)
        self.assertEqual(len(unique), 350)
        # Checkpoint cells fill the budget, so the corridor is not fetched.
        self.assertEqual(plan["corridor"], [])
        self.assertEqual(plan["corridor_cell_deg"], 0.3)
        scored = 0
        for route in plan["routes"]:
            self.assertTrue(route["checkpoints"])
            for checkpoint in route["checkpoints"]:
                if checkpoint["wind_mph"] is None:
                    self.assertIsNone(checkpoint["level"])
                    continue
                scored += 1
                self.assertIn(checkpoint["level"], ("Low", "Moderate", "High", "Severe", "No Travel"))
                self.assertIsNotNone(checkpoint["rain_in_hr"])
        self.assertGreater(scored, 0)
        calls = get.call_count
        self.assertGreater(calls, 0)
        again = build_plan(_body(), _long_routes, open_meteo)
        self.assertEqual(get.call_count, calls)
        self.assertEqual(again["corridor"], [])
        self.assertEqual(again["corridor_cell_deg"], 0.3)

    @patch("routing.services.requests.get")
    def test_checkpoints_two_km_apart_share_one_location(self, get):
        sent = []
        self._stub_forecast(get, sent)
        plan = build_plan(
            _body(
                origin={"name": "A", "lat": 41.0, "lng": -87.6},
                destination={"name": "B", "lat": 41.004, "lng": -87.6},
                spacing_miles=10,
            ),
            _short_route,
            open_meteo,
        )
        checkpoints = plan["routes"][0]["checkpoints"]
        self.assertGreaterEqual(len(checkpoints), 2)
        gap_km = haversine_miles(
            (checkpoints[0]["lat"], checkpoints[0]["lng"]),
            (checkpoints[1]["lat"], checkpoints[1]["lng"]),
        ) * 1.609344
        self.assertAlmostEqual(gap_km, 0.44, delta=0.15)
        self.assertEqual(checkpoints[0]["wind_mph"], checkpoints[1]["wind_mph"])
        self.assertEqual(checkpoints[0]["rain_in_hr"], checkpoints[1]["rain_in_hr"])
        self.assertEqual(checkpoints[0]["wind_mph"], 12.0)
        self.assertEqual(checkpoints[0]["rain_in_hr"], 0.0)
        near = [
            pair
            for pair in set(sent)
            if abs(float(pair[0]) - 41.0) < 0.05 and abs(float(pair[1]) + 87.6) < 0.05
        ]
        self.assertEqual(len(near), 1)
        self.assertEqual(plan["corridor_cell_deg"], 0.2)

    @patch("routing.services.requests.get")
    def test_checkpoints_four_hundredths_apart_are_separate(self, get):
        sent = []
        self._stub_forecast(get, sent)
        plan = build_plan(
            _body(
                origin={"name": "A", "lat": 41.0, "lng": -87.6},
                destination={"name": "B", "lat": 41.04, "lng": -87.6},
                spacing_miles=10,
            ),
            _split_route,
            open_meteo,
        )
        checkpoints = plan["routes"][0]["checkpoints"]
        self.assertGreaterEqual(len(checkpoints), 2)
        fetched = {
            (round(float(lat), 2), round(float(lng), 2))
            for lat, lng in set(sent)
        }
        self.assertIn((41.0, -87.6), fetched)
        self.assertIn((41.04, -87.6), fetched)
        for lat, lng in fetched:
            if (lat, lng) in ((41.0, -87.6), (41.04, -87.6)):
                continue
            self.assertEqual(lat, round(lat, 1))
            self.assertEqual(lng, round(lng, 1))
        self.assertEqual(plan["corridor_cell_deg"], 0.2)

    @patch("routing.services.requests.get")
    def test_naive_departure_is_read_in_the_origin_zone(self, get):
        def forecast(url, params=None, timeout=None):
            if params.get("hourly") == "temperature_2m":
                return _response(200, {"timezone": "America/Denver", "hourly": {"time": HOURS[:1]}})
            lats = [part for part in str(params["latitude"]).split(",") if part]
            count = len(HOURS)
            rows = [
                {
                    "timezone": "America/Denver",
                    "hourly": {
                        "time": HOURS,
                        "wind_speed_10m": [10.0] * count,
                        "rain": [0.0] * count,
                        "showers": [0.0] * count,
                        "snowfall": [0.0] * count,
                    },
                }
                for _lat in lats
            ]
            return _response(200, rows[0] if len(rows) == 1 else rows)

        get.side_effect = forecast
        plan = build_plan(
            _body(
                origin={"name": "Denver", "lat": 39.74, "lng": -104.99},
                destination={"name": "Salt Lake City", "lat": 40.76, "lng": -111.89},
                departure="2026-11-18T12:30:00",
            ),
            _short_route,
            open_meteo,
        )
        self.assertEqual(plan["departure"], "2026-11-18T19:30:00+00:00")
        self.assertEqual(plan["origin"]["tz"], "America/Denver")
        self.assertEqual(plan["destination"]["tz"], "America/Denver")
        self.assertTrue(all(point["tz"] == "America/Denver" for point in plan["routes"][0]["checkpoints"]))

    @patch("routing.services.requests.get")
    def test_open_meteo_429_is_plan_error_503(self, get):
        get.return_value = _response(429, {"reason": "limit"})
        with self.assertRaises(PlanError) as caught:
            build_plan(
                _body(
                    origin={"name": "A", "lat": 41.0, "lng": -87.6},
                    destination={"name": "B", "lat": 41.018, "lng": -87.6},
                ),
                _short_route,
                open_meteo,
            )
        self.assertEqual(caught.exception.status, 503)
        self.assertEqual(
            str(caught.exception),
            "Weather service rate limit reached. Try again in a minute.",
        )
