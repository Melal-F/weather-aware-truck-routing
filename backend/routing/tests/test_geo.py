import unittest
from datetime import datetime, timedelta, timezone

from routing.geo import (
    color_segments,
    corridor_samples,
    cumulative_miles,
    haversine_miles,
    place_checkpoints,
    sample_miles,
)


class SampleMilesTest(unittest.TestCase):
    def test_includes_origin_destination_and_a_short_tail(self):
        marks = sample_miles(40, 25)
        self.assertEqual(marks[0], 0)
        self.assertEqual(marks[-1], 40)
        self.assertEqual(marks, [0, 25, 40])

    def test_exact_multiple_does_not_duplicate_the_end(self):
        self.assertEqual(sample_miles(50, 25), [0, 25, 50])


class PlaceCheckpointsTest(unittest.TestCase):
    def test_equator_spacing_and_eta(self):
        # One degree of longitude at the equator is about 69.17 miles.
        coords = [(0.0, 0.0), (0.0, 1.0)]
        total = cumulative_miles(coords)[-1]
        self.assertAlmostEqual(total, haversine_miles(coords[0], coords[1]))
        self.assertGreater(total, 69)
        self.assertLess(total, 70)

        departure = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
        placed = place_checkpoints(coords, 25, departure, duration_seconds=7200)
        self.assertEqual(placed[0]["mile"], 0)
        self.assertEqual(placed[-1]["mile"], total)
        self.assertGreaterEqual(len(placed), 3)
        self.assertAlmostEqual(placed[-1]["mile"] - placed[-2]["mile"], total % 25, places=4)
        self.assertEqual(placed[0]["eta"], departure)
        self.assertEqual(placed[-1]["eta"], departure + timedelta(seconds=7200))
        halfway = next(point for point in placed if point["mile"] == 25)
        self.assertEqual(
            halfway["eta"],
            departure + timedelta(seconds=7200 * (25 / total)),
        )

    def test_color_follows_the_checkpoint_that_opens_the_stretch(self):
        coords = [(0.0, 0.0), (0.0, 1.0)]
        total = cumulative_miles(coords)[-1]
        checkpoints = [
            {"mile": 0, "lat": 0, "lng": 0, "level": "Low"},
            {"mile": 25, "lat": 0, "lng": 0.3, "level": "High"},
            {"mile": total, "lat": 0, "lng": 1, "level": "Severe"},
        ]
        segments = color_segments(coords, checkpoints)
        self.assertEqual(segments[0]["level"], "Low")
        self.assertEqual(segments[-1]["level"], "High")
        self.assertNotIn("Severe", [segment["level"] for segment in segments])

    def test_corridor_cap(self):
        coords = [(0.0, mile / 69.0) for mile in range(0, 400)]
        sampled = corridor_samples([coords, coords], spacing_miles=10, cap=30)
        self.assertEqual(len(sampled), 30)

    def test_north_south_route_offsets_side_points(self):
        sampled = corridor_samples([[(40.0, -90.0), (41.0, -90.0)]], spacing_miles=30)
        centers = [point for point in sampled if abs(point[1] + 90) < 1e-6]
        self.assertGreaterEqual(len(centers), 2)
        middle = min(centers, key=lambda point: abs(point[0] - 40.5))
        sides = [
            point
            for point in sampled
            if abs(point[0] - middle[0]) < 0.02 and abs(point[1] + 90) > 1e-4
        ]
        self.assertEqual(len(sides), 4)
        for point in sides:
            self.assertAlmostEqual(point[0], middle[0], delta=0.01)
        distances = sorted(haversine_miles(middle, point) for point in sides)
        self.assertAlmostEqual(distances[0], 10, delta=0.8)
        self.assertAlmostEqual(distances[1], 10, delta=0.8)
        self.assertAlmostEqual(distances[2], 20, delta=0.8)
        self.assertAlmostEqual(distances[3], 20, delta=0.8)
        self.assertTrue(any(point[1] < -90 for point in sides))
        self.assertTrue(any(point[1] > -90 for point in sides))


if __name__ == "__main__":
    unittest.main()
