import os
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import requests

from routing.geo import cumulative_miles, haversine_miles, place_checkpoints
from routing.services import (
    KM_PER_MILE,
    TARE_LB,
    LB_TO_TONNES,
    ORS_MAX_WEIGHT_TONNES,
    build_plan,
    open_meteo,
    ors_routes,
    via_points,
    weight_tonnes,
)


def _response(status, payload):
    class Response:
        status_code = status

        def json(self):
            return payload

    return Response()


def _feature(coords_lng_lat, duration, steps=None):
    properties = {"summary": {"duration": duration}}
    if steps is not None:
        properties["segments"] = [{"steps": steps}]
    return {
        "geometry": {"type": "LineString", "coordinates": coords_lng_lat},
        "properties": properties,
    }


def _hours():
    times = []
    cursor = datetime(2026, 11, 18, tzinfo=timezone.utc)
    for _ in range(96):
        times.append(cursor.strftime("%Y-%m-%dT%H:%M"))
        cursor += timedelta(hours=1)
    return times


class ViaFallbackTest(unittest.TestCase):
    @patch.dict(os.environ, {"ORS_API_KEY": "test-key"})
    @patch("routing.services.requests.post")
    def test_long_trip_adds_via_routes_until_three(self, post):
        # About 144 km south. Real ORS returns 400 when this distance asks for alternatives.
        origin = {"name": "North", "lat": 41.88, "lng": -87.63}
        destination = {"name": "South", "lat": 40.58, "lng": -87.63}
        direct = [[-87.63, 41.88], [-87.63, 41.2], [-87.63, 40.58]]

        def post_json(url, json=None, headers=None, timeout=None):
            body = json or {}
            if "alternative_routes" in body:
                return _response(400, {"error": {"message": "Request parameters exceed the server configuration."}})
            coords = body.get("coordinates") or []
            shift = 0.0 if len(coords) < 3 else coords[1][0] - coords[0][0]
            line = [[lng + shift, lat] for lng, lat in direct]
            return _response(200, {"features": [_feature(line, 4000 + abs(shift) * 100)]})

        post.side_effect = post_json
        routes = ors_routes(origin, destination, load_lb=42000)
        self.assertEqual(len(routes), 3)
        self.assertTrue(all(route["profile"] == "driving-hgv" for route in routes))
        bodies = [call.kwargs["json"] for call in post.call_args_list]
        self.assertTrue(bodies)
        self.assertTrue(all("alternative_routes" not in body for body in bodies))
        self.assertTrue(all("/driving-hgv/" in call.args[0] for call in post.call_args_list))
        self.assertEqual(len(bodies[0]["coordinates"]), 2)
        self.assertEqual(bodies[0]["radiuses"], [-1, -1])
        self.assertEqual(bodies[0]["options"]["profile_params"]["restrictions"]["weight"], weight_tonnes(42000))
        self.assertEqual(weight_tonnes(42000), round((42000 + TARE_LB) * LB_TO_TONNES, 2))
        self.assertEqual(weight_tonnes(10**9), ORS_MAX_WEIGHT_TONNES)
        via_bodies = [body for body in bodies if len(body["coordinates"]) == 3]
        self.assertGreaterEqual(len(via_bodies), 2)
        self.assertEqual(via_bodies[0]["radiuses"], [-1, -1, -1])
        self.assertEqual(len({round(route["coords"][1][1], 2) for route in routes}), 3)


def _shifted_line(coordinates):
    start = coordinates[0]
    end = coordinates[-1]
    shift = 0.0 if len(coordinates) < 3 else coordinates[1][0] - start[0]
    middle = [start[0], (start[1] + end[1]) / 2]
    line = [[lng + shift, lat] for lng, lat in (start, middle, end)]
    coords = [(lat, lng) for lng, lat in line]
    return line, coords, 5000.0 + abs(shift) * 100


def _is_via(coordinates, via):
    return (
        len(coordinates) == 3
        and abs(coordinates[1][0] - via["lng"]) < 1e-9
        and abs(coordinates[1][1] - via["lat"]) < 1e-9
    )


_NORTH = {"name": "North", "lat": 41.88, "lng": -87.63}
_SOUTH = {"name": "South", "lat": 40.58, "lng": -87.63}


def _via_path(via=None):
    path = [[_NORTH["lng"], _NORTH["lat"]], [_SOUTH["lng"], _SOUTH["lat"]]]
    if via is not None:
        path.insert(1, [via["lng"], via["lat"]])
    return path


class ConcurrentViaTest(unittest.TestCase):
    @patch.dict(os.environ, {"ORS_API_KEY": "test-key"})
    @patch("routing.services.requests.post")
    def test_via_requests_overlap_and_keep_via_order(self, post):
        vias = via_points(_NORTH, _SOUTH)
        fast = vias[2]
        finished = []
        lock = threading.Lock()

        def post_json(url, json=None, headers=None, timeout=None):
            body = json or {}
            coords = body["coordinates"]
            self.assertNotIn("alternative_routes", body)
            self.assertEqual(timeout, (5, 20))
            if len(coords) == 3:
                time.sleep(0.01 if _is_via(coords, fast) else 0.2)
                with lock:
                    finished.append(coords[1][0])
            line, _route_coords, duration = _shifted_line(coords)
            return _response(200, {"features": [_feature(line, duration)]})

        post.side_effect = post_json
        started = time.perf_counter()
        routes = ors_routes(_NORTH, _SOUTH, load_lb=42000)
        elapsed = time.perf_counter() - started
        print(f"MEASURE via_elapsed={elapsed:.4f}", flush=True)
        self.assertLess(elapsed, 0.6)
        self.assertEqual(len(routes), 3)
        self.assertEqual(post.call_count, 7)
        self.assertEqual(finished[0], fast["lng"])
        expected = [_shifted_line(_via_path(via))[1] for via in (None, vias[0], vias[1])]
        self.assertEqual([route["coords"] for route in routes], expected)

    @patch.dict(os.environ, {"ORS_API_KEY": "test-key"})
    @patch("routing.services.requests.post")
    def test_timed_out_via_is_skipped(self, post):
        vias = via_points(_NORTH, _SOUTH)
        missed = vias[1]

        def post_json(url, json=None, headers=None, timeout=None):
            coords = (json or {})["coordinates"]
            if _is_via(coords, missed):
                raise requests.Timeout("timed out")
            line, _route_coords, duration = _shifted_line(coords)
            return _response(200, {"features": [_feature(line, duration)]})

        post.side_effect = post_json
        routes = ors_routes(_NORTH, _SOUTH, load_lb=42000)
        self.assertEqual(len(routes), 3)
        expected = [_shifted_line(_via_path(via))[1] for via in (None, vias[0], vias[2])]
        self.assertEqual([route["coords"] for route in routes], expected)
        missed_coords = _shifted_line(_via_path(missed))[1]
        self.assertTrue(all(route["coords"] != missed_coords for route in routes))


class ConcurrentWeatherTest(unittest.TestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.clear()

    @patch("routing.services.requests.get")
    def test_chunks_overlap_and_stay_in_point_order(self, get):
        points = [(round(30 + i / 100, 5), round(-100 - i / 100, 5)) for i in range(250)]

        def forecast(url, params=None, timeout=None):
            self.assertIn("open-meteo.com", url)
            self.assertEqual(timeout, (5, 20))
            time.sleep(0.2)
            lats = [float(part) for part in str(params["latitude"]).split(",") if part]
            times = _hours()
            count = len(times)
            rows = []
            for lat in lats:
                rows.append(
                    {
                        "hourly": {
                            "time": times,
                            "wind_speed_10m": [lat] * count,
                            "rain": [0.0] * count,
                            "showers": [0.0] * count,
                            "snowfall": [0.0] * count,
                        }
                    }
                )
            payload = rows[0] if len(rows) == 1 else rows
            return _response(200, payload)

        get.side_effect = forecast
        start = datetime(2026, 11, 18, tzinfo=timezone.utc)
        started = time.perf_counter()
        results = open_meteo(points, start, start + timedelta(days=2))
        elapsed = time.perf_counter() - started
        print(f"MEASURE weather_elapsed={elapsed:.4f}", flush=True)
        self.assertLess(elapsed, 0.45)
        self.assertEqual(len(results), len(points))
        self.assertEqual(get.call_count, 3)
        self.assertTrue(all("open-meteo.com" in call.args[0] for call in get.call_args_list))
        sizes = sorted(len(call.kwargs["params"]["latitude"].split(",")) for call in get.call_args_list)
        self.assertEqual(sizes, [50, 100, 100])
        for index, (lat, _lng) in enumerate(points):
            self.assertEqual(results[index]["wind_speed_10m"][0], float(f"{lat:.5f}"))


class StepEtaTest(unittest.TestCase):
    @patch.dict(os.environ, {"ORS_API_KEY": "test-key"})
    @patch("routing.services.requests.post")
    def test_checkpoint_eta_follows_step_duration(self, post):
        origin = {"name": "A", "lat": 41.88, "lng": -87.63}
        destination = {"name": "B", "lat": 41.90, "lng": -87.63}
        coords = [[-87.63, 41.88], [-87.63, 41.89], [-87.63, 41.90]]
        post.return_value = _response(
            200,
            {
                "features": [
                    _feature(
                        coords,
                        1100,
                        steps=[
                            {"duration": 100, "way_points": [0, 1]},
                            {"duration": 1000, "way_points": [1, 2]},
                        ],
                    )
                ]
            },
        )
        routes = ors_routes(origin, destination, load_lb=1000)
        self.assertEqual(post.call_count, 1)
        path = routes[0]["coords"]
        self.assertAlmostEqual(routes[0]["time_at_coords"][1], 100, places=3)
        miles = cumulative_miles(path)
        departure = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
        placed = place_checkpoints(
            path,
            miles[1],
            departure,
            routes[0]["duration_seconds"],
            routes[0]["time_at_coords"],
        )
        middle = placed[1]
        elapsed = (middle["eta"] - departure).total_seconds()
        self.assertAlmostEqual(elapsed, 100, delta=1)
        self.assertGreater(abs(elapsed - 550), 100)


class RecommendationReasonTest(unittest.TestCase):
    def setUp(self):
        super().setUp()
        patcher = patch(
            "routing.services._utcnow",
            return_value=datetime(2026, 11, 18, 11, 0, tzinfo=timezone.utc),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    @patch.dict(os.environ, {"ORS_API_KEY": "test-key"})
    @patch("routing.services.requests.get")
    @patch("routing.services.requests.post")
    def test_reason_names_the_sort_key_that_won(self, post, get):
        windy = [[-87.6, 42.0], [-87.6, 42.02]]
        calm = [[-87.6, 41.0], [-87.6, 41.02]]
        post.return_value = _response(
            200,
            {"features": [_feature(windy, 1000), _feature(calm, 5000)]},
        )

        def forecast(url, params=None, timeout=None):
            times = _hours()
            rows = []
            for lat in str(params["latitude"]).split(","):
                wind = 60.0 if float(lat) > 41.5 else 10.0
                count = len(times)
                rows.append(
                    {
                        "hourly": {
                            "time": times,
                            "wind_speed_10m": [wind] * count,
                            "rain": [0.0] * count,
                            "snowfall": [0.0] * count,
                        }
                    }
                )
            payload = rows[0] if len(rows) == 1 else rows
            return _response(200, payload)

        get.side_effect = forecast
        plan = build_plan(
            {
                "origin": {"name": "Windy", "lat": 42.0, "lng": -87.6},
                "destination": {"name": "Calm", "lat": 41.0, "lng": -87.6},
                "departure": "2026-11-18T12:30:00+00:00",
                "load_lb": 42000,
                "spacing_miles": 25,
            },
            ors_routes,
            open_meteo,
        )
        winner = next(route for route in plan["routes"] if route["recommended"])
        self.assertEqual(winner["id"], 1)
        self.assertRegex(
            winner["recommendation_reason"],
            r"^Fewest No Travel miles: 0\.0 vs [0-9]+\.[0-9] mi$",
        )
        shown = float(winner["recommendation_reason"].split(" vs ")[1].split(" ")[0])
        self.assertAlmostEqual(shown, plan["routes"][0]["miles"]["No Travel"], delta=0.05)
        self.assertIsNone(plan["routes"][0]["recommendation_reason"])


class ViaOffsetTest(unittest.TestCase):
    def _km_from_midpoint(self, origin, destination):
        points = via_points(origin, destination)
        mid = (
            (origin["lat"] + destination["lat"]) / 2,
            (origin["lng"] + destination["lng"]) / 2,
        )
        return points, [
            haversine_miles(mid, (point["lat"], point["lng"])) * KM_PER_MILE for point in points
        ]

    def test_offsets_follow_percentages_and_the_cap(self):
        origin = {"lat": 40.0, "lng": -90.0}
        near = {"lat": 40.0 + (200 / 110.574), "lng": -90.0}
        points, distances = self._km_from_midpoint(origin, near)
        self.assertEqual(len(points), 6)
        for index, fraction in enumerate((0.08, 0.15, 0.25)):
            expected = 200 * fraction
            self.assertAlmostEqual(distances[index * 2], expected, delta=1.5)
            self.assertAlmostEqual(distances[index * 2 + 1], expected, delta=1.5)
        self.assertLess(points[0]["lng"], origin["lng"])
        self.assertGreater(points[1]["lng"], origin["lng"])

        far = {"lat": 40.0 + (800 / 110.574), "lng": -90.0}
        _capped, capped_km = self._km_from_midpoint(origin, far)
        self.assertAlmostEqual(capped_km[0], 64, delta=2)
        self.assertAlmostEqual(capped_km[1], 64, delta=2)
        self.assertAlmostEqual(capped_km[2], 120, delta=2)
        self.assertAlmostEqual(capped_km[3], 120, delta=2)
        self.assertAlmostEqual(capped_km[4], 120, delta=2)
        self.assertAlmostEqual(capped_km[5], 120, delta=2)


_MISSING = object()


def _one_route(_origin, _destination, _load_lb=None):
    return [{"coords": [(41.0, -87.6), (41.02, -87.6)], "duration_seconds": 600}]


class ShowerRainTest(unittest.TestCase):
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

    def _run(self, get, rain, showers=_MISSING):
        times = _hours()
        count = len(times)

        def as_list(value):
            if isinstance(value, list):
                return value
            return [value] * count

        def forecast(url, params=None, timeout=None):
            self.assertEqual(params["hourly"], "wind_speed_10m,rain,showers,snowfall")
            hourly = {
                "time": times,
                "wind_speed_10m": [0.0] * count,
                "snowfall": [0.0] * count,
            }
            if rain is not _MISSING:
                hourly["rain"] = as_list(rain)
            if showers is not _MISSING:
                hourly["showers"] = as_list(showers)
            lats = [part for part in str(params["latitude"]).split(",") if part != ""]
            rows = [{"hourly": hourly} for _lat in lats]
            payload = rows[0] if len(rows) == 1 else rows
            return _response(200, payload)

        get.side_effect = forecast
        start = datetime(2026, 11, 18, tzinfo=timezone.utc)
        series = open_meteo([(41.0, -87.6)], start, start + timedelta(days=2))
        plan = build_plan(
            {
                "origin": {"name": "A", "lat": 41.0, "lng": -87.6},
                "destination": {"name": "B", "lat": 41.02, "lng": -87.6},
                "departure": "2026-11-18T12:30:00+00:00",
                "load_lb": 1000,
                "spacing_miles": 25,
            },
            _one_route,
            open_meteo,
        )
        return series[0]["rain"], plan["routes"][0]["checkpoints"][0]

    @patch("routing.services.requests.get")
    def test_showers_raise_steady_rain_to_severe(self, get):
        stored, point = self._run(get, 0.0, 15.0)
        self.assertEqual(stored, [15.0] * len(_hours()))
        self.assertAlmostEqual(point["rain_in_hr"], 15 / 25.4, places=3)
        self.assertEqual(point["rain_level"], "Severe")

    @patch("routing.services.requests.get")
    def test_none_showers_leave_moderate_rain(self, get):
        stored, point = self._run(get, 3.0, None)
        self.assertEqual(stored, [3.0] * len(_hours()))
        self.assertAlmostEqual(point["rain_in_hr"], 3 / 25.4, places=3)
        self.assertEqual(point["rain_level"], "Moderate")

    @patch("routing.services.requests.get")
    def test_missing_rain_and_showers_are_not_safe(self, get):
        stored, point = self._run(get, None, None)
        self.assertEqual(stored, [None] * len(_hours()))
        self.assertIsNone(point["rain_in_hr"])
        self.assertIsNone(point["rain_level"])

    @patch("routing.services.requests.get")
    def test_missing_showers_key_leaves_rain_unchanged(self, get):
        rain = [None] + [0.0] * (len(_hours()) - 1)
        stored, point = self._run(get, rain)
        self.assertEqual(stored, rain)
        self.assertIsNone(stored[0])
        self.assertAlmostEqual(point["rain_in_hr"], 0.0, places=3)
        self.assertEqual(point["rain_level"], "Low")

    @patch("routing.services.requests.get")
    def test_short_showers_count_as_zero(self, get):
        payloads = [
            {
                "hourly": {
                    "time": ["2026-11-18T00:00", "2026-11-18T01:00", "2026-11-18T02:00"],
                    "wind_speed_10m": [0.0, 0.0, 0.0],
                    "rain": [None, 0.0, 4.0],
                    "showers": [15.0, None],
                    "snowfall": [0.0, 0.0, 0.0],
                }
            },
            {
                "hourly": {
                    "time": ["2026-11-18T00:00", "2026-11-18T01:00"],
                    "wind_speed_10m": [0.0, 0.0],
                    "showers": [None, 15.0],
                    "snowfall": [0.0, 0.0],
                }
            },
            {"hourly": {"time": [], "wind_speed_10m": [], "snowfall": []}},
        ]

        def forecast(url, params=None, timeout=None):
            return _response(200, payloads.pop(0))

        get.side_effect = forecast
        from django.core.cache import cache

        start = datetime(2026, 11, 18, tzinfo=timezone.utc)
        end = start + timedelta(days=1)
        point = [(41.0, -87.6)]
        self.assertEqual(open_meteo(point, start, end)[0]["rain"], [15.0, 0.0, 4.0])
        cache.clear()
        self.assertEqual(open_meteo(point, start, end)[0]["rain"], [None, 15.0])
        cache.clear()
        self.assertEqual(open_meteo(point, start, end)[0]["rain"], [])


if __name__ == "__main__":
    unittest.main()
