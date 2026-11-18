import math
import unittest
from datetime import datetime, timedelta, timezone

from routing.geo import (
    EARTH_MILES,
    color_segments,
    corridor_samples,
    cumulative_miles,
    haversine_miles,
    place_checkpoints,
    point_at_mile,
    sample_miles,
    simplify,
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

    def test_corridor_uses_cell_centers_within_22_miles(self):
        route = [(40.0, -90.0), (41.5, -90.0)]
        sampled = corridor_samples([route], 0.2)
        self.assertEqual(sampled, corridor_samples([route, route], 0.2))
        self.assertGreaterEqual(len(sampled), 2)
        miles = cumulative_miles(route)
        marks = [point_at_mile(route, miles, mile) for mile in sample_miles(miles[-1], 6)]
        deg = 0.2
        for lat, lng in sampled:
            self.assertAlmostEqual(lat, (math.floor(lat / deg + 1e-9) + 0.5) * deg, places=7)
            self.assertAlmostEqual(lng, (math.floor(lng / deg + 1e-9) + 0.5) * deg, places=7)
            self.assertTrue(any(haversine_miles((lat, lng), mark) <= 22 + 1e-6 for mark in marks))
        self.assertTrue(any(lng < -90 for _lat, lng in sampled))
        self.assertTrue(any(lng > -90 for _lat, lng in sampled))


class SimplifyTest(unittest.TestCase):
    def test_straight_line_keeps_endpoints(self):
        coords = [(40.0, -90.0 + i * 0.01) for i in range(100)]
        simplified = simplify(coords)
        self.assertEqual(len(simplified), 2)
        self.assertEqual(simplified[0][:2], coords[0])
        self.assertEqual(simplified[-1][:2], coords[-1])

    def test_l_shape_keeps_the_corner(self):
        horizontal = [(40.0, -90.0 + i * 0.02) for i in range(11)]
        vertical = [(40.0 + i * 0.02, horizontal[-1][1]) for i in range(1, 11)]
        coords = horizontal + vertical
        simplified = simplify(coords)
        pairs = [(lat, lng) for lat, lng, _mile in simplified]
        self.assertEqual(pairs[0], coords[0])
        self.assertEqual(pairs[-1], coords[-1])
        self.assertIn(horizontal[-1], pairs)
        self.assertEqual(len(pairs), 3)

    def test_miles_stay_on_the_original_route(self):
        coords = [(0.0, 0.0)]
        for index in range(1, 40):
            coords.append((index * 0.02, 0.0002 if index % 2 else 0.0))
        coords.append((0.8, 0.0))
        simplified = simplify(coords)
        self.assertEqual(len(simplified), 2)
        full = cumulative_miles(coords)[-1]
        self.assertAlmostEqual(simplified[-1][2], full)
        self.assertGreater(full, haversine_miles(coords[0], coords[-1]))

    def test_simplified_geometry_splits_at_checkpoint_miles(self):
        coords = [(0.0, index * 0.01) for index in range(80)]
        total = cumulative_miles(coords)[-1]
        simplified = simplify(coords)
        self.assertEqual(len(simplified), 2)
        self.assertAlmostEqual(simplified[-1][2], total)
        mile = 25
        checkpoints = [
            {"mile": 0, "lat": coords[0][0], "lng": coords[0][1], "level": "Low"},
            {"mile": mile, "lat": 0.0, "lng": math.degrees(mile / EARTH_MILES), "level": "High"},
            {"mile": total, "lat": coords[-1][0], "lng": coords[-1][1], "level": "Severe"},
        ]
        segments = color_segments(simplified, checkpoints)
        self.assertEqual(segments[0]["level"], "High")
        self.assertEqual(segments[-1]["level"], "Severe")
        flat = [point for segment in segments for point in segment["positions"]]
        self.assertTrue(any(abs(point[1] - checkpoints[1]["lng"]) < 1e-9 for point in flat))


if __name__ == "__main__":
    unittest.main()
