import { useEffect, useRef, useState } from "react";
import MapView from "./MapView";
import {
  RISK_COLORS,
  RISK_LEVELS,
  formatDuration,
  formatWhen,
  geocode,
  planTrip,
} from "./api";

function defaultDeparture() {
  const next = new Date();
  next.setMinutes(0, 0, 0);
  next.setHours(next.getHours() + 1);
  const pad = (value) => String(value).padStart(2, "0");
  return `${next.getFullYear()}-${pad(next.getMonth() + 1)}-${pad(next.getDate())}T${pad(next.getHours())}:00`;
}

function PlaceField({ id, label, query, setQuery, pick, setPick }) {
  const [hits, setHits] = useState([]);
  const seq = useRef(0);

  useEffect(() => {
    if (pick || query.trim().length < 2) {
      setHits([]);
      return undefined;
    }
    const ticket = ++seq.current;
    const timer = setTimeout(async () => {
      try {
        const found = await geocode(query.trim());
        if (ticket === seq.current) setHits(found);
      } catch {
        if (ticket === seq.current) setHits([]);
      }
    }, 400);
    return () => clearTimeout(timer);
  }, [pick, query]);

  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        value={query}
        autoComplete="off"
        placeholder="City, address, or place"
        onChange={(event) => {
          setQuery(event.target.value);
          setPick(null);
        }}
      />
      {hits.length > 0 && (
        <ul className="suggestions">
          {hits.map((hit) => (
            <li key={`${hit.lat},${hit.lng}`}>
              <button
                type="button"
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => {
                  setPick(hit);
                  setQuery(hit.name);
                  setHits([]);
                }}
              >
                {hit.name}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function RouteCard({ route, selected, onSelect }) {
  const total = RISK_LEVELS.reduce((sum, level) => sum + (route.miles[level] || 0), 0);
  return (
    <button
      type="button"
      className={selected ? "card selected" : "card"}
      aria-pressed={selected}
      onClick={() => onSelect(route.id)}
    >
      <header>
        <strong>Route {route.id + 1}</strong>
        {route.recommended && <span className="badge">Recommended</span>}
      </header>
      {route.profile === "driving-car" && (
        <p className="muted">Car profile (truck routing unavailable)</p>
      )}
      {route.recommended && route.recommendation_reason && (
        <p className="reason">{route.recommendation_reason}</p>
      )}
      <p className="trip-line">
        {route.distance_miles.toFixed(1)} mi · {formatDuration(route.duration_seconds)}
      </p>
      <p>Arrive {formatWhen(route.arrival)}</p>
      <div className="bar" aria-hidden="true">
        {RISK_LEVELS.map((level) => (
          <span
            key={level}
            style={{
              width: total ? `${(route.miles[level] / total) * 100}%` : 0,
              background: RISK_COLORS[level],
            }}
          />
        ))}
      </div>
      <ul className="miles">
        {RISK_LEVELS.map((level) => (
          <li key={level}>
            <i style={{ background: RISK_COLORS[level] }} />
            <span>{level}</span>
            <b>{route.miles[level].toFixed(1)} mi</b>
          </li>
        ))}
      </ul>
      <p>
        Average risk {route.average_risk == null ? "—" : route.average_risk.toFixed(2)}
        <span className="muted"> · {route.checkpoints.length} checkpoints</span>
      </p>
    </button>
  );
}

async function resolvePlace(pick, query, label) {
  if (pick) return pick;
  const text = query.trim();
  if (text.length < 2) throw new Error(`${label} is required.`);
  const hits = await geocode(text);
  if (!hits.length) throw new Error(`No match for ${label.toLowerCase()}.`);
  return hits[0];
}

export default function App() {
  const [originQuery, setOriginQuery] = useState("");
  const [originPick, setOriginPick] = useState(null);
  const [destQuery, setDestQuery] = useState("");
  const [destPick, setDestPick] = useState(null);
  const [departure, setDeparture] = useState(defaultDeparture);
  const [load, setLoad] = useState("");
  const [spacing, setSpacing] = useState("25");
  const [plan, setPlan] = useState(null);
  const [selected, setSelected] = useState(null);
  const [hour, setHour] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [showHeat, setShowHeat] = useState(true);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!playing || !plan) return undefined;
    const timer = setInterval(() => {
      setHour((current) => (current >= 48 ? 0 : current + 1));
    }, 800);
    return () => clearInterval(timer);
  }, [playing, plan]);

  async function onSubmit(event) {
    event.preventDefault();
    setError("");
    const loadLb = Number(load);
    if (!Number.isFinite(loadLb) || loadLb <= 0) {
      setError("Load weight must be greater than 0 pounds.");
      return;
    }
    const when = new Date(departure);
    if (Number.isNaN(when.getTime())) {
      setError("Departure date and time is required.");
      return;
    }
    setLoading(true);
    try {
      const origin = await resolvePlace(originPick, originQuery, "Origin");
      const destination = await resolvePlace(destPick, destQuery, "Destination");
      setOriginPick(origin);
      setOriginQuery(origin.name);
      setDestPick(destination);
      setDestQuery(destination.name);
      const next = await planTrip({
        origin,
        destination,
        departure: when.toISOString(),
        load_lb: loadLb,
        spacing_miles: Number(spacing),
      });
      const winner = next.routes.find((route) => route.recommended) || next.routes[0];
      setPlan(next);
      setSelected(winner ? winner.id : null);
      setHour(0);
      setPlaying(false);
    } catch (err) {
      setError(err.message || "Could not plan that trip.");
    } finally {
      setLoading(false);
    }
  }

  const forecastLabel = plan
    ? formatWhen(new Date(new Date(plan.forecast_start).getTime() + hour * 3600000).toISOString())
    : "Plan a trip to color the corridor";

  return (
    <div className="shell">
      <header className="top">
        <div>
          <p className="eyebrow">Dispatch</p>
          <h1>Weather-Aware Truck Routing</h1>
        </div>
      </header>
      <div className="body">
        <aside>
          <form onSubmit={onSubmit}>
            <PlaceField
              id="origin"
              label="Origin"
              query={originQuery}
              setQuery={setOriginQuery}
              pick={originPick}
              setPick={setOriginPick}
            />
            <PlaceField
              id="destination"
              label="Destination"
              query={destQuery}
              setQuery={setDestQuery}
              pick={destPick}
              setPick={setDestPick}
            />
            <div className="split">
              <div className="field">
                <label htmlFor="departure">Departure</label>
                <input
                  id="departure"
                  type="datetime-local"
                  value={departure}
                  onChange={(event) => setDeparture(event.target.value)}
                  required
                />
              </div>
              <div className="field">
                <label htmlFor="load">Load weight (lb)</label>
                <input
                  id="load"
                  type="number"
                  min="1"
                  step="1"
                  inputMode="decimal"
                  placeholder="42000"
                  value={load}
                  onChange={(event) => setLoad(event.target.value)}
                  required
                />
              </div>
            </div>
            <div className="field">
              <label htmlFor="spacing">Weather every</label>
              <select id="spacing" value={spacing} onChange={(event) => setSpacing(event.target.value)}>
                <option value="10">10 miles</option>
                <option value="25">25 miles</option>
                <option value="50">50 miles</option>
              </select>
            </div>
            <button className="go" type="submit" disabled={loading}>
              {loading ? "Planning routes…" : plan ? "Update routes" : "Plan routes"}
            </button>
            {error && (
              <p className="error" role="alert">
                {error}
              </p>
            )}
          </form>
          {plan?.note && <p className="note">{plan.note}</p>}
          <div className="cards">
            {plan?.routes.map((route) => (
              <RouteCard
                key={route.id}
                route={route}
                selected={route.id === selected}
                onSelect={setSelected}
              />
            ))}
          </div>
        </aside>
        <div className="stage">
          <MapView plan={plan} selected={selected} hour={hour} onSelect={setSelected} showHeat={showHeat} />
          {!plan && <p className="empty">Routes, checkpoints, and the weather corridor show up here.</p>}
          <div
            className="slider"
            onPointerDown={(event) => event.stopPropagation()}
          >
            <div className="slider-top">
              <label htmlFor="hour">Corridor forecast</label>
              <strong>
                Hour {hour} of 48 · {forecastLabel}
              </strong>
            </div>
            <input
              id="hour"
              type="range"
              min="0"
              max="48"
              step="1"
              value={hour}
              disabled={!plan}
              onChange={(event) => setHour(Number(event.target.value))}
            />
            <div className="slider-actions">
              <button type="button" disabled={!plan} onClick={() => setPlaying((on) => !on)}>
                {playing ? "Pause" : "Play"}
              </button>
              <button type="button" disabled={!plan} onClick={() => setShowHeat((on) => !on)}>
                {showHeat ? "Hide corridor" : "Show corridor"}
              </button>
            </div>
            <p className="hint">
              Play walks the 48 hours on the corridor already loaded. Checkpoint risk stays tied to arrival time.
            </p>
            <ul className="legend">
              {RISK_LEVELS.map((level) => (
                <li key={level}>
                  <i style={{ background: RISK_COLORS[level] }} />
                  {level}
                </li>
              ))}
            </ul>
          </div>
        </div>
      </div>
    </div>
  );
}
