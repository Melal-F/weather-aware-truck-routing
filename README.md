# Weather-Aware Truck Routing

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
export NOMINATIM_USER_AGENT="WeatherTruckRouting/1.0 (you@example.com)"
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

`ORS_API_KEY` is an OpenRouteService key. `NOMINATIM_USER_AGENT` should identify this app. Open-Meteo and the map tiles do not need keys.

## What the driver sends

Origin and destination (a picked place, with a name and coordinates), departure date and time, load weight in pounds, and a checkpoint spacing of 10, 25, or 50 miles. The default spacing is 25. Departure is taken from the browser’s local clock and stored in UTC.

## Routes

One OpenRouteService call asks for three alternatives on the `driving-hgv` profile. If that profile is refused, the same call is made with `driving-car`. The app does not invent a missing route. When fewer than three come back, the note on the page says so.

Checkpoints sit on the driven path, not on a straight line between cities. The origin and the destination are always included. The last gap is shorter when the length is not a multiple of the spacing.

The ETA at a checkpoint is the departure time plus the route duration scaled by distance along the path. Weather for that checkpoint is the forecast hour that contains the ETA.

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

Miles of a level are the path miles from a checkpoint to the next one, tagged with the level at the checkpoint where that stretch starts. The destination does not open a new stretch.

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

## Units

The page and the API stay in miles, mph, inches per hour, and pounds. Open-Meteo wind is requested in mph. Rain comes back in millimeters per hour and is divided by 25.4. Snow comes back in centimeters per hour and is divided by 2.54.

## The map and the slider

All routes are drawn at once, split into runs of one risk color. Checkpoints are dots. A popup shows the mile, the ETA, the combined risk, and the wind, rain, and snow readings with their own levels. The panel lists distance, travel time, arrival, miles in each level, average risk, and a Recommended badge. Click a card or a line to select that route.

The 0–48 hour slider is separate from checkpoint risk. Hour 0 is the departure hour, and hour 48 is 48 hours later. It recolors a corridor drawn along the routes from forecast data that was already loaded. Dragging it does not call the network again and does not change ETAs.

Colors: Low green, Moderate yellow, High orange, Severe red, No Travel black.

## Hosting

Build with Docker and run the image on any host that can store `ORS_API_KEY` and `ALLOWED_HOSTS`. The walkthrough script is in [LOOM.md](LOOM.md).
