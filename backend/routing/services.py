"""Geocoding, three driving routes, and one forecast for every sample point."""

import math
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import requests
from django.core.cache import cache

from routing.geo import (
    color_segments,
    corridor_samples,
    cumulative_miles,
    haversine_miles,
    place_checkpoints,
    time_at_coords,
)
from routing.risk import (
    average_risk,
    classify,
    cm_to_inches,
    miles_by_level,
    mm_to_inches,
    rank_routes,
    recommendation_reason,
)

SPACINGS = (10, 25, 50)
FORECAST_HOURS = 49
WEATHER_CHUNK = 100
# ORS rejects alternative_routes once the crow-flies trip is longer than this.
ALTERNATIVES_MAX_KM = 90
VIA_FRACTIONS = (0.08, 0.15, 0.25)
VIA_OFFSET_CAP_KM = 120
DETOUR_LIMIT = 1.6
# Empty tractor-trailer. ORS weight is the gross vehicle weight, not the cargo.
TARE_LB = 35000
LB_TO_TONNES = 0.00045359237
# ORS stores HGV weight in hundredths of a tonne in a 16-bit field (0–327.67).
ORS_MAX_WEIGHT_TONNES = 327.67
KM_PER_MILE = 1.609344
ORS_URL = "https://api.openrouteservice.org/v2/directions/{profile}/geojson"


class PlanError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def parse_departure(value):
    if not isinstance(value, str) or not value.strip():
        raise PlanError("Departure date and time is required.")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise PlanError("Departure must be an ISO date and time.") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_place(value, label):
    if not isinstance(value, dict):
        raise PlanError(f"{label} is required.")
    name = str(value.get("name") or "").strip()
    if not name:
        raise PlanError(f"{label} needs a name. Pick a suggestion.")
    try:
        lat = float(value["lat"])
        lng = float(value["lng"])
    except (KeyError, TypeError, ValueError):
        raise PlanError(f"{label} needs a latitude and longitude. Pick a suggestion.") from None
    if not (math.isfinite(lat) and math.isfinite(lng) and -90 <= lat <= 90 and -180 <= lng <= 180):
        raise PlanError(f"{label} is outside the map.")
    return {"name": name, "lat": lat, "lng": lng}


def parse_load(value):
    if isinstance(value, bool) or value is None or value == "":
        raise PlanError("Load weight is required.")
    try:
        load = float(value)
    except (TypeError, ValueError):
        raise PlanError("Load weight must be a number of pounds.") from None
    if not math.isfinite(load) or load <= 0:
        raise PlanError("Load weight must be greater than 0 pounds.")
    return load


def parse_spacing(value):
    if isinstance(value, bool) or isinstance(value, float) and not value.is_integer():
        raise PlanError("Spacing must be 10, 25, or 50 miles.")
    try:
        spacing = int(value)
    except (TypeError, ValueError):
        raise PlanError("Spacing must be 10, 25, or 50 miles.") from None
    if spacing not in SPACINGS:
        raise PlanError("Spacing must be 10, 25, or 50 miles.")
    return spacing


def floor_hour(moment):
    return moment.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def hour_index(row, moment):
    if not row.get("first"):
        return None
    i = int((floor_hour(moment) - row["first"]) // timedelta(hours=1))
    return i if 0 <= i < len(row["time"]) else None


def reading_at(hourly, moment):
    index = hour_index(hourly, moment)
    if index is None:
        raise PlanError(
            "The forecast does not cover that departure. Pick a time within the next week.",
            status=502,
        )

    def cell(name):
        values = hourly.get(name) or []
        if index >= len(values):
            return None
        value = values[index]
        if value is None:
            return None
        return float(value)

    return cell("wind_speed_10m"), mm_to_inches(cell("rain")), cm_to_inches(cell("snowfall"))


def _step_spans(properties):
    spans = []
    for segment in (properties or {}).get("segments") or []:
        for step in segment.get("steps") or []:
            way = step.get("way_points") or []
            duration = step.get("duration")
            if duration is None or len(way) < 2:
                continue
            spans.append((int(way[0]), int(way[1]), float(duration)))
    return spans


def routes_from_geojson(payload):
    routes = []
    for feature in payload.get("features") or []:
        geometry = feature.get("geometry") or {}
        raw = geometry.get("coordinates") or []
        if geometry.get("type") == "MultiLineString":
            raw = [point for line in raw for point in line]
        coords = []
        for point in raw:
            if not isinstance(point, (list, tuple)) or len(point) < 2:
                continue
            lng, lat = float(point[0]), float(point[1])
            coords.append((lat, lng))
        properties = feature.get("properties") or {}
        summary = properties.get("summary") or {}
        duration = summary.get("duration")
        if duration is None or len(coords) < 2:
            continue
        duration = float(duration)
        routes.append(
            {
                "coords": coords,
                "duration_seconds": duration,
                "time_at_coords": time_at_coords(coords, _step_spans(properties), duration),
            }
        )
        if len(routes) == 3:
            break
    return routes


def _ors_message(response):
    try:
        body = response.json()
    except ValueError:
        return "The routing service rejected the request."
    error = body.get("error")
    if isinstance(error, dict) and error.get("message"):
        return str(error["message"])
    if isinstance(error, str) and error:
        return error
    return "The routing service rejected the request."


def weight_tonnes(load_lb):
    if load_lb is None:
        return None
    gross_lb = float(load_lb) + TARE_LB
    tonnes = min(gross_lb * LB_TO_TONNES, ORS_MAX_WEIGHT_TONNES)
    return round(tonnes, 2)


def _trip_km(origin, destination):
    miles = haversine_miles(
        (origin["lat"], origin["lng"]),
        (destination["lat"], destination["lng"]),
    )
    return miles * KM_PER_MILE


def via_points(origin, destination):
    """Points beside the crow-flies line, nearest offset first, both sides."""
    mid_lat = (origin["lat"] + destination["lat"]) / 2
    mid_lng = (origin["lng"] + destination["lng"]) / 2
    north_km = (destination["lat"] - origin["lat"]) * 110.574
    east_km = (destination["lng"] - origin["lng"]) * 111.320 * math.cos(math.radians(mid_lat))
    length = math.hypot(north_km, east_km) or 1.0
    side_east = -north_km / length
    side_north = east_km / length
    points = []
    for fraction in VIA_FRACTIONS:
        offset = min(length * fraction, VIA_OFFSET_CAP_KM)
        for sign in (1, -1):
            east = side_east * offset * sign
            north = side_north * offset * sign
            lat = mid_lat + north / 110.574
            cosine = math.cos(math.radians(mid_lat)) or 1.0
            lng = mid_lng + east / (111.320 * cosine)
            points.append({"lat": lat, "lng": lng})
    return points


def _routes_differ(left, right):
    samples = (0.2, 0.4, 0.6, 0.8)
    gaps = []
    for fraction in samples:
        a = left[min(len(left) - 1, int(fraction * (len(left) - 1)))]
        b = right[min(len(right) - 1, int(fraction * (len(right) - 1)))]
        gaps.append(haversine_miles(a, b))
    return sum(gaps) / len(gaps) > 5


def _keep_distinct(kept, candidate):
    for existing in kept:
        if not _routes_differ(existing["coords"], candidate["coords"]):
            return False
    kept.append(candidate)
    return True


def _ors_body(coordinates, load_lb, alternatives):
    # -1 lets ORS snap a point that is not on a road to the nearest road.
    body = {"coordinates": coordinates, "radiuses": [-1] * len(coordinates)}
    if alternatives:
        body["alternative_routes"] = {
            "target_count": 3,
            "share_factor": 0.6,
            "weight_factor": 1.4,
        }
    tonnes = weight_tonnes(load_lb)
    if tonnes is not None:
        body["options"] = {"profile_params": {"restrictions": {"weight": tonnes}}}
    return body


def _ors_headers(key):
    return {
        "Authorization": key,
        "Content-Type": "application/json",
        "Accept": "application/json, application/geo+json",
    }


def _ors_call(profile, coordinates, load_lb, key, alternatives):
    """Returns (routes, status) where status is ok, refused, unauthorized, or error."""
    response = requests.post(
        ORS_URL.format(profile=profile),
        json=_ors_body(coordinates, load_lb if profile == "driving-hgv" else None, alternatives),
        headers=_ors_headers(key),
        timeout=15,
    )
    if response.status_code in (400, 403, 404):
        return [], "refused"
    if response.status_code == 401:
        return [], "unauthorized"
    if response.status_code >= 400:
        raise PlanError(_ors_message(response), status=502)
    return routes_from_geojson(response.json()), "ok"


def _prune_vias(kept):
    if not kept:
        return
    limit = DETOUR_LIMIT * min(route["duration_seconds"] for route in kept)
    kept[:] = [
        route
        for route in kept
        if not route.get("via") or route["duration_seconds"] <= limit
    ]


def _direct_routes(profile, coordinates, load_lb, key, alternatives):
    """One direct request. A 400 that only happens with alternative_routes is retried without it."""
    routes, status = _ors_call(profile, coordinates, load_lb, key, alternatives)
    if status == "unauthorized":
        raise PlanError("OpenRouteService rejected the API key.", status=502)
    if alternatives and status == "refused":
        routes, status = _ors_call(profile, coordinates, load_lb, key, False)
        if status == "unauthorized":
            raise PlanError("OpenRouteService rejected the API key.", status=502)
    return routes, status


def ors_routes(origin, destination, load_lb=None):
    key = os.environ.get("ORS_API_KEY", "").strip()
    if not key:
        raise PlanError(
            "ORS_API_KEY is not set. Add a free OpenRouteService key and restart.",
            status=500,
        )
    trip_km = _trip_km(origin, destination)
    long_trip = trip_km > ALTERNATIVES_MAX_KM
    direct = [
        [origin["lng"], origin["lat"]],
        [destination["lng"], destination["lat"]],
    ]
    profile = "driving-hgv"
    routes, status = _direct_routes(profile, direct, load_lb, key, alternatives=not long_trip)
    # Car only when HGV itself was refused on a body that did not ask for alternatives.
    if status == "refused" or (not routes and not long_trip):
        profile = "driving-car"
        routes, status = _direct_routes(profile, direct, None, key, alternatives=not long_trip)
        if status == "refused" or (not routes and not long_trip):
            raise PlanError("The routing service rejected the request.", status=502)
    kept = []
    for route in routes or []:
        _keep_distinct(kept, route)
    if long_trip and len(kept) < 3:
        paths = [
            [
                [origin["lng"], origin["lat"]],
                [via["lng"], via["lat"]],
                [destination["lng"], destination["lat"]],
            ]
            for via in via_points(origin, destination)
        ]

        def fetch_via(path):
            try:
                extra, extra_status = _ors_call(profile, path, load_lb, key, False)
            except (PlanError, requests.RequestException):
                return None
            if extra_status != "ok" or not extra:
                return None
            return extra[0]

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(fetch_via, path): index for index, path in enumerate(paths)}
            candidates = [None] * len(paths)
            for future, index in futures.items():
                candidates[index] = future.result()
        for candidate in candidates:
            if len(kept) >= 3:
                break
            if candidate is None:
                continue
            if kept and candidate["duration_seconds"] > DETOUR_LIMIT * min(
                route["duration_seconds"] for route in kept
            ):
                continue
            candidate["via"] = True
            if _keep_distinct(kept, candidate):
                _prune_vias(kept)
    if not kept:
        raise PlanError("No driving route was found between those places.", status=502)
    for route in kept:
        route.pop("via", None)
        route["profile"] = profile
    return kept[:3]


def ors_autocomplete(query):
    text = (query or "").strip()
    if len(text) < 3:
        return []
    cache_key = "geo:" + text.lower()
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    key = os.environ.get("ORS_API_KEY", "").strip()
    if not key:
        raise PlanError(
            "ORS_API_KEY is not set. Add a free OpenRouteService key and restart.",
            status=500,
        )
    response = requests.get(
        "https://api.openrouteservice.org/geocode/autocomplete",
        params={"api_key": key, "text": text},
        timeout=10,
    )
    if response.status_code in (401, 403):
        raise PlanError("OpenRouteService rejected the API key.", status=502)
    if response.status_code == 429:
        raise PlanError("Geocoding is busy. Wait a second and try again.", status=429)
    if response.status_code >= 400:
        raise PlanError("Geocoding is unavailable right now.", status=502)
    try:
        payload = response.json()
    except ValueError:
        raise PlanError("Geocoding is unavailable right now.", status=502) from None
    features = payload.get("features") if isinstance(payload, dict) else None
    if not isinstance(features, list):
        features = []
    found = []
    for feature in features[:5]:
        properties = feature.get("properties") if isinstance(feature, dict) else None
        geometry = feature.get("geometry") if isinstance(feature, dict) else None
        label = properties.get("label") if isinstance(properties, dict) else None
        coords = geometry.get("coordinates") if isinstance(geometry, dict) else None
        if not isinstance(label, str) or not label:
            continue
        if not isinstance(coords, (list, tuple)) or len(coords) < 2:
            continue
        lng, lat = coords[0], coords[1]
        if type(lng) not in (int, float) or type(lat) not in (int, float):
            continue
        found.append({"name": label, "lat": lat, "lng": lng})
    cache.set(cache_key, found, 600)
    return found


def _forecast_chunk(chunk, start, end):
    response = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": ",".join(f"{lat:.5f}" for lat, _lng in chunk),
            "longitude": ",".join(f"{lng:.5f}" for _lat, lng in chunk),
            "hourly": "wind_speed_10m,rain,showers,snowfall",
            "wind_speed_unit": "mph",
            "timezone": "GMT",
            "start_date": start.date().isoformat(),
            "end_date": end.date().isoformat(),
        },
        timeout=20,
    )
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if response.status_code >= 400 or not isinstance(payload, (dict, list)):
        reason = "The weather service rejected the forecast request."
        if isinstance(payload, dict) and payload.get("reason"):
            reason = str(payload["reason"])
        raise PlanError(reason, status=502)
    if isinstance(payload, dict) and payload.get("error"):
        raise PlanError(payload.get("reason") or "The weather service rejected the forecast request.", status=502)
    chunk_rows = payload if isinstance(payload, list) else [payload]
    if len(chunk_rows) != len(chunk):
        raise PlanError("The weather service returned an unexpected forecast.", status=502)
    rows = []
    for row in chunk_rows:
        hourly = row.get("hourly") or {}
        rain_mm = hourly.get("rain") or []
        showers_mm = hourly.get("showers") or []
        rain_total = []
        for index in range(max(len(rain_mm), len(showers_mm))):
            steady = rain_mm[index] if index < len(rain_mm) else None
            shower = showers_mm[index] if index < len(showers_mm) else None
            if steady is None and shower is None:
                rain_total.append(None)
            else:
                rain_total.append((steady or 0) + (shower or 0))
        times = hourly.get("time") or []
        rows.append(
            {
                "time": times,
                "first": datetime.fromisoformat(times[0]).replace(tzinfo=timezone.utc) if times else None,
                "wind_speed_10m": hourly.get("wind_speed_10m") or [],
                "rain": rain_total,
                "snowfall": hourly.get("snowfall") or [],
            }
        )
    return rows


def open_meteo(points, start, end):
    """Hourly wind, rain, and snow for every point.

    One request covers up to 100 locations. A longer sample list takes another
    request only because the forecast URL cannot hold it.
    """
    if not points:
        return []
    chunks = [points[offset : offset + WEATHER_CHUNK] for offset in range(0, len(points), WEATHER_CHUNK)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(_forecast_chunk, chunk, start, end): index for index, chunk in enumerate(chunks)}
        parts = [None] * len(chunks)
        for future, index in futures.items():
            parts[index] = future.result()
    return [row for part in parts for row in part]


def _point_key(lat, lng):
    return (round(lat, 4), round(lng, 4))


def _route_note(count):
    if count >= 3:
        return None
    noun = "route" if count == 1 else "routes"
    return f"The routing service returned {count} {noun}."


def build_plan(payload, fetch_routes, fetch_weather):
    if not isinstance(payload, dict):
        raise PlanError("Request body must be a JSON object.")
    origin = parse_place(payload.get("origin"), "Origin")
    destination = parse_place(payload.get("destination"), "Destination")
    departure = parse_departure(payload.get("departure"))
    load_lb = parse_load(payload.get("load_lb"))
    spacing = parse_spacing(payload.get("spacing_miles"))
    if abs(origin["lat"] - destination["lat"]) < 1e-5 and abs(origin["lng"] - destination["lng"]) < 1e-5:
        raise PlanError("Origin and destination are the same place.")

    raw_routes = fetch_routes(origin, destination, load_lb) or []
    drafts = []
    for raw in raw_routes[:3]:
        coords = raw.get("coords") or []
        if len(coords) < 2:
            continue
        duration = float(raw["duration_seconds"])
        checkpoints = place_checkpoints(
            coords,
            spacing,
            departure,
            duration,
            raw.get("time_at_coords"),
        )
        drafts.append(
            {
                "coords": coords,
                "duration_seconds": duration,
                "distance_miles": cumulative_miles(coords)[-1],
                "checkpoints": checkpoints,
                "profile": raw.get("profile"),
            }
        )
    if not drafts:
        raise PlanError("No driving route was found between those places.", status=502)

    points = []
    seen = set()

    def add_point(lat, lng):
        key = _point_key(lat, lng)
        if key in seen:
            return
        seen.add(key)
        points.append((lat, lng))

    for draft in drafts:
        for checkpoint in draft["checkpoints"]:
            add_point(checkpoint["lat"], checkpoint["lng"])
    corridor = corridor_samples([draft["coords"] for draft in drafts], spacing)
    for lat, lng in corridor:
        add_point(lat, lng)

    start = floor_hour(departure)
    latest = max(checkpoint["eta"] for draft in drafts for checkpoint in draft["checkpoints"])
    end = floor_hour(max(departure + timedelta(hours=48), latest))
    series = fetch_weather(points, start, end)
    if len(series) != len(points):
        raise PlanError("The weather service returned an unexpected forecast.", status=502)
    for row in series:
        if not row.get("first"):
            times = row.get("time") or []
            row["first"] = datetime.fromisoformat(times[0]).replace(tzinfo=timezone.utc) if times else None
    if not series or hour_index(series[0], start) is None or hour_index(series[0], start + timedelta(hours=48)) is None:
        raise PlanError(
            "The forecast does not cover that departure. Pick a time within the next week.",
            status=502,
        )
    by_key = {_point_key(lat, lng): hourly for (lat, lng), hourly in zip(points, series)}

    def weather(lat, lng, moment):
        return reading_at(by_key[_point_key(lat, lng)], moment)

    routes = []
    for index, draft in enumerate(drafts):
        checkpoints = []
        for checkpoint in draft["checkpoints"]:
            wind, rain, snow = weather(checkpoint["lat"], checkpoint["lng"], checkpoint["eta"])
            scored = classify(wind, rain, snow, load_lb)
            checkpoints.append(
                {
                    "mile": checkpoint["mile"],
                    "lat": checkpoint["lat"],
                    "lng": checkpoint["lng"],
                    "eta": checkpoint["eta"],
                    "wind_mph": wind,
                    "rain_in_hr": rain,
                    "snow_in_hr": snow,
                    **scored,
                }
            )
        miles = miles_by_level(checkpoints)
        routes.append(
            {
                "id": index,
                "duration_seconds": draft["duration_seconds"],
                "distance_miles": draft["distance_miles"],
                "arrival": draft["checkpoints"][-1]["eta"],
                "checkpoints": checkpoints,
                "segments": color_segments(draft["coords"], checkpoints),
                "miles": miles,
                "average_risk": average_risk(miles),
                "profile": draft.get("profile"),
            }
        )
    rank_routes(routes)
    reason = recommendation_reason(routes)
    for route in routes:
        route["recommendation_reason"] = reason if route["recommended"] else None

    corridor_out = []
    for lat, lng in corridor:
        risks = []
        for hour in range(FORECAST_HOURS):
            wind, rain, snow = weather(lat, lng, start + timedelta(hours=hour))
            risks.append(classify(wind, rain, snow, load_lb)["level"])
        corridor_out.append({"lat": lat, "lng": lng, "risks": risks})

    return {
        "departure": departure.isoformat(),
        "forecast_start": start.isoformat(),
        "load_lb": load_lb,
        "spacing_miles": spacing,
        "origin": origin,
        "destination": destination,
        "note": _route_note(len(routes)),
        "routes": [_public_route(route) for route in routes],
        "corridor": corridor_out,
    }


def _round(value, places):
    if value is None:
        return None
    return round(value, places)


def _public_route(route):
    return {
        "id": route["id"],
        "recommended": route["recommended"],
        "recommendation_reason": route.get("recommendation_reason"),
        "distance_miles": _round(route["distance_miles"], 2),
        "duration_seconds": round(route["duration_seconds"]),
        "arrival": route["arrival"].isoformat(),
        "average_risk": _round(route["average_risk"], 3),
        "profile": route.get("profile"),
        "miles": {name: round(amount, 2) for name, amount in route["miles"].items()},
        "checkpoints": [
            {
                "mile": round(point["mile"], 2),
                "lat": point["lat"],
                "lng": point["lng"],
                "eta": point["eta"].isoformat(),
                "wind_mph": _round(point["wind_mph"], 1),
                "rain_in_hr": _round(point["rain_in_hr"], 3),
                "snow_in_hr": _round(point["snow_in_hr"], 3),
                "wind_level": point["wind_level"],
                "rain_level": point["rain_level"],
                "snow_level": point["snow_level"],
                "level": point["level"],
            }
            for point in route["checkpoints"]
        ],
        "segments": route["segments"],
    }
