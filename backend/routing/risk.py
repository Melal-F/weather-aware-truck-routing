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


def miles_by_level(checkpoints):
    totals = {name: 0.0 for name in LEVELS}
    for start, end in zip(checkpoints, checkpoints[1:]):
        level = start.get("level")
        if level not in totals:
            continue
        totals[level] += end["mile"] - start["mile"]
    return totals


def average_risk(miles):
    total = sum(miles.values())
    if total <= 0:
        return None
    return sum(RANK[name] * miles[name] for name in LEVELS) / total


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


def recommendation_reason(routes):
    """Why the recommended route won, with its number against the runner-up."""
    winner = next((route for route in routes if route.get("recommended")), None)
    if winner is None:
        return None
    others = [route for route in routes if route is not winner]
    if not others:
        return "Only route returned."

    def average(route):
        value = route.get("average_risk")
        return UNSCORED_AVERAGE if value is None else value

    def miles(best, other):
        return f"{best:.1f} vs {other:.1f} mi"

    checks = (
        (lambda route: route["miles"]["No Travel"], "Fewest No Travel miles", miles),
        (lambda route: route["miles"]["Severe"], "Fewest Severe miles", miles),
        (lambda route: route["miles"]["High"], "Fewest High miles", miles),
        (average, "Lowest average risk", lambda best, other: f"{best:.2f} vs {other:.2f}"),
        (
            lambda route: route["duration_seconds"],
            "Shortest travel time",
            lambda best, other: f"{format_duration(best)} vs {format_duration(other)}",
        ),
    )
    for measure, label, render in checks:
        best = measure(winner)
        other = min(measure(route) for route in others)
        if best < other - 1e-9:
            return f"{label}: {render(best, other)}"
    return "Tied on risk and time; kept the first route the routing service returned."


def rank_routes(routes):
    """Mark exactly one route recommended. Lowest tuple wins.

    Order: No Travel miles, Severe miles, High miles, average risk,
    travel time, then the provider's original order.
    """
    if not routes:
        return routes

    def sort_key(index):
        route = routes[index]
        average = route["average_risk"]
        if average is None:
            average = UNSCORED_AVERAGE
        return (
            route["miles"]["No Travel"],
            route["miles"]["Severe"],
            route["miles"]["High"],
            average,
            route["duration_seconds"],
            index,
        )

    best = min(range(len(routes)), key=sort_key)
    for index, route in enumerate(routes):
        route["recommended"] = index == best
    return routes
