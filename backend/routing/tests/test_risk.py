import unittest

from routing.risk import (
    RANK,
    apply_load,
    average_risk,
    band_rain,
    band_snow,
    band_wind,
    classify,
    cm_to_inches,
    format_duration,
    miles_by_level,
    mm_to_inches,
    rank_routes,
    recommendation_reason,
    route_sort_key,
    UNSCORED_AVERAGE,
)


def route(no_travel, severe, high, average, duration, low=0, moderate=0):
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


class BandEdgesTest(unittest.TestCase):
    def test_wind_edges(self):
        self.assertEqual(band_wind(24.99), "Low")
        self.assertEqual(band_wind(25), "Moderate")
        self.assertEqual(band_wind(34.99), "Moderate")
        self.assertEqual(band_wind(35), "High")
        self.assertEqual(band_wind(44.99), "High")
        self.assertEqual(band_wind(45), "Severe")
        self.assertEqual(band_wind(54.99), "Severe")
        self.assertEqual(band_wind(55), "No Travel")

    def test_rain_edges(self):
        self.assertEqual(band_rain(0.099), "Low")
        self.assertEqual(band_rain(0.10), "Moderate")
        self.assertEqual(band_rain(0.249), "Moderate")
        self.assertEqual(band_rain(0.25), "High")
        self.assertEqual(band_rain(0.499), "High")
        self.assertEqual(band_rain(0.50), "Severe")
        self.assertEqual(band_rain(1.00), "Severe")
        self.assertEqual(band_rain(1.001), "No Travel")

    def test_snow_edges(self):
        self.assertEqual(band_snow(0.49), "Low")
        self.assertEqual(band_snow(0.5), "Moderate")
        self.assertEqual(band_snow(0.99), "Moderate")
        self.assertEqual(band_snow(1.0), "High")
        self.assertEqual(band_snow(1.99), "High")
        self.assertEqual(band_snow(2.0), "Severe")
        self.assertEqual(band_snow(3.0), "Severe")
        self.assertEqual(band_snow(3.01), "No Travel")


class LoadRulesTest(unittest.TestCase):
    def test_cutoff_is_strictly_greater_than(self):
        self.assertEqual(apply_load(50, 30000, "Severe"), "Severe")
        self.assertEqual(apply_load(50, 30000.01, "Severe"), "No Travel")
        self.assertEqual(apply_load(45, 30001, "Severe"), "No Travel")
        self.assertEqual(apply_load(54.99, 10000, "Severe"), "Severe")
        self.assertEqual(apply_load(40, 40000, "High"), "High")
        self.assertEqual(apply_load(40, 40001, "High"), "Severe")
        self.assertEqual(apply_load(35, 40001, "High"), "Severe")
        self.assertEqual(apply_load(44.99, 80000, "High"), "Severe")
        self.assertEqual(apply_load(34.99, 80000, "Moderate"), "Moderate")
        self.assertEqual(apply_load(55, 1, "No Travel"), "No Travel")

    def test_load_applies_before_worst_of(self):
        scored = classify(40, 0.0, 0.0, 50000)
        self.assertEqual(scored["wind_level"], "Severe")
        self.assertEqual(scored["level"], "Severe")


class WorstOfTest(unittest.TestCase):
    def test_worst_weather_wins(self):
        scored = classify(10, 0.30, 0.6, 10000)
        self.assertEqual(scored["wind_level"], "Low")
        self.assertEqual(scored["rain_level"], "High")
        self.assertEqual(scored["snow_level"], "Moderate")
        self.assertEqual(scored["level"], "High")

    def test_missing_reading_is_skipped(self):
        scored = classify(50, None, None, 10000)
        self.assertIsNone(scored["rain_level"])
        self.assertIsNone(scored["snow_level"])
        self.assertEqual(scored["level"], "Severe")

    def test_all_missing_has_no_level(self):
        scored = classify(None, None, None, 10000)
        self.assertIsNone(scored["level"])

    def test_rain_and_snow_ignore_load(self):
        scored = classify(None, 0.05, None, 80000)
        self.assertEqual(scored["level"], "Low")


class MilesAndAverageTest(unittest.TestCase):
    def test_partial_last_segment_is_kept_and_last_point_opens_nothing(self):
        checkpoints = [
            {"mile": 0, "level": "Low"},
            {"mile": 25, "level": "High"},
            {"mile": 40, "level": "Severe"},
        ]
        miles = miles_by_level(checkpoints)
        self.assertAlmostEqual(miles["Low"], 0)
        self.assertAlmostEqual(miles["High"], 25)
        self.assertAlmostEqual(miles["Severe"], 15)
        self.assertAlmostEqual(average_risk(miles), (RANK["High"] * 25 + RANK["Severe"] * 15) / 40)

    def test_missing_level_miles_are_not_treated_as_safe(self):
        checkpoints = [
            {"mile": 0, "level": None},
            {"mile": 10, "level": "Low"},
            {"mile": 30, "level": "Low"},
        ]
        miles = miles_by_level(checkpoints)
        self.assertAlmostEqual(sum(miles.values()), 20)
        self.assertAlmostEqual(average_risk(miles), 1)

    def test_no_scored_miles(self):
        self.assertIsNone(average_risk({name: 0.0 for name in RANK}))


class RankRoutesTest(unittest.TestCase):
    def _assert_reason_names_the_ranked_winner(self, routes):
        rank_routes(routes)
        reason = recommendation_reason(routes)
        first = min(range(len(routes)), key=lambda index: route_sort_key(routes[index], index))
        self.assertEqual(
            [index for index, item in enumerate(routes) if item["recommended"]],
            [first],
        )
        if len(routes) == 1:
            self.assertEqual(reason, "Only route returned.")
            return
        if " vs " not in reason:
            return
        winner = routes[first]
        shown = reason.split(" vs ", 1)[0].split(": ", 1)[1]
        if reason.startswith("Fewest No Travel miles"):
            expected = f"{winner['miles']['No Travel']:.1f}"
        elif reason.startswith("Fewest Severe miles"):
            expected = f"{winner['miles']['Severe']:.1f}"
        elif reason.startswith("Fewest High miles"):
            expected = f"{winner['miles']['High']:.1f}"
        elif reason.startswith("Lowest average risk"):
            value = winner["average_risk"]
            expected = f"{(UNSCORED_AVERAGE if value is None else value):.2f}"
        else:
            self.assertTrue(reason.startswith("Shortest travel time: "))
            expected = format_duration(winner["duration_seconds"])
        self.assertEqual(shown, expected)

    def test_no_travel_loses_to_a_severe_route(self):
        routes = [
            route(10, 0, 0, 5, 100),
            route(0, 50, 0, 4, 200),
        ]
        rank_routes(routes)
        self.assertFalse(routes[0]["recommended"])
        self.assertTrue(routes[1]["recommended"])

    def test_full_order(self):
        fewer_high = route(0, 10, 50, 3.5, 400)
        more_high = route(0, 10, 100, 3, 300)
        lower_average = route(0, 10, 50, 2, 500)
        shorter = route(0, 10, 50, 2, 100)
        routes = [more_high, fewer_high, lower_average, shorter]
        rank_routes(routes)
        self.assertTrue(shorter["recommended"])
        self.assertEqual(sum(route["recommended"] for route in routes), 1)

    def test_tie_falls_through_to_travel_time_then_provider_order(self):
        slower = route(0, 5, 5, 2, 300, low=10)
        faster = route(0, 5, 5, 2, 100, low=10)
        routes = [slower, faster]
        rank_routes(routes)
        self.assertTrue(faster["recommended"])

        first = route(0, 1, 1, 1.5, 100, low=8)
        second = route(0, 1, 1, 1.5, 100, low=8)
        tied = [first, second]
        rank_routes(tied)
        self.assertTrue(first["recommended"])
        self.assertFalse(second["recommended"])

    def test_unscored_route_does_not_beat_a_scored_one(self):
        blank = route(0, 0, 0, None, 10)
        scored = route(0, 0, 0, 4, 9999, low=1)
        routes = [blank, scored]
        rank_routes(routes)
        self.assertTrue(scored["recommended"])

    def test_reason_quotes_the_runner_up(self):
        severe = route(0, 12.4, 0, 3, 7 * 3600)
        winner = route(0, 0, 1, 1.5, 6 * 3600 + 10 * 60)
        routes = [severe, winner]
        rank_routes(routes)
        self.assertEqual(recommendation_reason(routes), "Fewest Severe miles: 0.0 vs 12.4 mi")
        self._assert_reason_names_the_ranked_winner(routes)

        longer = route(0, 0, 0, 1, 6 * 3600 + 45 * 60, low=10)
        shorter = route(0, 0, 0, 1, 6 * 3600 + 10 * 60, low=10)
        tied_miles = [longer, shorter]
        rank_routes(tied_miles)
        self.assertEqual(
            recommendation_reason(tied_miles),
            "Shortest travel time: 6 h 10 min vs 6 h 45 min",
        )
        self._assert_reason_names_the_ranked_winner(tied_miles)

    def test_high_miles_use_the_runner_up_not_the_best_other_route(self):
        orders = (("A", "B", "C"), ("C", "A", "B"), ("B", "C", "A"), ("A", "C", "B"))
        for order in orders:
            made = {
                "A": route(0, 0, 10, 2, 1000),
                "B": route(0, 0, 20, 2, 1000),
                "C": route(0, 5, 0, 2, 1000),
            }
            routes = [made[name] for name in order]
            with self.subTest(order=order):
                rank_routes(routes)
                self.assertEqual(
                    recommendation_reason(routes),
                    "Fewest High miles: 10.0 vs 20.0 mi",
                )
                self._assert_reason_names_the_ranked_winner(routes)

    def test_duration_seconds_against_the_runner_up(self):
        longer = route(0, 0, 0, 1, 24300, low=10)
        shorter = route(0, 0, 0, 1, 22200, low=10)
        routes = [longer, shorter]
        rank_routes(routes)
        self.assertEqual(
            recommendation_reason(routes),
            "Shortest travel time: 6 h 10 min vs 6 h 45 min",
        )
        self._assert_reason_names_the_ranked_winner(routes)

    def test_only_route_returned(self):
        routes = [route(1, 2, 3, 4, 5)]
        rank_routes(routes)
        self.assertEqual(recommendation_reason(routes), "Only route returned.")
        self._assert_reason_names_the_ranked_winner(routes)
        self.assertIsNone(recommendation_reason([]))


class UnitConversionTest(unittest.TestCase):
    def test_provider_units(self):
        self.assertAlmostEqual(mm_to_inches(25.4), 1)
        self.assertAlmostEqual(cm_to_inches(2.54), 1)
        self.assertIsNone(mm_to_inches(None))
        self.assertIsNone(cm_to_inches(None))


if __name__ == "__main__":
    unittest.main()
