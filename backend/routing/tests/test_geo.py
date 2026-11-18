import math
import unittest
from datetime import datetime, timedelta, timezone

from routing.geo import (
    EARTH_MILES,
    _segment_heading,
    _side_point,
    color_segments,
    corridor_samples,
    cumulative_miles,
    haversine_miles,
    place_checkpoints,
    point_at_mile,
    sample_miles,
)
from routing.risk import LEVELS, miles_by_level


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
        self.assertEqual(segments[0]["level"], "High")
        self.assertEqual(segments[-1]["level"], "Severe")
        self.assertEqual({segment["level"] for segment in segments}, {"High", "Severe"})

    def test_color_lengths_match_miles_by_the_worse_end(self):
        def at(mile):
            return (0.0, math.degrees(mile / EARTH_MILES))

        coords = [at(0), at(25), at(40)]
        marks = cumulative_miles(coords)
        self.assertAlmostEqual(marks[1], 25, delta=0.01)
        self.assertAlmostEqual(marks[2], 40, delta=0.01)
        checkpoints = [
            {"mile": 0, "lat": 0.0, "lng": 0.0, "level": "Low"},
            {"mile": 25, "lat": coords[1][0], "lng": coords[1][1], "level": "Low"},
            {"mile": 40, "lat": coords[2][0], "lng": coords[2][1], "level": "Severe"},
        ]
        segments = color_segments(coords, checkpoints)
        self.assertEqual(segments[-1]["level"], "Severe")

        colored = {name: 0.0 for name in LEVELS}
        for segment in segments:
            positions = segment["positions"]
            length = sum(
                haversine_miles(positions[index], positions[index + 1])
                for index in range(len(positions) - 1)
            )
            self.assertIn(segment["level"], colored)
            colored[segment["level"]] += length

        expected = miles_by_level(checkpoints)
        for name in LEVELS:
            self.assertAlmostEqual(colored[name], expected[name], delta=0.01)

    def test_corridor_cap(self):
        coords = [(0.0, mile / 69.0) for mile in range(0, 400)]
        sampled = corridor_samples([coords, coords], spacing_miles=10, cap=30)
        self.assertEqual(len(sampled), 30)

    def test_north_south_route_offsets_side_points(self):
        route = [(40.0, -90.0), (41.0, -90.0)]
        sampled = corridor_samples([route], spacing_miles=30)
        self.assertEqual(sampled, corridor_samples([route], spacing_miles=10))
        cell = 0.2

        def center(value):
            return round(round(value / cell) * cell, 1)

        for lat, lng in sampled:
            self.assertAlmostEqual(lat, center(lat), places=5)
            self.assertAlmostEqual(lng, center(lng), places=5)
        centers = [point for point in sampled if abs(point[1] + 90) < 1e-6]
        self.assertGreaterEqual(len(centers), 2)
        self.assertTrue(any(point[1] < -90 for point in sampled))
        self.assertTrue(any(point[1] > -90 for point in sampled))

        miles = cumulative_miles(route)
        marks = sample_miles(miles[-1], 12)
        target = marks[len(marks) // 2]
        lat, lng = point_at_mile(route, miles, target)
        east, north = _segment_heading(route, miles, target)
        for distance in (10, 20):
            for sign in (1, -1):
                raw = _side_point(lat, lng, east, north, distance, sign)
                snapped = (center(raw[0]), center(raw[1]))
                self.assertIn(snapped, sampled)
                self.assertLessEqual(abs(snapped[0] - raw[0]), cell / 2 + 1e-9)
                self.assertLessEqual(abs(snapped[1] - raw[1]), cell / 2 + 1e-9)
                self.assertGreater(abs(snapped[1] + 90), 1e-6)


if __name__ == "__main__":
    unittest.main()
