"""Weather risk bands from the dispatch brief.

Band edges are inclusive on the lower bound. No Travel starts strictly above
the top of Severe for rain and snow, and at 55 mph for wind. Load changes
wind only, and it is applied before a checkpoint takes the worst of wind,
rain, and snow.
"""

LEVELS = ("Low", "Moderate", "High", "Severe", "No Travel")
RANK = {name: index + 1 for index, name in enumerate(LEVELS)}

# A route with no scored miles must not sort as the safest option.
UNSCORED_AVERAGE = 99


def band_wind(mph):
    if mph < 25:
        return "Low"
    if mph < 35:
        return "Moderate"
    if mph < 45:
        return "High"
    if mph < 55:
        return "Severe"
    return "No Travel"


def apply_load(mph, load_lb, level):
    if 45 <= mph < 55 and load_lb > 30000:
        return "No Travel"
    if 35 <= mph < 45 and load_lb > 40000:
        return "Severe"
    return level


def band_rain(inches_per_hour):
    if inches_per_hour < 0.10:
        return "Low"
    if inches_per_hour < 0.25:
        return "Moderate"
    if inches_per_hour < 0.50:
        return "High"
    if inches_per_hour <= 1.00:
        return "Severe"
    return "No Travel"


def band_snow(inches_per_hour):
    if inches_per_hour < 0.5:
        return "Low"
    if inches_per_hour < 1.0:
        return "Moderate"
    if inches_per_hour < 2.0:
        return "High"
    if inches_per_hour <= 3.0:
        return "Severe"
    return "No Travel"


def worst(levels):
    present = [level for level in levels if level is not None]
    if not present:
        return None
    return max(present, key=lambda level: RANK[level])


def classify(wind_mph, rain_in_hr, snow_in_hr, load_lb):
    wind_level = rain_level = snow_level = None
    parts = []
    if wind_mph is not None:
        wind_level = apply_load(wind_mph, load_lb, band_wind(wind_mph))
        parts.append(wind_level)
    if rain_in_hr is not None:
        rain_level = band_rain(rain_in_hr)
        parts.append(rain_level)
    if snow_in_hr is not None:
        snow_level = band_snow(snow_in_hr)
        parts.append(snow_level)
    return {
        "wind_level": wind_level,
        "rain_level": rain_level,
        "snow_level": snow_level,
        "level": worst(parts),
    }


def mm_to_inches(mm):
    if mm is None:
        return None
    return mm / 25.4


def cm_to_inches(cm):
    if cm is None:
        return None
    return cm / 2.54


UNASSESSED = "Unassessed"


def miles_by_level(checkpoints):
    totals = {name: 0.0 for name in (*LEVELS, UNASSESSED)}
    for start, end in zip(checkpoints, checkpoints[1:]):
        level = worst((start.get("level"), end.get("level")))
        if level is None:
            totals[UNASSESSED] += end["mile"] - start["mile"]
            continue
        totals[level] += end["mile"] - start["mile"]
    return totals


def average_risk(miles):
    """Distance-weighted mean on Low=1 … No Travel=5. Unassessed miles are left out."""
    total = sum(miles.get(name, 0.0) for name in LEVELS)
    if total <= 0:
        return None
    return sum(RANK[name] * miles.get(name, 0.0) for name in LEVELS) / total


def safe_to_recommend(route):
    miles = route["miles"]
    return miles.get("No Travel", 0.0) <= 1e-9 and miles.get(UNASSESSED, 0.0) <= 1e-9


def format_duration(seconds):
    whole = max(0, int(float(seconds) + 0.5))
    hours = whole // 3600
    minutes = int((whole % 3600) / 60 + 0.5)
    if minutes == 60:
        hours += 1
        minutes = 0
    if hours <= 0:
        return f"{minutes} min"
    return f"{hours} h {minutes} min"


def route_sort_key(route, index):
    average = route["average_risk"]
    if average is None:
        average = UNSCORED_AVERAGE
    return (
        route["miles"]["Severe"],
        route["miles"]["High"],
        average,
        route["duration_seconds"],
        index,
    )


def _eligible_indexes(routes):
    return [index for index in range(len(routes)) if safe_to_recommend(routes[index])]


def _route_letter(index):
    return chr(ord("A") + index)


def _mile_text(value):
    text = f"{value:.1f}"
    return text[:-2] if text.endswith(".0") else text


def _clock(seconds):
    return format_duration(seconds).replace(" min", " m")


def _hazard(route, level):
    """Name the weather behind a level when every such stretch agrees."""
    points = route.get("checkpoints") or []
    found = set()
    for start, end in zip(points, points[1:]):
        if worst((start.get("level"), end.get("level"))) != level:
            continue
        for point in (start, end):
            if point.get("rain_level") == level:
                found.add("rain")
            if point.get("snow_level") == level:
                found.add("snow")
            if point.get("wind_level") == level:
                found.add("wind")
    if found == {"rain"}:
        return f"{level} rain"
    if found == {"snow"}:
        return f"{level} snow"
    if found == {"wind"}:
        return f"{level} wind"
    return level


def _average(route):
    value = route.get("average_risk")
    return UNSCORED_AVERAGE if value is None else value


def _faster_lost_reason(routes, winner, winner_index, pool):
    """Name a quicker route that lost on weather, when there is one."""
    faster = [
        index
        for index in pool
        if index != winner_index and routes[index]["duration_seconds"] + 1e-9 < winner["duration_seconds"]
    ]
    if not faster:
        return None
    lost_index = min(faster, key=lambda index: (routes[index]["duration_seconds"], index))
    lost = routes[lost_index]
    if lost["miles"].get("No Travel", 0) > winner["miles"].get("No Travel", 0) + 1e-9:
        return None
    letter = _route_letter(lost_index)
    clock = _clock(lost["duration_seconds"])
    if lost["miles"]["Severe"] > winner["miles"]["Severe"] + 1e-9:
        return f"Avoids {_mile_text(lost['miles']['Severe'])} mi of {_hazard(lost, 'Severe')} on Route {letter} ({clock})"
    if lost["miles"]["High"] > winner["miles"]["High"] + 1e-9:
        return f"Avoids {_mile_text(lost['miles']['High'])} mi of {_hazard(lost, 'High')} on Route {letter} ({clock})"
    if _average(winner) + 1e-9 < _average(lost):
        return f"Lower average risk than Route {letter}: {_average(winner):.2f} vs {_average(lost):.2f}"
    return None


def recommendation_reason(routes):
    """Why the safe route won, with its number against the next safe route."""
    if not routes:
        return None
    order = sorted(_eligible_indexes(routes), key=lambda index: route_sort_key(routes[index], index))
    if not order:
        return "No safe route is available."
    if len(order) == 1:
        return "Only route returned." if len(routes) == 1 else "Only safe route."
    winner = routes[order[0]]
    named = _faster_lost_reason(routes, winner, order[0], order)
    if named:
        return named
    runner_up = routes[order[1]]

    def miles(best, other):
        return f"{best:.1f} vs {other:.1f} mi"

    checks = (
        (lambda route: route["miles"]["Severe"], "Fewest Severe miles", miles),
        (lambda route: route["miles"]["High"], "Fewest High miles", miles),
        (_average, "Lowest average risk", lambda best, other: f"{best:.2f} vs {other:.2f}"),
        (
            lambda route: route["duration_seconds"],
            "Shortest travel time",
            lambda best, other: f"{format_duration(best)} vs {format_duration(other)}",
        ),
    )
    for measure, label, render in checks:
        best = measure(winner)
        other = measure(runner_up)
        if best < other - 1e-9:
            return f"{label}: {render(best, other)}"
    return "Tied on risk and time; kept the first route the routing service returned."


def rank_routes(routes):
    """Mark one safe route recommended, or none.

    A route with No Travel or unassessed miles is not eligible. Eligible
    routes sort by Severe miles, High miles, average risk, travel time,
    then the provider's original order.
    """
    if not routes:
        return routes

    pool = _eligible_indexes(routes)
    best = min(pool, key=lambda index: route_sort_key(routes[index], index)) if pool else None
    for index, route in enumerate(routes):
        route["recommended"] = index == best
    return routes
