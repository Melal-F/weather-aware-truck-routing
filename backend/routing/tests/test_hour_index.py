import unittest
from datetime import datetime, timedelta, timezone

from routing.services import hour_index


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
