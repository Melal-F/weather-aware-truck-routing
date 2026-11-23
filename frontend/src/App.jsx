import { useEffect, useRef, useState } from "react";
import MapView from "./MapView";
import {
  RISK_COLORS,
  RISK_LEVELS,
  dayKey,
  dayOffset,
  formatClock,
  formatDayClock,
  formatTripStamp,
  formatWeekday,
  formatDrive,
  geocode,
  planTrip,
  routeLetter,
} from "./api";

function Icon({ size = 16, children }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none" aria-hidden="true">
      {children}
    </svg>
  );
}

function stroke() {
  return { stroke: "currentColor", strokeWidth: 1.5, strokeLinecap: "round", strokeLinejoin: "round" };
}

function Logo() {
  return (
    <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true">
      <rect width="20" height="20" rx="6" fill="currentColor" />
      <path d="M5 14.5 9.2 7.2 15 11" fill="none" stroke="#fff" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="5" cy="14.5" r="1.15" fill="#fff" />
      <circle cx="15" cy="11" r="1.15" fill="#fff" />
    </svg>
  );
}

function HollowIcon() {
  return (
    <Icon>
      <circle cx="8" cy="8" r="4" fill="none" {...stroke()} />
    </Icon>
  );
}

function FilledIcon() {
  return (
    <Icon>
      <circle cx="8" cy="8" r="4" fill="currentColor" />
    </Icon>
  );
}

function SwapIcon() {
  return (
    <Icon>
      <path d="M5.5 3.5v9M5.5 12.5 3.5 10.5M5.5 12.5 7.5 10.5M10.5 12.5v-9M10.5 3.5 8.5 5.5M10.5 3.5 12.5 5.5" {...stroke()} />
    </Icon>
  );
}

function AlertIcon({ size = 16 }) {
  return (
    <Icon size={size}>
      <path d="M8 2.75 14 13.25H2L8 2.75z" {...stroke()} />
      <path d="M8 6.5v3.25" {...stroke()} />
      <path d="M8 11.75h.01" {...stroke()} />
    </Icon>
  );
}

function CheckIcon({ size = 12 }) {
  return (
    <Icon size={size}>
      <path d="M3 8.2 6.2 11.2 13 4.2" {...stroke()} />
    </Icon>
  );
}

function CheckCircleIcon() {
  return (
    <Icon>
      <circle cx="8" cy="8" r="6" {...stroke()} />
      <path d="M5.2 8.2 7.1 10.1 10.8 6.2" {...stroke()} />
    </Icon>
  );
}

function PlayIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
      <path d="M4.2 2.6v8.8L11.4 7 4.2 2.6z" fill="currentColor" />
    </svg>
  );
}

function PauseIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
      <path d="M3.5 2.5h2.2v9H3.5zM8.3 2.5h2.2v9H8.3z" fill="currentColor" />
    </svg>
  );
}

function formatLocal(date) {
  const pad = (value) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function defaultDeparture() {
  const next = new Date();
  next.setMinutes(0, 0, 0);
  next.setHours(next.getHours() + 1);
  return formatLocal(next);
}

function departureWindow(now = new Date()) {
  return {
    min: formatLocal(new Date(now.getTime() - 60 * 60 * 1000)),
    max: formatLocal(new Date(now.getTime() + 13 * 24 * 60 * 60 * 1000)),
  };
}

function parseLocal(value) {
  const [day, clock] = value.split("T");
  const [year, month, date] = day.split("-").map(Number);
  const [hour, minute] = clock.split(":").map(Number);
  return new Date(year, month - 1, date, hour, minute);
}

function groupLoad(value) {
  const digits = String(value).replace(/[^\d]/g, "");
  if (!digits) return "";
  return Number(digits).toLocaleString("en-US");
}

function milesRound(value) {
  return `${Math.round(Number(value) || 0).toLocaleString("en-US")} mi`;
}

function stripLabel(route) {
  const parts = RISK_LEVELS.map((level) => `${level} ${Math.round(route.miles[level] || 0)} mi`);
  if ((route.miles.Unassessed || 0) > 0) {
    parts.push(`Unassessed ${Math.round(route.miles.Unassessed)} mi`);
  }
  return parts.join(", ");
}

function levelRows(miles) {
  return [...RISK_LEVELS, "Unassessed"].filter((level) => level === "Low" || (miles[level] || 0) > 0);
}

function reading(value) {
  if (value == null || Number.isNaN(Number(value))) return null;
  return String(value);
}

function PlaceField({ id, label, kind, query, setQuery, pick, setPick, invalid }) {
  const [hits, setHits] = useState([]);
  const seq = useRef(0);

  useEffect(() => {
    if (pick || query.trim().length < 3) {
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
      <div className="pin-field">
        {kind === "origin" ? <HollowIcon /> : <FilledIcon />}
        <input
          id={id}
          value={query}
          autoComplete="off"
          placeholder="City, address, or place"
          aria-invalid={invalid || undefined}
          className={invalid ? "invalid" : ""}
          onChange={(event) => {
            setQuery(event.target.value);
            setPick(null);
          }}
        />
      </div>
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

function SpacingControl({ value, onChange, labelledBy }) {
  const options = [
    { id: "10", label: "10 mi" },
    { id: "25", label: "25 mi" },
    { id: "50", label: "50 mi" },
  ];

  function onKey(event) {
    if (!["ArrowRight", "ArrowLeft", "ArrowDown", "ArrowUp"].includes(event.key)) return;
    event.preventDefault();
    const index = options.findIndex((item) => item.id === value);
    const delta = event.key === "ArrowRight" || event.key === "ArrowDown" ? 1 : -1;
    const next = options[(index + delta + options.length) % options.length];
    onChange(next.id);
    event.currentTarget.querySelector(`[data-space="${next.id}"]`)?.focus();
  }

  return (
    <div className="segment" role="radiogroup" aria-labelledby={labelledBy} onKeyDown={onKey}>
      {options.map((item) => (
        <button
          key={item.id}
          type="button"
          role="radio"
          data-space={item.id}
          aria-checked={value === item.id}
          tabIndex={value === item.id ? 0 : -1}
          className={value === item.id ? "on" : ""}
          onClick={() => onChange(item.id)}
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}

function RouteCard({ route, plan, selected, onSelect }) {
  const unsafe = (route.miles["No Travel"] || 0) > 0 || (route.miles.Unassessed || 0) > 0;
  const levels = [...RISK_LEVELS, "Unassessed"].filter((level) => (route.miles[level] || 0) > 0);
  const total = levels.reduce((sum, level) => sum + (route.miles[level] || 0), 0);
  const offset = dayOffset(plan.departure, route.arrival, plan.origin?.tz, plan.destination?.tz);
  const rows = levelRows(route.miles);
  return (
    <button
      type="button"
      className={selected ? "card selected" : "card"}
      aria-pressed={selected}
      onClick={() => onSelect(route.id)}
    >
      <div className="card-top">
        <span className="letter">{routeLetter(route.id)}</span>
        <span className="card-name">Route {routeLetter(route.id)}</span>
        {unsafe ? (
          <span className="flag bad"><AlertIcon size={12} /> Not safe</span>
        ) : route.recommended ? (
          <span className="flag good"><CheckIcon /> Recommended</span>
        ) : null}
      </div>
      {route.profile === "driving-car" && (
        <p className="profile">Car routing (truck profile unavailable)</p>
      )}
      <div className="metrics">
        <div>
          <span>Distance</span>
          <strong>{milesRound(route.distance_miles)}</strong>
        </div>
        <div>
          <span>Drive time</span>
          <strong>{formatDrive(route.duration_seconds)}</strong>
        </div>
        <div>
          <span>Arrives</span>
          <strong>{formatClock(route.arrival, plan.destination?.tz)}</strong>
          {offset > 0 && <em>+{offset} day{offset === 1 ? "" : "s"}</em>}
        </div>
      </div>
      <div className="strip" role="img" aria-label={stripLabel(route)}>
        {levels.map((level) => (
          <span
            key={level}
            style={{
              width: total ? `${(route.miles[level] / total) * 100}%` : 0,
              background: RISK_COLORS[level],
            }}
          />
        ))}
      </div>
      <div className={selected ? "extra open" : "extra"} aria-hidden={selected ? undefined : true}>
        <div className="extra-inner">
          <ul className="levels">
            {rows.map((level) => (
              <li key={level}>
                <i className="dot" style={{ width: 8, height: 8, background: RISK_COLORS[level] }} />
                <span>{level}</span>
                <b>{milesRound(route.miles[level])}</b>
              </li>
            ))}
          </ul>
          <p className="avg">
            Avg risk {route.average_risk == null ? "—" : Number(route.average_risk).toFixed(2)}
            {" · "}
            {route.checkpoints.length} checkpoints
          </p>
        </div>
      </div>
    </button>
  );
}

function Checkpoints({ route, onHover, onFocus }) {
  return (
    <section>
      <div className="section-head checks-head">
        <div>
          <h2>Checkpoints · Route {routeLetter(route.id)}</h2>
          <p className="units">Wind mph · Rain and snow in/h</p>
        </div>
        <span>{route.checkpoints.length}</span>
      </div>
      <div className="table-wrap">
        <table className="checks">
          <colgroup>
            <col className="c-mile" />
            <col className="c-eta" />
            <col className="c-wind" />
            <col className="c-rain" />
            <col className="c-snow" />
            <col className="c-risk" />
          </colgroup>
          <thead>
            <tr>
              <th className="num">Mile</th>
              <th>ETA</th>
              <th className="num">Wind</th>
              <th className="num">Rain</th>
              <th className="num">Snow</th>
              <th>Risk</th>
            </tr>
          </thead>
          <tbody>
            {route.checkpoints.map((point, index) => {
              const level = point.level;
              const previous = route.checkpoints[index - 1];
              const showDay = !previous || dayKey(previous.eta, previous.tz) !== dayKey(point.eta, point.tz);
              const riskStyle = !level || level === "Low"
                ? undefined
                : {
                    color: level === "No Travel" ? "var(--text)" : RISK_COLORS[level],
                    fontWeight: level === "No Travel" ? 600 : 400,
                  };
              return (
                <tr
                  key={`${point.mile}-${index}`}
                  tabIndex={0}
                  onMouseEnter={() => onHover({ routeId: route.id, index })}
                  onMouseLeave={() => onHover(null)}
                  onClick={() => onFocus({ routeId: route.id, index, nonce: Date.now() })}
                  onKeyDown={(event) => {
                    if (event.key !== "Enter" && event.key !== " ") return;
                    event.preventDefault();
                    onFocus({ routeId: route.id, index, nonce: Date.now() });
                  }}
                >
                  <td className="num">{point.mile == null || Number.isNaN(Number(point.mile)) ? "—" : Math.round(Number(point.mile))}</td>
                  <td className="eta">
                    {showDay && <span className="eta-day">{formatWeekday(point.eta, point.tz)}</span>}
                    {formatClock(point.eta, point.tz)}
                  </td>
                  <td className="num">{reading(point.wind_mph) == null ? <span className="missing">—</span> : reading(point.wind_mph)}</td>
                  <td className="num">{reading(point.rain_in_hr) == null ? <span className="missing">—</span> : reading(point.rain_in_hr)}</td>
                  <td className="num">{reading(point.snow_in_hr) == null ? <span className="missing">—</span> : reading(point.snow_in_hr)}</td>
                  <td>
                    <span className="risk-cell" style={riskStyle}>
                      <i className="dot" style={{ width: 8, height: 8, background: RISK_COLORS[level] || "var(--text-3)" }} />
                      {level || "No data"}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function midnightMarks(forecastStart) {
  const start = new Date(forecastStart).getTime();
  const marks = [];
  for (let hour = 0; hour <= 48; hour += 1) {
    const date = new Date(start + hour * 3600000);
    if (date.getHours() === 0 && date.getMinutes() === 0) {
      marks.push({ hour, label: formatWeekday(date.toISOString()) });
    }
  }
  return marks;
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
  const [load, setLoad] = useState("42000");
  const [loadFocus, setLoadFocus] = useState(false);
  const [spacing, setSpacing] = useState("25");
  const [plan, setPlan] = useState(null);
  const [editing, setEditing] = useState(true);
  const [selected, setSelected] = useState(null);
  const [hour, setHour] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [showHeat, setShowHeat] = useState(true);
  const [error, setError] = useState("");
  const [live, setLive] = useState("");
  const [loading, setLoading] = useState(false);
  const [highlight, setHighlight] = useState(null);
  const [focus, setFocus] = useState(null);
  const sliderRef = useRef(null);

  useEffect(() => {
    if (!playing || !plan) return undefined;
    const timer = setInterval(() => {
      setHour((current) => (current >= 48 ? 0 : current + 1));
    }, 800);
    return () => clearInterval(timer);
  }, [playing, plan]);

  function swapPlaces() {
    setOriginQuery(destQuery);
    setDestQuery(originQuery);
    setOriginPick(destPick);
    setDestPick(originPick);
  }

  async function onSubmit(event) {
    event.preventDefault();
    setError("");
    const loadLb = Number(String(load).replace(/[^\d.]/g, ""));
    if (!Number.isFinite(loadLb) || loadLb <= 0) {
      const message = "Load weight must be greater than 0 pounds.";
      setError(message);
      setLive(message);
      return;
    }
    const when = new Date(departure);
    if (Number.isNaN(when.getTime())) {
      const message = "Departure date and time is required.";
      setError(message);
      setLive(message);
      return;
    }
    const { min, max } = departureWindow();
    const chosen = parseLocal(departure);
    if (chosen < parseLocal(min) || chosen > parseLocal(max)) {
      const message = "Departure must be between now and 13 days ahead (forecast limit).";
      setError(message);
      setLive(message);
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
        departure,
        load_lb: loadLb,
        spacing_miles: Number(spacing),
      });
      const winner = next.routes.find((route) => route.recommended) || next.routes[0];
      const count = next.routes.length;
      const recommended = next.routes.find((route) => route.recommended);
      setPlan(next);
      setSelected(winner ? winner.id : null);
      setHour(0);
      setPlaying(false);
      setEditing(false);
      setHighlight(null);
      setFocus(null);
      setLive(
        recommended
          ? `${count} routes planned. Route ${routeLetter(recommended.id)} recommended.`
          : `${count} routes planned.`,
      );
    } catch (err) {
      const message = err.message || "Could not plan that trip.";
      setError(message);
      setLive(message);
    } finally {
      setLoading(false);
    }
  }

  function onDockKey(event) {
    if (!plan) return;
    if (event.key === " " && event.target === event.currentTarget) {
      event.preventDefault();
      setPlaying((on) => !on);
      return;
    }
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const step = event.shiftKey ? 6 : 1;
    const delta = event.key === "ArrowRight" ? step : -step;
    setHour((current) => Math.min(48, Math.max(0, current + delta)));
  }

  const { min: departureMin, max: departureMax } = departureWindow();
  const showForm = !plan || editing;
  const selectedRoute = plan?.routes.find((route) => route.id === selected) || null;
  const recommended = plan?.routes.find((route) => route.recommended) || null;
  const forecastIso = plan
    ? new Date(new Date(plan.forecast_start).getTime() + hour * 3600000).toISOString()
    : "";
  let windowEnd = 0;
  if (plan && selectedRoute) {
    const start = new Date(plan.forecast_start).getTime();
    const end = new Date(selectedRoute.arrival).getTime();
    windowEnd = Math.max(0, Math.min(48, (end - start) / 3600000));
  }
  const ticks = [0, 6, 12, 18, 24, 30, 36, 42, 48];
  const days = plan ? midnightMarks(plan.forecast_start) : [];

  return (
    <div className="shell">
      <header className="top">
        <div className="brand">
          <Logo />
          <h1>Weather-Aware Truck Routing</h1>
        </div>
        <p className="sources">Routes: OpenRouteService · Weather: Open-Meteo</p>
      </header>
      <div className="body">
        <aside>
          <div className="panel-scroll">
            <p className="sr" aria-live="polite">{live}</p>
            <section>
              <h2 className="trip-heading">Trip</h2>
              {showForm ? (
                <form onSubmit={onSubmit}>
                  <div className="place-stack">
                    <PlaceField
                      id="origin"
                      label="Origin"
                      kind="origin"
                      query={originQuery}
                      setQuery={setOriginQuery}
                      pick={originPick}
                      setPick={setOriginPick}
                      invalid={/origin/i.test(error)}
                    />
                    <button type="button" className="swap" aria-label="Swap origin and destination" onClick={swapPlaces}>
                      <SwapIcon />
                    </button>
                    <PlaceField
                      id="destination"
                      label="Destination"
                      kind="destination"
                      query={destQuery}
                      setQuery={setDestQuery}
                      pick={destPick}
                      setPick={setDestPick}
                      invalid={/destination/i.test(error)}
                    />
                  </div>
                  <div className="split">
                    <div className="field">
                      <label htmlFor="departure">Departure (origin local time)</label>
                      <input
                        id="departure"
                        type="datetime-local"
                        value={departure}
                        min={departureMin}
                        max={departureMax}
                        aria-invalid={/departure/i.test(error) || undefined}
                        className={/departure/i.test(error) ? "invalid" : ""}
                        onChange={(event) => setDeparture(event.target.value)}
                        onInvalid={(event) => {
                          const { rangeUnderflow, rangeOverflow } = event.target.validity;
                          if (!rangeUnderflow && !rangeOverflow) return;
                          event.preventDefault();
                          const message = "Departure must be between now and 13 days ahead (forecast limit).";
                          setError(message);
                          setLive(message);
                        }}
                        required
                      />
                    </div>
                    <div className="field">
                      <label htmlFor="load">Load</label>
                      <div className="suffix">
                        <input
                          id="load"
                          inputMode="numeric"
                          value={loadFocus ? load : groupLoad(load)}
                          aria-invalid={/load/i.test(error) || undefined}
                          className={/load/i.test(error) ? "invalid" : ""}
                          onFocus={() => setLoadFocus(true)}
                          onBlur={() => setLoadFocus(false)}
                          onChange={(event) => setLoad(event.target.value.replace(/[^\d]/g, ""))}
                          required
                        />
                        <span>lb</span>
                      </div>
                    </div>
                  </div>
                  <div className="field">
                    <label id="spacing-label">Checkpoint spacing</label>
                    <SpacingControl value={spacing} onChange={setSpacing} labelledBy="spacing-label" />
                  </div>
                  <button className="go" type="submit" disabled={loading}>
                    {loading && <span className="spinner" aria-hidden="true" />}
                    {loading ? "Planning routes…" : "Plan routes"}
                  </button>
                  {error && (
                    <p className="alert" role="alert">
                      <AlertIcon />
                      <span>{error}</span>
                    </p>
                  )}
                </form>
              ) : (
                <div className="summary">
                  <div className="summary-row">
                    <p className="summary-title">{plan.origin.name} → {plan.destination.name}</p>
                    <button type="button" className="text-btn" onClick={() => setEditing(true)}>Edit</button>
                  </div>
                  <p className="summary-meta">
                    {formatTripStamp(plan.departure, plan.origin?.tz)}
                    {" · "}
                    {Number(plan.load_lb).toLocaleString("en-US")} lb
                    {" · every "}
                    {plan.spacing_miles} mi
                  </p>
                  {error && (
                    <p className="alert" role="alert">
                      <AlertIcon />
                      <span>{error}</span>
                    </p>
                  )}
                </div>
              )}
              {plan?.note && <p className="note">{plan.note}</p>}
              {plan?.safety_note && (
                <p className="alert" role="alert">
                  <AlertIcon />
                  <span>{plan.safety_note}</span>
                </p>
              )}
            </section>
            <section>
              <div className="section-head">
                <h2>Routes</h2>
                {plan && !loading && (
                  <span>{plan.routes.length} option{plan.routes.length === 1 ? "" : "s"}</span>
                )}
              </div>
              {loading && (
                <div className="skeletons" aria-hidden="true">
                  <div className="skeleton" />
                  <div className="skeleton" />
                  <div className="skeleton" />
                </div>
              )}
              {!loading && recommended && (
                <div className="callout">
                  <div className="callout-title">
                    <CheckCircleIcon />
                    Route {routeLetter(recommended.id)} recommended
                  </div>
                  {recommended.recommendation_reason && <p>{recommended.recommendation_reason}</p>}
                </div>
              )}
              {!loading && plan && (
                <div className="route-list">
                  {plan.routes.map((route) => (
                    <RouteCard
                      key={route.id}
                      route={route}
                      plan={plan}
                      selected={route.id === selected}
                      onSelect={setSelected}
                    />
                  ))}
                </div>
              )}
            </section>
            {!loading && selectedRoute && (
              <Checkpoints route={selectedRoute} onHover={setHighlight} onFocus={setFocus} />
            )}
          </div>
        </aside>
        <div className="stage">
          <MapView
            plan={plan}
            selected={selected}
            hour={hour}
            onSelect={setSelected}
            showHeat={showHeat}
            sliderRef={sliderRef}
            loading={loading}
            highlightCheckpoint={highlight}
            focusCheckpoint={focus}
          />
          {!plan && !loading && (
            <div className="empty-card">
              <h2>Plan a trip</h2>
              <p>Enter an origin, destination, departure time and load. Routes, checkpoint risk and a 48-hour weather corridor appear here.</p>
            </div>
          )}
          <div
            ref={sliderRef}
            className={plan ? "dock" : "dock is-off"}
            tabIndex={plan ? 0 : -1}
            onKeyDown={onDockKey}
            onPointerDown={(event) => event.stopPropagation()}
          >
            {!plan ? (
              <p className="dock-idle">Forecast timeline · plan a trip first</p>
            ) : (
              <div className="dock-row">
                <button
                  type="button"
                  className="play"
                  aria-label={playing ? "Pause forecast" : "Play forecast"}
                  onClick={() => setPlaying((on) => !on)}
                >
                  {playing ? <PauseIcon /> : <PlayIcon />}
                </button>
                <div
                  className="dock-time"
                  title="Play walks the 48 hours on the corridor already loaded. Checkpoint risk stays tied to arrival time."
                >
                  <strong>{formatDayClock(forecastIso)}</strong>
                  <span>+{hour} h</span>
                </div>
                <div className="scrub">
                  <div className="scrub-window" style={{ left: 0, width: `${(windowEnd / 48) * 100}%` }} />
                  <div className="scrub-track" aria-hidden="true">
                    <div className="scrub-fill" style={{ width: `${(hour / 48) * 100}%` }} />
                  </div>
                  <input
                    id="hour"
                    type="range"
                    min="0"
                    max="48"
                    step="1"
                    value={hour}
                    aria-label="Forecast hour"
                    onChange={(event) => setHour(Number(event.target.value))}
                  />
                  <div className="ticks" aria-hidden="true">
                    {ticks.map((mark) => (
                      <i key={mark} style={{ left: `${(mark / 48) * 100}%` }} />
                    ))}
                  </div>
                  <div className="days" aria-hidden="true">
                    {days.map((mark) => (
                      <span key={mark.hour} style={{ left: `${(mark.hour / 48) * 100}%` }}>{mark.label}</span>
                    ))}
                  </div>
                </div>
                <ul className="legend">
                  {RISK_LEVELS.map((level) => (
                    <li key={level}>
                      <i className="dot" style={{ width: 8, height: 8, background: RISK_COLORS[level] }} />
                      {level}
                    </li>
                  ))}
                </ul>
                <div className="corridor">
                  <button
                    type="button"
                    className={showHeat ? "switch on" : "switch"}
                    role="switch"
                    aria-checked={showHeat}
                    aria-label="Corridor"
                    onClick={() => setShowHeat((on) => !on)}
                  >
                    <span />
                  </button>
                  Corridor
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
