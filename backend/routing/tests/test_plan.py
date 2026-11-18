import unittest
from datetime import datetime, timedelta, timezone

from routing.services import PlanError, build_plan, routes_from_geojson


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


def two_routes(_origin, _destination):
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


class BuildPlanTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
