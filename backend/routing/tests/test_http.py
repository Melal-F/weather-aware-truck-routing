import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from routing.geo import cumulative_miles, place_checkpoints
from routing.services import LB_TO_TONNES, build_plan, open_meteo, ors_routes


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
        # About 144 km south, so the direct alternative is not enough on its own.
        origin = {"name": "North", "lat": 41.88, "lng": -87.63}
        destination = {"name": "South", "lat": 40.58, "lng": -87.63}
        direct = [[-87.63, 41.88], [-87.63, 41.2], [-87.63, 40.58]]

        def post_json(url, json=None, headers=None, timeout=None):
            shift = 0.0 if len(post.call_args_list) == 1 else 0.5 * len(post.call_args_list)
            line = [[lng - shift, lat] for lng, lat in direct]
            return _response(200, {"features": [_feature(line, 5000 + shift)]})

        post.side_effect = post_json
        routes = ors_routes(origin, destination, load_lb=42000)
        self.assertEqual(len(routes), 3)
        bodies = [call.kwargs["json"] for call in post.call_args_list]
        self.assertEqual(len(bodies[0]["coordinates"]), 2)
        self.assertIn("alternative_routes", bodies[0])
        self.assertEqual(
            bodies[0]["options"]["profile_params"]["restrictions"]["weight"],
            round(42000 * LB_TO_TONNES, 2),
        )
        via_bodies = [body for body in bodies[1:] if len(body["coordinates"]) == 3]
        self.assertGreaterEqual(len(via_bodies), 2)
        self.assertNotIn("alternative_routes", via_bodies[0])
        self.assertEqual(len({round(route["coords"][1][1], 2) for route in routes}), 3)


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
        self.assertEqual(winner["recommendation_reason"], "Recommended for the fewest miles that are No Travel.")
        self.assertIsNone(plan["routes"][0]["recommendation_reason"])


if __name__ == "__main__":
    unittest.main()
