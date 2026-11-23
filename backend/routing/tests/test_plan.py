import copy
import json
import math
import threading
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import requests
from urllib3.response import HTTPResponse

from routing.geo import (
    EARTH_MILES,
    color_segments,
    cumulative_miles,
    haversine_miles,
    place_checkpoints,
)
from routing.risk import classify, miles_by_level
import routing.services as services
from routing.services import PlanError, build_plan, open_meteo, parse_departure, routes_from_geojson


DEPARTURE = "2026-11-18T12:30:00+00:00"


def hours_from(start, count):
    times = []
    cursor = start
    for _ in range(count):
        times.append(cursor.strftime("%Y-%m-%dT%H:%M"))
        cursor += timedelta(hours=1)
    return times


def weather_factory(wind_for_lat, rain_mm=0.0, snow_cm=0.0, times=None):
    series = times or hours_from(datetime(2026, 11, 18, tzinfo=timezone.utc), 96)

    def fetch(_points, _start, _end):
        rows = []
        for lat, _lng in _points:
            count = len(series)
            rows.append(
                {
                    "time": series,
                    "wind_speed_10m": [wind_for_lat(lat)] * count,
                    "rain": [rain_mm] * count,
                    "snowfall": [snow_cm] * count,
                }
            )
        return rows

    return fetch


def two_routes(_origin, _destination, _load_lb=None):
    return [
        {"coords": [(10.0, 0.0), (10.0, 1.0)], "duration_seconds": 1000},
        {"coords": [(1.0, 0.0), (1.0, 1.0)], "duration_seconds": 5000},
    ]


def payload(**overrides):
    body = {
        "origin": {"name": "North yard", "lat": 10, "lng": 0},
        "destination": {"name": "South dock", "lat": 1, "lng": 1},
        "departure": DEPARTURE,
        "load_lb": 45000,
        "spacing_miles": 25,
    }
    body.update(overrides)
    return body


def _response(status, body):
    class Response:
        status_code = status

        def json(self):
            return body

    return Response()


class _OneShot:
    """Body that closes at EOF so urllib3's stream loop stops."""

    def __init__(self, data):
        self._data = data
        self.closed = False

    def read(self, amt=-1):
        if self.closed:
            return b""
        if amt is None or amt < 0:
            amt = len(self._data)
        chunk = self._data[:amt]
        self._data = self._data[amt:]
        if not self._data:
            self.closed = True
        return chunk

    def close(self):
        self.closed = True

    def isclosed(self):
        return self.closed


def _forecast_http(url):
    query = parse_qs(urlsplit(url).query)
    lats = query["latitude"][0].split(",")
    hours = hours_from(datetime(2026, 11, 18, tzinfo=timezone.utc), 96)
    count = len(hours)
    rows = []
    for _lat in lats:
        rows.append(
            {
                "hourly": {
                    "time": hours,
                    "wind_speed_10m": [5.0] * count,
                    "rain": [0.0] * count,
                    "showers": [0.0] * count,
                    "snowfall": [0.0] * count,
                }
            }
        )
    return HTTPResponse(
        body=_OneShot(json.dumps(rows).encode()),
        status=200,
        reason="OK",
        headers={"Content-Type": "application/json"},
        preload_content=False,
        decode_content=False,
    )


def _meridian(lat, lng, miles, steps):
    span = miles / (EARTH_MILES * math.pi / 180.0)
    return [(lat + span * index / steps, lng) for index in range(steps + 1)]


def _distance_to_meridian(point, lat0, lat1, lng):
    lat = point[0]
    if lat <= lat0:
        return haversine_miles(point, (lat0, lng))
    if lat >= lat1:
        return haversine_miles(point, (lat1, lng))
    return haversine_miles(point, (lat, lng))


def _coords_6000():
    corners = []
    lat, lng = 35.0, -102.0
    for index in range(15):
        corners.append((lat, lng))
        if index % 2 == 0:
            lng += 0.8
        else:
            lat += 0.7
    segments = len(corners) - 1
    steps_total = 5999
    base, extra = divmod(steps_total, segments)
    coords = [corners[0]]
    for index, (start, end) in enumerate(zip(corners, corners[1:])):
        steps = base + (1 if index < extra else 0)
        for step in range(1, steps + 1):
            fraction = step / steps
            coords.append(
                (
                    start[0] + fraction * (end[0] - start[0]),
                    start[1] + fraction * (end[1] - start[1]),
                )
            )
    return coords


class BuildPlanTest(unittest.TestCase):
    def setUp(self):
        super().setUp()
        from django.core.cache import cache

        cache.clear()
        patcher = patch(
            "routing.services._utcnow",
            return_value=datetime(2026, 11, 18, 11, 0, tzinfo=timezone.utc),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_no_travel_route_loses_and_heatmap_is_local(self):
        plan = build_plan(
            payload(),
            two_routes,
            weather_factory(lambda lat: 60.0 if lat > 5 else 10.0),
        )
        self.assertEqual(len(plan["routes"]), 2)
        self.assertEqual(plan["note"], "The routing service returned 2 routes.")
        self.assertTrue(plan["routes"][1]["recommended"])
        self.assertFalse(plan["routes"][0]["recommended"])
        self.assertGreater(plan["routes"][0]["miles"]["No Travel"], 0)
        self.assertEqual(plan["routes"][0]["checkpoints"][0]["level"], "No Travel")
        self.assertEqual(plan["routes"][1]["checkpoints"][0]["level"], "Low")
        self.assertEqual(plan["spacing_miles"], 25)
        self.assertGreaterEqual(len(plan["routes"][0]["checkpoints"]), 3)
        self.assertEqual(plan["routes"][0]["checkpoints"][0]["mile"], 0)
        self.assertTrue(plan["routes"][0]["segments"])
        self.assertTrue(plan["corridor"])
        self.assertTrue(all(len(point["risks"]) == 49 for point in plan["corridor"]))
        self.assertEqual(plan["forecast_start"], "2026-11-18T12:00:00+00:00")

    def test_heavy_load_raises_high_wind_to_severe(self):
        plan = build_plan(
            payload(load_lb=45000),
            two_routes,
            weather_factory(lambda lat: 40.0 if lat > 5 else 5.0),
        )
        windy = plan["routes"][0]["checkpoints"][0]
        self.assertEqual(windy["wind_level"], "Severe")
        self.assertEqual(windy["level"], "Severe")
        self.assertEqual(plan["routes"][1]["checkpoints"][0]["level"], "Low")
        self.assertTrue(plan["routes"][1]["recommended"])

    def test_load_at_the_cutoff_does_not_raise_wind(self):
        plan = build_plan(
            payload(load_lb=40000),
            two_routes,
            weather_factory(lambda lat: 40.0),
        )
        self.assertEqual(plan["routes"][0]["checkpoints"][0]["wind_level"], "High")

    def test_rain_conversion_uses_inches(self):
        plan = build_plan(
            payload(load_lb=1000),
            two_routes,
            weather_factory(lambda _lat: 0.0, rain_mm=25.4),
        )
        point = plan["routes"][0]["checkpoints"][0]
        self.assertAlmostEqual(point["rain_in_hr"], 1.0, places=3)
        self.assertEqual(point["rain_level"], "Severe")

    def test_tighter_spacing_adds_checkpoints(self):
        wide = build_plan(payload(spacing_miles=50), two_routes, weather_factory(lambda _lat: 5.0))
        tight = build_plan(payload(spacing_miles=10), two_routes, weather_factory(lambda _lat: 5.0))
        self.assertGreater(
            len(tight["routes"][0]["checkpoints"]),
            len(wide["routes"][0]["checkpoints"]),
        )

    def test_forecast_gap_is_an_error(self):
        def empty_forecast(points, _start, _end):
            return [
                {"time": ["2026-11-01T00:00"], "wind_speed_10m": [1], "rain": [0], "snowfall": [0]}
                for _point in points
            ]

        with self.assertRaises(PlanError) as caught:
            build_plan(payload(), two_routes, empty_forecast)
        self.assertIn("forecast", str(caught.exception).lower())

    def test_validation(self):
        with self.assertRaises(PlanError):
            build_plan(payload(load_lb=0), None, None)
        with self.assertRaises(PlanError):
            build_plan(payload(spacing_miles=15), None, None)
        with self.assertRaises(PlanError):
            build_plan(payload(origin={"name": "Yard", "lat": 1, "lng": 1}, destination={"name": "Dock", "lat": 1, "lng": 1}), None, None)
        with self.assertRaises(PlanError):
            build_plan(payload(departure="tomorrow"), None, None)

    def test_short_north_south_corridor_has_no_holes(self):
        coords = _meridian(34.0, -90.0, 300, 300)
        requested = []

        def fetch(points, start, end):
            requested.extend(points)
            return weather_factory(lambda _lat: 5.0)(points, start, end)

        def routes(_origin, _destination, _load_lb=None):
            return [{"coords": coords, "duration_seconds": 5 * 3600}]

        plan = build_plan(
            payload(
                origin={"name": "South yard", "lat": 34.0, "lng": -90.0},
                destination={"name": "North dock", "lat": coords[-1][0], "lng": -90.0},
                spacing_miles=25,
            ),
            routes,
            fetch,
        )
        self.assertEqual(plan["corridor_cell_deg"], 0.2)
        self.assertLessEqual(len(set(requested)), 350)
        present = {(point["lat"], point["lng"]) for point in plan["corridor"]}
        lat0, lat1 = coords[0][0], coords[-1][0]
        deg = 0.2
        missing = []
        i0 = math.floor((lat0 - 1) / deg) - 1
        i1 = math.floor((lat1 + 1) / deg) + 2
        j0 = math.floor((-91) / deg) - 1
        j1 = math.floor((-89) / deg) + 2
        for i in range(i0, i1):
            for j in range(j0, j1):
                center = ((i + 0.5) * deg, (j + 0.5) * deg)
                if _distance_to_meridian(center, lat0, lat1, -90.0) > 20:
                    continue
                rounded = (round(center[0], 5), round(center[1], 5))
                if rounded not in present:
                    missing.append(rounded)
        self.assertEqual(missing, [])

    @patch("routing.services.session.get")
    def test_long_trip_uses_coarser_cells_under_the_weather_cap(self, get):
        sent = []
        hours = hours_from(datetime(2026, 11, 18, tzinfo=timezone.utc), 96)

        def forecast(url, params=None, timeout=None):
            self.assertEqual(timeout, (5, 20))
            lats = [part for part in str(params["latitude"]).split(",") if part]
            lngs = [part for part in str(params["longitude"]).split(",") if part]
            sent.extend(zip(lats, lngs))
            count = len(hours)
            rows = []
            for _lat in lats:
                rows.append(
                    {
                        "hourly": {
                            "time": hours,
                            "wind_speed_10m": [8.0] * count,
                            "rain": [0.0] * count,
                            "showers": [0.0] * count,
                            "snowfall": [0.0] * count,
                        }
                    }
                )
            return _response(200, rows[0] if len(rows) == 1 else rows)

        get.side_effect = forecast

        def routes(_origin, _destination, _load_lb=None):
            return [
                {"coords": _meridian(32.0, lng, 900, 80), "duration_seconds": 18 * 3600}
                for lng in (-100.0, -98.0, -96.0)
            ]

        plan = build_plan(
            payload(
                origin={"name": "South yard", "lat": 32.0, "lng": -100.0},
                destination={"name": "North dock", "lat": 45.0, "lng": -96.0},
                spacing_miles=25,
                load_lb=42000,
            ),
            routes,
            open_meteo,
        )
        self.assertEqual(len(plan["routes"]), 3)
        self.assertIn(plan["corridor_cell_deg"], (0.25, 0.3))
        self.assertLessEqual(len(set(sent)), 350)
        self.assertTrue(plan["corridor"])
        for route in plan["routes"]:
            self.assertTrue(route["checkpoints"])
            self.assertIsNotNone(route["checkpoints"][0]["wind_mph"])

    def test_simplified_display_keeps_full_route_miles(self):
        coords = _coords_6000()
        self.assertEqual(len(coords), 6000)
        duration = 10 * 3600

        def routes(_origin, _destination, _load_lb=None):
            return [{"coords": coords, "duration_seconds": duration}]

        plan = build_plan(
            payload(
                origin={"name": "West yard", "lat": 35.0, "lng": -102.0},
                destination={"name": "East dock", "lat": 40.0, "lng": -96.0},
                spacing_miles=50,
                load_lb=1000,
            ),
            routes,
            weather_factory(lambda _lat: 5.0),
        )
        positions = sum(len(segment["positions"]) for segment in plan["routes"][0]["segments"])
        self.assertLess(positions, 1500)
        full = cumulative_miles(coords)[-1]
        self.assertAlmostEqual(plan["routes"][0]["distance_miles"], full, delta=0.01)
        departure = datetime(2026, 11, 18, 12, 30, tzinfo=timezone.utc)
        placed = place_checkpoints(coords, 50, departure, duration)
        for point in placed:
            point.update(classify(5.0, 0.0, 0.0, 1000))
        expected = miles_by_level(placed)
        for name, amount in expected.items():
            self.assertAlmostEqual(plan["routes"][0]["miles"][name], amount, delta=0.01)
        before_plan = copy.deepcopy(plan)
        before_plan["routes"][0]["segments"] = [
            {
                "level": segment["level"],
                "positions": [[round(lat, 5), round(lng, 5)] for lat, lng in segment["positions"]],
            }
            for segment in color_segments(coords, placed)
        ]
        before = len(json.dumps(before_plan))
        after = len(json.dumps(plan))
        self.assertLess(after, before)

    def test_open_meteo_connect_timeout_is_retried_by_the_session(self):
        gets = {"n": 0}
        attempts = {"n": 0}
        lock = threading.Lock()
        original_get = services.session.get

        def counting_get(*args, **kwargs):
            with lock:
                gets["n"] += 1
            self.assertEqual(kwargs.get("timeout"), (5, 20))
            self.assertIn("open-meteo.com", args[0])
            return original_get(*args, **kwargs)

        def flaky_make_request(_pool, _conn, _method, url, *_args, **_kwargs):
            with lock:
                attempts["n"] += 1
                first = attempts["n"] == 1
            if first:
                raise requests.ConnectTimeout("connect timed out")
            return _forecast_http(url)

        # Env proxies would tunnel before _make_request and never hit the flaky transport.
        previous_trust = services.session.trust_env
        services.session.trust_env = False
        try:
            with patch.object(services.session, "get", counting_get), patch(
                "urllib3.connectionpool.HTTPConnectionPool._make_request",
                flaky_make_request,
            ):
                plan = build_plan(payload(), two_routes, open_meteo)
        finally:
            services.session.trust_env = previous_trust

        self.assertGreaterEqual(attempts["n"], 2)
        self.assertEqual(attempts["n"], gets["n"] + 1)
        self.assertEqual(len(plan["routes"]), 2)
        self.assertEqual(plan["forecast_start"], "2026-11-18T12:00:00+00:00")

    def test_failed_weather_chunk_leaves_the_earlier_chunk_cached(self):
        from django.core.cache import cache

        points = [(float(index), -100.0) for index in range(-40, 61)]
        self.assertEqual(len(points), 101)
        start = datetime(2026, 11, 18, tzinfo=timezone.utc)
        end = start + timedelta(days=2)
        hours = hours_from(start, 96)

        def forecast(url, params=None, timeout=None):
            self.assertEqual(timeout, (5, 20))
            lats = [float(part) for part in str(params["latitude"]).split(",") if part]
            if max(lats) >= 60:
                raise requests.ConnectTimeout("connect timed out")
            count = len(hours)
            rows = []
            for _lat in lats:
                rows.append(
                    {
                        "hourly": {
                            "time": hours,
                            "wind_speed_10m": [5.0] * count,
                            "rain": [0.0] * count,
                            "showers": [1.0] * count,
                            "snowfall": [0.0] * count,
                        }
                    }
                )
            return _response(200, rows[0] if len(rows) == 1 else rows)

        with patch("routing.services.session.get", side_effect=forecast):
            with self.assertRaises(requests.ConnectTimeout):
                open_meteo(points, start, end)

        start_date = start.date().isoformat()
        end_date = end.date().isoformat()
        for lat, lng in points[:100]:
            key = f"wx:{lat:.2f}:{lng:.2f}:{start_date}:{end_date}"
            cached = cache.get(key)
            self.assertIsNotNone(cached)
            self.assertEqual(cached["rain"][0], 1.0)
        lat, lng = points[100]
        missed = f"wx:{lat:.2f}:{lng:.2f}:{start_date}:{end_date}"
        self.assertIsNone(cache.get(missed))


class GeoJsonTest(unittest.TestCase):
    def test_swaps_lng_lat_and_keeps_three(self):
        features = []
        for index in range(4):
            features.append(
                {
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[-87.6, 41.8 + index], [-87.5, 41.9 + index]],
                    },
                    "properties": {"summary": {"duration": 600 + index}},
                }
            )
        features.append(
            {
                "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
                "properties": {"summary": {}},
            }
        )
        routes = routes_from_geojson({"features": features})
        self.assertEqual(len(routes), 3)
        self.assertEqual(routes[0]["coords"][0], (41.8, -87.6))
        self.assertEqual(routes[0]["duration_seconds"], 600)

    def test_multiline(self):
        payload = {
            "features": [
                {
                    "geometry": {
                        "type": "MultiLineString",
                        "coordinates": [[[0, 1], [1, 1]], [[1, 1], [2, 1]]],
                    },
                    "properties": {"summary": {"duration": 50}},
                }
            ]
        }
        routes = routes_from_geojson(payload)
        self.assertEqual(len(routes[0]["coords"]), 4)


WINDOW_ERROR = "Departure must be between now and 13 days ahead (forecast limit)."


class ParseDepartureWindowTest(unittest.TestCase):
    def test_fourteen_days_ahead_is_rejected(self):
        now = datetime(2026, 11, 18, 11, 0, tzinfo=timezone.utc)
        with self.assertRaises(PlanError) as caught:
            parse_departure((now + timedelta(days=14)).isoformat(), now=now)
        self.assertEqual(str(caught.exception), WINDOW_ERROR)
        self.assertEqual(caught.exception.status, 400)

    def test_two_hours_ago_is_rejected(self):
        now = datetime(2026, 11, 18, 11, 0, tzinfo=timezone.utc)
        with self.assertRaises(PlanError) as caught:
            parse_departure((now - timedelta(hours=2)).isoformat(), now=now)
        self.assertEqual(str(caught.exception), WINDOW_ERROR)
        self.assertEqual(caught.exception.status, 400)

    def test_two_hours_ahead_is_accepted(self):
        now = datetime(2026, 11, 18, 11, 0, tzinfo=timezone.utc)
        parsed = parse_departure((now + timedelta(hours=2)).isoformat(), now=now)
        self.assertEqual(parsed, now + timedelta(hours=2))
        self.assertEqual(parsed.tzinfo, timezone.utc)


if __name__ == "__main__":
    unittest.main()
