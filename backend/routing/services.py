"""Geocoding, three driving routes, and one forecast for every sample point."""

import math
import os
from datetime import datetime, timedelta, timezone

import requests

from routing.geo import (
    color_segments,
    corridor_samples,
    cumulative_miles,
    place_checkpoints,
)
from routing.risk import (
    average_risk,
    classify,
    cm_to_inches,
    miles_by_level,
    mm_to_inches,
    rank_routes,
)

SPACINGS = (10, 25, 50)
FORECAST_HOURS = 49
WEATHER_CHUNK = 100
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


def hour_token(moment):
    return floor_hour(moment).strftime("%Y-%m-%dT%H:%M")


def find_hour(times, moment):
    token = hour_token(moment)
    for index, raw in enumerate(times):
        if str(raw)[:16] == token:
            return index
    return None


def reading_at(hourly, moment):
    index = find_hour(hourly["time"], moment)
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
        summary = (feature.get("properties") or {}).get("summary") or {}
        duration = summary.get("duration")
        if duration is None or len(coords) < 2:
            continue
        routes.append({"coords": coords, "duration_seconds": float(duration)})
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


def ors_routes(origin, destination):
    key = os.environ.get("ORS_API_KEY", "").strip()
    if not key:
        raise PlanError(
            "ORS_API_KEY is not set. Add a free OpenRouteService key and restart.",
            status=500,
        )
    body = {
        "coordinates": [
            [origin["lng"], origin["lat"]],
            [destination["lng"], destination["lat"]],
        ],
        "alternative_routes": {
            "target_count": 3,
            "share_factor": 0.6,
            "weight_factor": 1.4,
        },
    }
    headers = {
        "Authorization": key,
        "Content-Type": "application/json",
        "Accept": "application/json, application/geo+json",
    }
    try_car = False
    for profile in ("driving-hgv", "driving-car"):
        if profile == "driving-car" and not try_car:
            break
        response = requests.post(
            ORS_URL.format(profile=profile),
            json=body,
            headers=headers,
            timeout=30,
        )
        if profile == "driving-hgv" and response.status_code in (400, 403, 404):
            try_car = True
            continue
        if response.status_code == 401:
            raise PlanError("OpenRouteService rejected the API key.", status=502)
        if response.status_code >= 400:
            raise PlanError(_ors_message(response), status=502)
        routes = routes_from_geojson(response.json())
        if routes:
            return routes
        if profile == "driving-hgv":
            try_car = True
            continue
    raise PlanError("No driving route was found between those places.", status=502)


def nominatim_search(query):
    text = (query or "").strip()
    if len(text) < 2:
        return []
    response = requests.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": text, "format": "jsonv2", "limit": 5},
        headers={
            "User-Agent": os.environ.get("NOMINATIM_USER_AGENT", "WeatherTruckRouting/1.0"),
            "Accept": "application/json",
        },
        timeout=15,
    )
    if response.status_code == 429:
        raise PlanError("Geocoding is busy. Wait a second and try again.", status=429)
    if response.status_code >= 400:
        raise PlanError("Geocoding is unavailable right now.", status=502)
    found = []
    for item in response.json():
        found.append(
            {
                "name": item.get("display_name") or text,
                "lat": float(item["lat"]),
                "lng": float(item["lon"]),
            }
        )
    return found


def open_meteo(points, start, end):
    """Hourly wind, rain, and snow for every point.

    One request covers up to 100 locations. A longer sample list takes another
    request only because the forecast URL cannot hold it.
    """
    if not points:
        return []
    rows = []
    for offset in range(0, len(points), WEATHER_CHUNK):
        chunk = points[offset : offset + WEATHER_CHUNK]
        response = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": ",".join(f"{lat:.5f}" for lat, _lng in chunk),
                "longitude": ",".join(f"{lng:.5f}" for _lat, lng in chunk),
                "hourly": "wind_speed_10m,rain,snowfall",
                "wind_speed_unit": "mph",
                "timezone": "GMT",
                "start_date": start.date().isoformat(),
                "end_date": end.date().isoformat(),
            },
            timeout=40,
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
        for row in chunk_rows:
            hourly = row.get("hourly") or {}
            rows.append(
                {
                    "time": hourly.get("time") or [],
                    "wind_speed_10m": hourly.get("wind_speed_10m") or [],
                    "rain": hourly.get("rain") or [],
                    "snowfall": hourly.get("snowfall") or [],
                }
            )
    return rows


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

    raw_routes = fetch_routes(origin, destination) or []
    drafts = []
    for raw in raw_routes[:3]:
        coords = raw.get("coords") or []
        if len(coords) < 2:
            continue
        duration = float(raw["duration_seconds"])
        checkpoints = place_checkpoints(coords, spacing, departure, duration)
        drafts.append(
            {
                "coords": coords,
                "duration_seconds": duration,
                "distance_miles": cumulative_miles(coords)[-1],
                "checkpoints": checkpoints,
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
    if not series or find_hour(series[0]["time"], start) is None or find_hour(series[0]["time"], start + timedelta(hours=48)) is None:
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
            }
        )
    rank_routes(routes)

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
        "distance_miles": _round(route["distance_miles"], 2),
        "duration_seconds": round(route["duration_seconds"]),
        "arrival": route["arrival"].isoformat(),
        "average_risk": _round(route["average_risk"], 3),
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
