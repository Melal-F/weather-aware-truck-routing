import unittest
from datetime import datetime, timedelta, timezone

from routing.risk import cm_to_inches, mm_to_inches
from routing.services import hour_index, reading_at


def _row():
    start = datetime(2026, 11, 18, tzinfo=timezone.utc)
    times = [(start + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(96)]
    return {"time": times, "first": start}


def _at(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


class HourIndexTest(unittest.TestCase):
    def test_hour_offsets(self):
        row = _row()
        self.assertEqual(hour_index(row, _at("2026-11-18T05:37Z")), 5)
        self.assertEqual(hour_index(row, _at("2026-11-21T23:59Z")), 95)
        self.assertIsNone(hour_index(row, _at("2026-11-22T00:00Z")))
        self.assertIsNone(hour_index(row, _at("2026-11-17T23:00Z")))


class PrecipitationHourTest(unittest.TestCase):
    def _row(self):
        start = datetime(2026, 11, 18, tzinfo=timezone.utc)
        times = [(start + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(10)]
        snow = [0.0] * 10
        snow[5] = 2.54
        rain = [0.0] * 10
        rain[5] = 25.4
        showers = [0.0] * 10
        showers[5] = 25.4
        return {
            "time": times,
            "first": start,
            "wind_speed_10m": [float(i) for i in range(10)],
            "rain": rain,
            "showers": showers,
            "snowfall": snow,
        }, start

    def test_snow_stamp_belongs_to_the_preceding_hour(self):
        row, _start = self._row()
        _wind, _rain, snow = reading_at(row, _at("2026-11-18T04:26Z"))
        self.assertAlmostEqual(snow, cm_to_inches(2.54))
        _wind, _rain, later = reading_at(row, _at("2026-11-18T05:26Z"))
        self.assertEqual(later, 0.0)

    def test_wind_stays_on_the_eta_hour(self):
        row, _start = self._row()
        wind, _rain, _snow = reading_at(row, _at("2026-11-18T04:26Z"))
        self.assertEqual(wind, 4.0)

    def test_showers_are_read_with_rain_on_the_next_stamp(self):
        row, _start = self._row()
        _wind, rain, _snow = reading_at(row, _at("2026-11-18T04:26Z"))
        self.assertAlmostEqual(rain, mm_to_inches(50.8))

    def test_corridor_hour_uses_the_following_rain_stamp(self):
        row, start = self._row()
        for hour in range(9):
            _wind, rain, _snow = reading_at(row, start + timedelta(hours=hour))
            self.assertAlmostEqual(rain, mm_to_inches(row["rain"][hour + 1] + row["showers"][hour + 1]))
        wind, rain, snow = reading_at(row, start + timedelta(hours=9))
        self.assertEqual(wind, 9.0)
        self.assertIsNone(rain)
        self.assertIsNone(snow)
