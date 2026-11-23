"""Deterministic checks of the assessment tables against the current engine.

These tests record what the code does. They do not change band edges,
load cutoffs, or the sort order.
"""

import math
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from routing.geo import (
    EARTH_MILES,
    cumulative_miles,
    haversine_miles,
    place_checkpoints,
    time_at_coords,
)
from routing.risk import (
    RANK,
    apply_load,
    average_risk,
    band_rain,
    band_snow,
    band_wind,
    classify,
    cm_to_inches,
    miles_by_level,
    mm_to_inches,
    rank_routes,
    recommendation_reason,
)
from routing.services import PlanError, build_plan, parse_departure, parse_load

MILES_PER_DEG = EARTH_MILES * math.pi / 180.0
NOW = datetime(2026, 11, 18, 11, 0, tzinfo=timezone.utc)
# Lower bound is inclusive. The next band starts at the displayed edge.
# Rain and snow No Travel start strictly above 1.00 and 3.0. Wind No Travel starts at 55.
WIND_EDGES = (
    (24.99, "Low"),
    (25, "Moderate"),
    (34.99, "Moderate"),
    (35, "High"),
    (44.99, "High"),
    (45, "Severe"),
    (54.99, "Severe"),
    (55, "No Travel"),
)
RAIN_EDGES = (
    (0.099, "Low"),
    (0.10, "Moderate"),
    (0.249, "Moderate"),
    (0.25, "High"),
    (0.499, "High"),
    (0.50, "Severe"),
    (1.00, "Severe"),
    (1.001, "No Travel"),
)
SNOW_EDGES = (
    (0.499, "Low"),
    (0.5, "Moderate"),
    (0.999, "Moderate"),
    (1.0, "High"),
    (1.999, "High"),
    (2.0, "Severe"),
    (3.0, "Severe"),
    (3.001, "No Travel"),
)
LOAD_CASES = (
    (55, 10000, "No Travel"),
    (55, 50000, "No Travel"),
    (50, 20000, "Severe"),
    (50, 30000, "Severe"),
    (50, 30001, "No Travel"),
    (40, 30000, "High"),
    (40, 40000, "High"),
    (40, 40001, "Severe"),
    (35, 40001, "Severe"),
    (45, 30001, "No Travel"),
)
COMBINED = (
    (("Low", "Low", "Low"), "Low"),
    (("Moderate", "Low", "Low"), "Moderate"),
    (("Low", "High", "Low"), "High"),
    (("Moderate", "Severe", "Low"), "Severe"),
    (("High", "Moderate", "Severe"), "Severe"),
    (("Low", "No Travel", "Low"), "No Travel"),
    (("No Travel", "Low", "Low"), "No Travel"),
)
# Values that land in each named band with a 10,000 lb load (no override).
SAMPLE = {
    "Low": (10, 0.01, 0.1),
    "Moderate": (30, 0.15, 0.7),
    "High": (40, 0.30, 1.5),
    "Severe": (50, 0.75, 2.5),
    "No Travel": (60, 1.5, 4.0),
}


def _north(miles, steps=40, lat0=40.0, lng=-104.0):
    span = miles / MILES_PER_DEG
    return [(lat0 + span * index / steps, lng) for index in range(steps + 1)]


def _route(no_travel, severe, high, average, duration, low=0, moderate=0):
    return {
        "miles": {
            "Low": low,
            "Moderate": moderate,
            "High": high,
            "Severe": severe,
            "No Travel": no_travel,
        },
        "average_risk": average,
        "duration_seconds": duration,
    }


def _winner(routes):
    rank_routes(routes)
    return next(index for index, route in enumerate(routes) if route["recommended"])


class ThresholdTest(unittest.TestCase):
    def test_wind_table(self):
        for mph, level in WIND_EDGES:
            with self.subTest(mph=mph):
                self.assertEqual(band_wind(mph), level)
                self.assertEqual(classify(mph, None, None, 10000)["level"], level)

    def test_rain_table(self):
        for inches, level in RAIN_EDGES:
            with self.subTest(inches=inches):
                self.assertEqual(band_rain(inches), level)

    def test_snow_table(self):
        for inches, level in SNOW_EDGES:
            with self.subTest(inches=inches):
                self.assertEqual(band_snow(inches), level)

    def test_shared_edges_belong_to_the_higher_band(self):
        self.assertEqual(band_rain(0.25), "High")
        self.assertEqual(band_rain(0.50), "Severe")
        self.assertEqual(band_snow(0.5), "Moderate")
        self.assertEqual(band_snow(1.0), "High")
        self.assertEqual(band_snow(2.0), "Severe")
        self.assertEqual(band_wind(25), "Moderate")
        self.assertEqual(band_wind(55), "No Travel")


class LoadOverrideTest(unittest.TestCase):
    def test_table(self):
        for mph, load, level in LOAD_CASES:
            with self.subTest(mph=mph, load=load):
                scored = classify(mph, 0.0, 0.0, load)
                self.assertEqual(scored["wind_level"], level)
                self.assertEqual(scored["level"], level)

    def test_exact_cutoffs_do_not_fire(self):
        self.assertEqual(apply_load(50, 30000, "Severe"), "Severe")
        self.assertEqual(apply_load(40, 40000, "High"), "High")

    def test_fractional_load_above_the_cutoff_fires(self):
        self.assertEqual(classify(50, 0, 0, 30000.5)["level"], "No Travel")
        self.assertEqual(classify(40, 0, 0, 40000.5)["level"], "Severe")

    def test_huge_load_uses_the_same_rules(self):
        self.assertEqual(classify(40, 0, 0, 1_000_000_000)["level"], "Severe")
        self.assertEqual(classify(50, 0, 0, 1_000_000_000)["level"], "No Travel")
        self.assertEqual(classify(10, 0, 0, 1_000_000_000)["level"], "Low")

    def test_parse_load_rejects_missing_zero_negative_and_text(self):
        for value in (None, "", 0, -1, -0.5, "10000 kg", "heavy", True):
            with self.subTest(value=value):
                with self.assertRaises(PlanError):
                    parse_load(value)
        self.assertEqual(parse_load("42000"), 42000.0)
        self.assertEqual(parse_load(30000.5), 30000.5)


class CombinedRiskTest(unittest.TestCase):
    def test_overall_is_the_worst_part(self):
        for (wind, rain, snow), expected in COMBINED:
            with self.subTest(wind=wind, rain=rain, snow=snow):
                wind_mph, _, _ = SAMPLE[wind]
                _, rain_in, _ = SAMPLE[rain]
                _, _, snow_in = SAMPLE[snow]
                scored = classify(wind_mph, rain_in, snow_in, 10000)
                self.assertEqual(scored["wind_level"], wind)
                self.assertEqual(scored["rain_level"], rain)
                self.assertEqual(scored["snow_level"], snow)
                self.assertEqual(scored["level"], expected)
                self.assertGreaterEqual(RANK[scored["level"]], RANK[wind])
                self.assertGreaterEqual(RANK[scored["level"]], RANK[rain])
                self.assertGreaterEqual(RANK[scored["level"]], RANK[snow])

    def test_missing_part_is_not_low(self):
        scored = classify(None, None, 2.5, 10000)
        self.assertIsNone(scored["wind_level"])
        self.assertIsNone(scored["rain_level"])
        self.assertEqual(scored["snow_level"], "Severe")
        self.assertEqual(scored["level"], "Severe")

    def test_all_missing_has_no_level(self):
        self.assertIsNone(classify(None, None, None, 10000)["level"])


class CheckpointDistanceTest(unittest.TestCase):
    def test_spacing_follows_the_road_and_keeps_the_tail(self):
        lengths = (10, 25, 50, 62, 100, 250, 1000)
        spacings = (10, 25, 50)
        for length in lengths:
            coords = _north(length)
            driven = cumulative_miles(coords)[-1]
            straight = haversine_miles(coords[0], coords[-1])
            self.assertAlmostEqual(driven, length, delta=0.02)
            self.assertAlmostEqual(driven, straight, delta=0.02)
            for spacing in spacings:
                with self.subTest(length=length, spacing=spacing):
                    departure = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
                    placed = place_checkpoints(coords, spacing, departure, 3600)
                    miles = [point["mile"] for point in placed]
                    self.assertEqual(miles[0], 0)
                    self.assertAlmostEqual(miles[-1], driven, places=6)
                    gaps = [miles[index + 1] - miles[index] for index in range(len(miles) - 1)]
                    self.assertAlmostEqual(sum(gaps), driven, places=6)
                    for gap in gaps[:-1]:
                        self.assertAlmostEqual(gap, spacing, places=6)
                    self.assertLessEqual(gaps[-1], spacing + 1e-6)
                    self.assertGreater(gaps[-1], 0)
                    for point in placed:
                        self.assertAlmostEqual(point["lng"], coords[0][1], places=6)
                        self.assertGreaterEqual(point["lat"], coords[0][0] - 1e-9)

    def test_corner_checkpoint_is_not_on_the_straight_line(self):
        lat0, lng0 = 40.0, -100.0
        east = 50 / (MILES_PER_DEG * math.cos(math.radians(lat0)))
        north = 50 / MILES_PER_DEG
        corner = (lat0, lng0 + east)
        coords = [(lat0, lng0), corner, (lat0 + north, lng0 + east)]
        driven = cumulative_miles(coords)[-1]
        straight = haversine_miles(coords[0], coords[-1])
        self.assertAlmostEqual(driven, 100, delta=0.05)
        self.assertLess(straight, 80)
        departure = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
        placed = place_checkpoints(coords, 50, departure, 7200)
        at_50 = next(point for point in placed if abs(point["mile"] - 50) < 1e-6)
        self.assertLess(haversine_miles((at_50["lat"], at_50["lng"]), corner), 0.05)
        midpoint = ((coords[0][0] + coords[-1][0]) / 2, (coords[0][1] + coords[-1][1]) / 2)
        self.assertGreater(haversine_miles((at_50["lat"], at_50["lng"]), midpoint), 20)

    def test_scored_miles_conserve_the_route(self):
        checkpoints = [
            {"mile": 0, "level": "Low"},
            {"mile": 20, "level": "Low"},
            {"mile": 50, "level": "Moderate"},
            {"mile": 75, "level": "High"},
            {"mile": 90, "level": "Severe"},
            {"mile": 100, "level": "No Travel"},
        ]
        miles = miles_by_level(checkpoints)
        self.assertAlmostEqual(miles["Low"], 20)
        self.assertAlmostEqual(miles["Moderate"], 30)
        self.assertAlmostEqual(miles["High"], 25)
        self.assertAlmostEqual(miles["Severe"], 15)
        self.assertAlmostEqual(miles["No Travel"], 10)
        self.assertAlmostEqual(sum(miles.values()), 100)

    def test_both_ends_missing_are_omitted_not_called_low(self):
        checkpoints = [
            {"mile": 0, "level": None},
            {"mile": 40, "level": None},
            {"mile": 100, "level": "High"},
        ]
        miles = miles_by_level(checkpoints)
        self.assertAlmostEqual(miles["Low"], 0)
        self.assertAlmostEqual(miles["High"], 60)
        self.assertAlmostEqual(miles["Unassessed"], 40)
        self.assertAlmostEqual(sum(miles.values()), 100)


class EtaTest(unittest.TestCase):
    def test_uniform_speed_puts_the_midpoint_at_two_hours(self):
        coords = _north(120)
        departure = datetime(2026, 11, 18, 8, 0, tzinfo=timezone.utc)
        placed = place_checkpoints(coords, 60, departure, 4 * 3600)
        by_mile = {round(point["mile"]): point["eta"] for point in placed}
        self.assertEqual(by_mile[0], departure)
        self.assertEqual(by_mile[60], datetime(2026, 11, 18, 10, 0, tzinfo=timezone.utc))
        self.assertEqual(by_mile[120], datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc))

    def test_section_durations_override_uniform_speed(self):
        coords = _north(120, steps=2)
        # First 60 miles take 1 hour. The second 60 miles take 3 hours.
        spans = [(0, 1, 3600), (1, 2, 3 * 3600)]
        seconds = time_at_coords(coords, spans, 4 * 3600)
        departure = datetime(2026, 11, 18, 8, 0, tzinfo=timezone.utc)
        placed = place_checkpoints(coords, 60, departure, 4 * 3600, seconds)
        by_mile = {round(point["mile"]): point["eta"] for point in placed}
        self.assertEqual(by_mile[60], datetime(2026, 11, 18, 9, 0, tzinfo=timezone.utc))
        self.assertEqual(by_mile[120], datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc))

    def test_midnight_crossing_stays_on_the_same_instant(self):
        coords = _north(60, steps=2)
        departure = datetime(2026, 11, 18, 23, 0, tzinfo=timezone.utc)
        placed = place_checkpoints(coords, 60, departure, 4 * 3600)
        self.assertEqual(placed[-1]["eta"], datetime(2026, 11, 19, 3, 0, tzinfo=timezone.utc))

    def test_offset_departure_converts_to_utc_without_moving_the_instant(self):
        parsed = parse_departure("2026-11-18T12:00:00-06:00", now=NOW)
        self.assertEqual(parsed, datetime(2026, 11, 18, 18, 0, tzinfo=timezone.utc))
        standard = parse_departure("2026-11-01T01:30:00-06:00", now=datetime(2026, 10, 31, tzinfo=timezone.utc))
        daylight = parse_departure("2026-11-01T01:30:00-05:00", now=datetime(2026, 10, 31, tzinfo=timezone.utc))
        self.assertEqual(standard - daylight, timedelta(hours=1))

    def test_invalid_departure_is_rejected(self):
        with self.assertRaises(PlanError):
            parse_departure("tomorrow", now=NOW)
        with self.assertRaises(PlanError):
            parse_departure(None, now=NOW)
        with self.assertRaises(PlanError):
            parse_departure("2026-11-18T08:00:00+00:00", now=NOW)


class WeatherAndPlanTest(unittest.TestCase):
    def setUp(self):
        patcher = patch("routing.services._utcnow", return_value=NOW)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _plan(self, coords, duration, wind_at_hour, load=10000, spacing=60, rain_mm=0.0, snow_cm=0.0):
        def routes(_origin, _destination, _load_lb=None):
            return [{"coords": coords, "duration_seconds": duration, "profile": "driving-hgv"}]

        def weather(points, _start, _end):
            rows = []
            for _lat, _lng in points:
                times = []
                cursor = datetime(2026, 11, 18, tzinfo=timezone.utc)
                winds = []
                for hour in range(96):
                    times.append(cursor.strftime("%Y-%m-%dT%H:%M"))
                    winds.append(wind_at_hour(hour))
                    cursor += timedelta(hours=1)
                rows.append(
                    {
                        "time": times,
                        "wind_speed_10m": winds,
                        "rain": [rain_mm] * 96,
                        "snowfall": [snow_cm] * 96,
                    }
                )
            return rows

        return build_plan(
            {
                "origin": {"name": "Start", "lat": coords[0][0], "lng": coords[0][1]},
                "destination": {"name": "End", "lat": coords[-1][0], "lng": coords[-1][1]},
                "departure": "2026-11-18T12:00:00+00:00",
                "load_lb": load,
                "spacing_miles": spacing,
            },
            routes,
            weather,
        )

    def test_each_checkpoint_uses_its_own_arrival_hour(self):
        # 120 miles in 4 hours, sampled every 50 miles: arrivals at 12:00, 13:40, 15:20, 16:00.
        winds = {12: 10, 13: 40, 15: 40, 16: 55}
        plan = self._plan(_north(120), 4 * 3600, lambda hour: winds.get(hour, 10), spacing=50)
        route = plan["routes"][0]
        by_hour = {
            datetime.fromisoformat(point["eta"]).hour: point for point in route["checkpoints"]
        }
        self.assertEqual(by_hour[12]["wind_mph"], 10)
        self.assertEqual(by_hour[12]["level"], "Low")
        self.assertEqual(by_hour[13]["wind_mph"], 40)
        self.assertEqual(by_hour[13]["level"], "High")
        self.assertEqual(by_hour[15]["wind_mph"], 40)
        self.assertEqual(by_hour[16]["wind_mph"], 55)
        self.assertEqual(by_hour[16]["level"], "No Travel")
        self.assertNotEqual(by_hour[12]["wind_mph"], by_hour[16]["wind_mph"])
        self.assertAlmostEqual(route["miles"]["High"], 100, delta=0.05)
        self.assertAlmostEqual(route["miles"]["No Travel"], 20, delta=0.05)
        self.assertAlmostEqual(route["miles"]["Low"], 0, delta=0.05)
        self.assertEqual(route["arrival"], "2026-11-18T16:00:00+00:00")

    def test_units_reach_the_risk_engine_in_inches(self):
        # 2.54 mm/h = 0.10 in/hr (Moderate). 1.27 cm/h = 0.50 in/hr (Moderate snow).
        plan = self._plan(
            _north(30, steps=2), 3600, lambda _hour: 10, spacing=25, rain_mm=2.54, snow_cm=1.27
        )
        point = plan["routes"][0]["checkpoints"][0]
        self.assertAlmostEqual(point["rain_in_hr"], mm_to_inches(2.54), places=6)
        self.assertAlmostEqual(point["snow_in_hr"], cm_to_inches(1.27), places=6)
        self.assertEqual(point["rain_level"], "Moderate")
        self.assertEqual(point["snow_level"], "Moderate")
        self.assertAlmostEqual(mm_to_inches(25.4), 1)
        self.assertAlmostEqual(cm_to_inches(2.54), 1)

    def test_heavy_load_rewrites_wind_before_the_route_is_ranked(self):
        calm = self._plan(_north(30, steps=2), 3600, lambda _hour: 40, load=40000, spacing=25)
        heavy = self._plan(_north(30, steps=2), 3600, lambda _hour: 40, load=45000, spacing=25)
        self.assertEqual(calm["routes"][0]["checkpoints"][0]["level"], "High")
        self.assertEqual(heavy["routes"][0]["checkpoints"][0]["level"], "Severe")

    def test_routes_are_ranked_from_their_own_weather(self):
        def routes(_origin, _destination, _load_lb=None):
            return [
                {"coords": _north(40, lat0=30.0), "duration_seconds": 3 * 3600, "profile": "driving-hgv"},
                {"coords": _north(40, lat0=45.0), "duration_seconds": 6 * 3600, "profile": "driving-hgv"},
            ]

        def weather(points, _start, _end):
            rows = []
            times = []
            cursor = datetime(2026, 11, 18, tzinfo=timezone.utc)
            for _hour in range(96):
                times.append(cursor.strftime("%Y-%m-%dT%H:%M"))
                cursor += timedelta(hours=1)
            for lat, _lng in points:
                wind = 50 if lat >= 40 else 10
                rows.append(
                    {
                        "time": times,
                        "wind_speed_10m": [wind] * 96,
                        "rain": [0.0] * 96,
                        "snowfall": [0.0] * 96,
                    }
                )
            return rows

        plan = build_plan(
            {
                "origin": {"name": "Start", "lat": 40, "lng": -104},
                "destination": {"name": "End", "lat": 42, "lng": -104},
                "departure": "2026-11-18T12:00:00+00:00",
                "load_lb": 10000,
                "spacing_miles": 25,
            },
            routes,
            weather,
        )
        calm, stormy = plan["routes"]
        self.assertEqual(calm["checkpoints"][0]["level"], "Low")
        self.assertEqual(stormy["checkpoints"][0]["level"], "Severe")
        self.assertGreater(stormy["miles"]["Severe"], 0)
        self.assertAlmostEqual(calm["miles"]["Severe"], 0)
        self.assertTrue(calm["recommended"])
        self.assertFalse(stormy["recommended"])
        self.assertIn("Fewest Severe miles", calm["recommendation_reason"])

    def test_a_missing_reading_stays_missing(self):
        coords = _north(60, steps=4)

        def routes(_origin, _destination, _load_lb=None):
            return [{"coords": coords, "duration_seconds": 2 * 3600, "profile": "driving-hgv"}]

        def weather(points, _start, _end):
            rows = []
            for _lat, _lng in points:
                times = []
                cursor = datetime(2026, 11, 18, tzinfo=timezone.utc)
                winds, rains, snows = [], [], []
                for hour in range(96):
                    times.append(cursor.strftime("%Y-%m-%dT%H:%M"))
                    # Wind is the stamp at the ETA. Rain and snow are the next stamp.
                    winds.append(None if hour == 12 else 10)
                    rains.append(None if hour == 13 else 0.0)
                    snows.append(None if hour == 13 else 0.0)
                    cursor += timedelta(hours=1)
                rows.append(
                    {"time": times, "wind_speed_10m": winds, "rain": rains, "snowfall": snows}
                )
            return rows

        plan = build_plan(
            {
                "origin": {"name": "Start", "lat": coords[0][0], "lng": coords[0][1]},
                "destination": {"name": "End", "lat": coords[-1][0], "lng": coords[-1][1]},
                "departure": "2026-11-18T12:00:00+00:00",
                "load_lb": 10000,
                "spacing_miles": 50,
            },
            routes,
            weather,
        )
        origin = plan["routes"][0]["checkpoints"][0]
        self.assertIsNone(origin["wind_mph"])
        self.assertIsNone(origin["rain_in_hr"])
        self.assertIsNone(origin["snow_in_hr"])
        self.assertIsNone(origin["level"])

    def test_provider_failure_does_not_invent_a_recommendation(self):
        def routes(_origin, _destination, _load_lb=None):
            return []

        with self.assertRaises(PlanError) as raised:
            build_plan(
                {
                    "origin": {"name": "Start", "lat": 40, "lng": -104},
                    "destination": {"name": "End", "lat": 41, "lng": -104},
                    "departure": "2026-11-18T12:00:00+00:00",
                    "load_lb": 10000,
                    "spacing_miles": 25,
                },
                routes,
                lambda *_args: [],
            )
        self.assertEqual(raised.exception.status, 502)

        def boom(*_args, **_kwargs):
            raise PlanError("The weather service rejected the forecast request.", status=502)

        with self.assertRaises(PlanError) as weather_error:
            build_plan(
                {
                    "origin": {"name": "Start", "lat": 40, "lng": -104},
                    "destination": {"name": "End", "lat": 41, "lng": -104},
                    "departure": "2026-11-18T12:00:00+00:00",
                    "load_lb": 10000,
                    "spacing_miles": 25,
                },
                lambda *_args: [
                    {"coords": _north(30, steps=2), "duration_seconds": 3600, "profile": "driving-hgv"}
                ],
                boom,
            )
        self.assertEqual(weather_error.exception.status, 502)


class RecommendationTest(unittest.TestCase):
    def test_severe_miles_beat_high_miles_and_a_shorter_trip(self):
        # Scenario A. Route B has fewer Severe miles.
        routes = [
            _route(0, 5, 0, 2, 3 * 3600),
            _route(0, 2, 30, 3, 6 * 3600),
        ]
        self.assertEqual(_winner(routes), 1)

    def test_high_miles_are_second(self):
        routes = [
            _route(0, 0, 20, 2, 4 * 3600, low=80),
            _route(0, 0, 10, 2, 4 * 3600, low=90),
        ]
        self.assertEqual(_winner(routes), 1)

    def test_average_risk_is_third_and_uses_scores_1_through_5(self):
        # Scenario C. Low=1, Moderate=2, High=3, Severe=4, No Travel=5.
        miles_a = {"Low": 20, "Moderate": 70, "High": 10, "Severe": 0, "No Travel": 0}
        miles_b = {"Low": 50, "Moderate": 40, "High": 10, "Severe": 0, "No Travel": 0}
        average_a = average_risk(miles_a)
        average_b = average_risk(miles_b)
        self.assertAlmostEqual(average_a, (20 * 1 + 70 * 2 + 10 * 3) / 100)
        self.assertAlmostEqual(average_b, (50 * 1 + 40 * 2 + 10 * 3) / 100)
        # The 0-based scale in the prompt is exactly one point lower and picks the same winner.
        self.assertAlmostEqual(average_a - 1, (20 * 0 + 70 * 1 + 10 * 2) / 100)
        self.assertAlmostEqual(average_b - 1, (50 * 0 + 40 * 1 + 10 * 2) / 100)
        self.assertLess(average_b, average_a)
        routes = [
            _route(0, 0, 10, average_a, 5 * 3600, low=20, moderate=70),
            _route(0, 0, 10, average_b, 5 * 3600, low=50, moderate=40),
        ]
        self.assertEqual(_winner(routes), 1)

    def test_travel_time_is_last(self):
        routes = [
            _route(0, 0, 10, 1.6, 5 * 3600, low=50, moderate=40),
            _route(0, 0, 10, 1.6, 4 * 3600, low=50, moderate=40),
        ]
        self.assertEqual(_winner(routes), 1)
        self.assertEqual(
            recommendation_reason(routes),
            "Shortest travel time: 4 h 0 min vs 5 h 0 min",
        )

    def test_a_complete_tie_keeps_the_first_route(self):
        routes = [
            _route(0, 0, 10, 1.6, 4 * 3600, low=50, moderate=40),
            _route(0, 0, 10, 1.6, 4 * 3600, low=50, moderate=40),
        ]
        self.assertEqual(_winner(routes), 0)
        self.assertEqual(
            recommendation_reason(routes),
            "Tied on risk and time; kept the first route the routing service returned.",
        )

    def test_no_travel_miles_are_ranked_before_severe_miles(self):
        # Four-key order (Severe first) would pick route B. This engine picks route A.
        routes = [
            _route(0, 100, 0, 4, 3 * 3600),
            _route(10, 0, 0, 5, 3 * 3600),
        ]
        self.assertEqual(_winner(routes), 0)
        self.assertEqual(recommendation_reason(routes), "Only safe route.")

    def test_a_clear_route_beats_routes_that_include_no_travel(self):
        routes = [
            _route(12, 0, 0, 5, 3 * 3600),
            _route(0, 4, 0, 2, 5 * 3600),
            _route(8, 0, 0, 4, 4 * 3600),
        ]
        self.assertEqual(_winner(routes), 1)
        self.assertGreater(routes[0]["miles"]["No Travel"], 0)
        self.assertGreater(routes[2]["miles"]["No Travel"], 0)


class AverageScaleTest(unittest.TestCase):
    def test_rank_table_starts_at_one(self):
        self.assertEqual(
            [RANK[name] for name in ("Low", "Moderate", "High", "Severe", "No Travel")],
            [1, 2, 3, 4, 5],
        )
