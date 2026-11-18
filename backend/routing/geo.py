"""Walk a driven path in miles and place weather checkpoints on it."""

import math
from datetime import timedelta

EARTH_MILES = 3958.7613


def haversine_miles(a, b):
    lat1, lng1 = math.radians(a[0]), math.radians(a[1])
    lat2, lng2 = math.radians(b[0]), math.radians(b[1])
    dlat = lat2 - lat1
    dlng = lng2 - lng1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 2 * EARTH_MILES * math.asin(min(1.0, math.sqrt(h)))


def cumulative_miles(coords):
    miles = [0.0]
    for start, end in zip(coords, coords[1:]):
        miles.append(miles[-1] + haversine_miles(start, end))
    return miles


def point_at_mile(coords, miles, target):
    if target <= 0 or len(coords) == 1:
        return coords[0]
    if target >= miles[-1]:
        return coords[-1]
    for index in range(1, len(miles)):
        if miles[index] >= target:
            span = miles[index] - miles[index - 1]
            fraction = 0.0 if span == 0 else (target - miles[index - 1]) / span
            lat = coords[index - 1][0] + fraction * (coords[index][0] - coords[index - 1][0])
            lng = coords[index - 1][1] + fraction * (coords[index][1] - coords[index - 1][1])
            return (lat, lng)
    return coords[-1]


def sample_miles(total, spacing):
    if total <= 0:
        return [0.0]
    marks = []
    mile = 0.0
    while mile < total - 1e-6:
        marks.append(mile)
        mile += spacing
    if not marks or abs(marks[-1] - total) > 1e-6:
        marks.append(total)
    return marks


def eta_at(departure, duration_seconds, mile, total):
    if total <= 0:
        return departure
    fraction = min(1.0, max(0.0, mile / total))
    return departure + timedelta(seconds=duration_seconds * fraction)


def time_at_coords(coords, spans, total_duration):
    """Seconds from the start of the route at each coordinate.

    `spans` are ORS step records (start index, end index, duration seconds).
    Duration is spread along each step by distance. With no steps, time follows
    distance across the whole route.
    """
    count = len(coords)
    if count == 0:
        return []
    miles = cumulative_miles(coords)
    if not spans:
        total = miles[-1]
        if total <= 0:
            return [0.0] * count
        return [total_duration * (mile / total) for mile in miles]

    seconds = [None] * count
    seconds[0] = 0.0
    for start, end, duration in spans:
        start = max(0, min(count - 1, int(start)))
        end = max(0, min(count - 1, int(end)))
        if end < start:
            start, end = end, start
        base = seconds[start] if seconds[start] is not None else 0.0
        if end == start:
            seconds[start] = base + float(duration)
            continue
        piece = cumulative_miles(coords[start : end + 1])
        span_miles = piece[-1]
        for offset, mile in enumerate(piece):
            fraction = 0.0 if span_miles == 0 else mile / span_miles
            seconds[start + offset] = base + float(duration) * fraction
    last = 0.0
    for index in range(count):
        if seconds[index] is None or seconds[index] < last:
            seconds[index] = last
        last = seconds[index]
    return seconds


def seconds_at_mile(coords, seconds, mile):
    miles = cumulative_miles(coords)
    if not miles:
        return 0.0
    if mile <= 0:
        return seconds[0]
    if mile >= miles[-1]:
        return seconds[-1]
    for index in range(1, len(miles)):
        if miles[index] >= mile:
            span = miles[index] - miles[index - 1]
            fraction = 0.0 if span == 0 else (mile - miles[index - 1]) / span
            return seconds[index - 1] + fraction * (seconds[index] - seconds[index - 1])
    return seconds[-1]


def place_checkpoints(coords, spacing_miles, departure, duration_seconds, time_seconds=None):
    miles = cumulative_miles(coords)
    total = miles[-1]
    if time_seconds is None or len(time_seconds) != len(coords):
        time_seconds = time_at_coords(coords, [], duration_seconds)
    placed = []
    for mile in sample_miles(total, spacing_miles):
        lat, lng = point_at_mile(coords, miles, mile)
        placed.append(
            {
                "mile": mile,
                "lat": lat,
                "lng": lng,
                "eta": departure + timedelta(seconds=seconds_at_mile(coords, time_seconds, mile)),
            }
        )
    return placed


def _with_miles(coords):
    miles = cumulative_miles(coords)
    return [{"lat": lat, "lng": lng, "mile": mile} for (lat, lng), mile in zip(coords, miles)]


def color_segments(coords, checkpoints):
    """Split the path so each piece carries the level of the checkpoint that opens it."""
    if len(coords) < 2 or not checkpoints:
        return []
    points = _with_miles(coords)
    existing = {round(point["mile"], 4) for point in points}
    for checkpoint in checkpoints:
        key = round(checkpoint["mile"], 4)
        if key not in existing:
            points.append(
                {
                    "lat": checkpoint["lat"],
                    "lng": checkpoint["lng"],
                    "mile": checkpoint["mile"],
                }
            )
            existing.add(key)
    points.sort(key=lambda point: point["mile"])

    ordered = sorted(checkpoints, key=lambda item: item["mile"])

    def level_at(mile):
        chosen = ordered[0].get("level")
        for checkpoint in ordered:
            if checkpoint["mile"] <= mile + 1e-6:
                chosen = checkpoint.get("level")
            else:
                break
        return chosen

    segments = []
    for start, end in zip(points, points[1:]):
        if end["mile"] - start["mile"] <= 1e-9:
            continue
        level = level_at(start["mile"])
        piece = [[start["lat"], start["lng"]], [end["lat"], end["lng"]]]
        if segments and segments[-1]["level"] == level:
            segments[-1]["positions"].append(piece[1])
        else:
            segments.append({"level": level, "positions": piece})
    return segments


def corridor_samples(routes, spacing_miles, cap=200):
    """Points along every route. Thinned to `cap` so a long haul stays one weather call."""
    sampled = []
    for coords in routes:
        if len(coords) < 2:
            continue
        miles = cumulative_miles(coords)
        for mile in sample_miles(miles[-1], spacing_miles):
            lat, lng = point_at_mile(coords, miles, mile)
            sampled.append((lat, lng))
    if len(sampled) <= cap:
        return sampled
    if cap <= 1:
        return sampled[:1]
    step = (len(sampled) - 1) / (cap - 1)
    return [sampled[round(index * step)] for index in range(cap)]
