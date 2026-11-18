# Weather-Aware Truck Routing

Live demo: https://YOUR-APP.onrender.com

A React and Django desk for a truck driver. Enter an origin, a destination, a departure time, and a load weight. The app asks OpenRouteService for up to three driving routes, samples weather along each one, and recommends the safest option.

## Run it locally

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
cp .env.example .env
# Put a free OpenRouteService key in .env as ORS_API_KEY

cd backend
export ORS_API_KEY=your-key
python manage.py runserver
```

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open http://127.0.0.1:5173. The dev server proxies `/api` to Django on port 8000.

Tests, from `backend`:

```bash
python -m unittest discover -s routing/tests -v
```

## Docker

```bash
export ORS_API_KEY=your-key
docker compose up --build
```

The site is on http://127.0.0.1:8000. Set `ALLOWED_HOSTS` to the public hostname when you deploy that image.

`ORS_API_KEY` is an OpenRouteService key. Geocoding uses ORS autocomplete with the same `ORS_API_KEY`. Open-Meteo and the map tiles do not need keys.

## What the driver sends

Origin and destination (a picked place, with a name and coordinates), departure date and time, load weight in pounds, and a checkpoint spacing of 10, 25, or 50 miles. The default spacing is 25. Departure is taken from the browser’s local clock and stored in UTC.

## Routes

The crow-flies distance is computed first. Up to 90 km, the app asks OpenRouteService for three alternatives on `driving-hgv`. Longer trips never send `alternative_routes`, because that request is rejected. They take the direct truck route, then fill up to three with routes through points 8%, 15%, and 25% of the straight-line length off to either side, nearest first. An offset is capped at 120 km. Every request sends `radiuses` of `-1` so a point that is not on a road snaps to the nearest one. A via route whose driving time is more than 1.6 times the fastest route already kept is dropped. A line is kept only when it stays more than about 5 miles from the ones already kept. The app still does not invent a line the service did not return. When fewer than three survive, the note on the page says so.

The HGV call sends gross weight in metric tonnes: the load plus `TARE_LB` (35,000 lb, an empty tractor-trailer) in `backend/routing/services.py`, times `0.00045359237`, capped at 327.67 t. That cap is the largest weight OpenRouteService stores (hundredths of a tonne in a 16-bit field). The app switches to `driving-car`, with no weight, only when `driving-hgv` itself is refused on a request that did not ask for alternatives. A 400 from an alternatives request is not treated as the truck profile being unavailable. Each route reports `profile`. A car route shows “Car profile (truck routing unavailable)” on its card.

Checkpoints sit on the driven path, not on a straight line between cities. The origin and the destination are always included. The last gap is shorter when the length is not a multiple of the spacing.

The ETA at a checkpoint follows the driving time OpenRouteService reports on each step, spread along that step by distance. If a route has no steps, time falls back to the whole-route duration scaled by distance. Weather for that checkpoint is the forecast hour that contains the ETA.

## Risk rules

One level per checkpoint: the worst of wind (after the load rules), rain, and snow. A missing reading is skipped. It is not treated as safe.

Wind (mph): under 25 Low, 25–34 Moderate, 35–44 High, 45–54 Severe, 55 or more No Travel.

Rain (in/hr): under 0.10 Low, 0.10 up to but not including 0.25 Moderate, 0.25 up to but not including 0.50 High, 0.50 through 1.00 Severe, over 1.00 No Travel.

Snow (in/hr): under 0.5 Low, 0.5 up to but not including 1.0 Moderate, 1.0 up to but not including 2.0 High, 2.0 through 3.0 Severe, over 3.0 No Travel.

Load changes wind only, and it is applied before the worst-of step:

- 55 mph or more is No Travel for any load.
- 45–54 mph and a load over 30,000 lb is No Travel. At 30,000 lb or under, that wind band stays Severe.
- 35–44 mph and a load over 40,000 lb is Severe. At 40,000 lb or under, that wind band stays High.

Rain and snow do not use the load cutoffs.

Miles of a level are the path miles from a checkpoint to the next one, tagged with the worse of the two checkpoints that bound it, so a hazard at either end counts.

Average risk is the distance-weighted mean of Low = 1, Moderate = 2, High = 3, Severe = 4, No Travel = 5.

## Which route is recommended

Best first:

1. Fewest No Travel miles
2. Fewest Severe miles
3. Fewest High miles
4. Lowest average risk
5. Shortest travel time
6. The order the routing service returned, if still tied

No Travel is sorted first so an impassable route cannot beat a route that is only severe. The brief’s four keys follow that, in the same order.

The recommended card states the first of those keys that actually separated the winner, with the winner’s number against the runner-up, for example “Fewest Severe miles: 0.0 vs 12.4 mi” or “Shortest travel time: 6 h 10 min vs 6 h 45 min.” A full tie keeps the first route the service returned.

## Units

The page and the API stay in miles, mph, inches per hour, and pounds. Open-Meteo wind is requested in mph. Rain = Open-Meteo rain + showers (mm/h) ÷ 25.4. Snow comes back in centimeters per hour and is divided by 2.54.

## The map and the slider

All routes are drawn at once, split into runs of one risk color. Each route keeps a summary label on the map (distance, travel time, Severe and High miles, average risk). Checkpoints are dots. Hovering a checkpoint shows the mile, the ETA, the combined risk, and the wind, rain, and snow readings. A click opens the same detail and selects that route. The panel lists distance, travel time, arrival, miles in each level, average risk, and a Recommended badge with the reason under it.

The corridor is a band, not a single line. For each sample along a route the forecast also covers points 10 and 20 miles to either side, perpendicular to the local heading. Samples are taken every 12 miles, ignoring slider spacing, and snapped to a 0.2° grid. Weather lookups for checkpoints and the corridor snap to 0.1° (about 11 km) and share one forecast per cell. A plan requests at most 350 unique locations; checkpoint cells are kept and corridor cells are thinned to fit. Forecasts are cached for 30 minutes, and Open-Meteo is still requested in batches of 100. On the map the band is radial gradients about 16 km across, wider in pixels as you zoom in, drawn from lower risk to higher so the worst level sits on top. Dragging the map only moves the existing canvas. The current hour is redrawn once the pan or zoom settles, or when the slider or Play changes the hour. Up to six hours already drawn for that view are kept and dropped when the view changes. Hide corridor turns the band off. None of that calls the network again, and checkpoint risk stays tied to arrival time. Hour 0 is the departure hour. Hour 48 is 48 hours later.

Colors: Low green, Moderate yellow, High orange, Severe red, No Travel black.

## Hosting

The repo is a Docker web service. The image builds the React app, then gunicorn serves that build and the Django API on port 8000. Routing, the via-point fallback, and the corridor forecast all run inside that process. The only extra configuration is the environment below. Render builds the root `Dockerfile`.

1. In Render, create a Web Service and connect `https://github.com/Melal-F/weather-aware-truck-routing`.
2. Language: Docker. Branch: `main`. Dockerfile path: `Dockerfile`.
3. Instance type can be the free web service. The first request after sleep takes a minute.
4. Environment variables:
   - `ORS_API_KEY` — free key from OpenRouteService
   - `DJANGO_SECRET_KEY` — a long random string
   - `DJANGO_DEBUG` — `0`
   - `ALLOWED_HOSTS` — the Render hostname, such as `weather-truck-routing.onrender.com`
5. Health check path: `/`.

`docker compose up --build` is the same image locally, on http://127.0.0.1:8000.

The walkthrough script is in [LOOM.md](LOOM.md).
