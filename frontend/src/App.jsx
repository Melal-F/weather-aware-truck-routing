import { memo, useEffect, useRef, useState } from "react";
import MapView from "./MapView";
import {
  RISK_COLORS,
  RISK_LEVELS,
  dateFormatter,
  dayKey,
  dayOffset,
  formatClock,
  formatDayClock,
  formatTripStamp,
  formatWeekday,
  formatDrive,
  fetchTimezone,
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

function pad(value) {
  return String(value).padStart(2, "0");
}

function zonedParts(date, timeZone) {
  const options = {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  };
  if (timeZone) options.timeZone = timeZone;
  const parts = dateFormatter("en-US", options).formatToParts(date);
  const pick = (type) => Number(parts.find((part) => part.type === type).value);
  let hour = pick("hour");
  if (hour === 24) hour = 0;
  return {
    year: pick("year"),
    month: pick("month"),
    day: pick("day"),
    hour,
    minute: pick("minute"),
    second: pick("second"),
  };
}

function formatInZone(date, timeZone) {
  if (!timeZone) return formatLocal(date);
  const parts = zonedParts(date, timeZone);
  return `${parts.year}-${pad(parts.month)}-${pad(parts.day)}T${pad(parts.hour)}:${pad(parts.minute)}`;
}

function nextFullHour(now, timeZone) {
  if (!timeZone) {
    const next = new Date(now);
    next.setMinutes(0, 0, 0);
    next.setHours(next.getHours() + 1);
    return formatLocal(next);
  }
  const parts = zonedParts(now, timeZone);
  const into = (parts.minute * 60 + parts.second) * 1000 + now.getMilliseconds();
  return formatInZone(new Date(now.getTime() - into + 60 * 60 * 1000), timeZone);
}

function defaultDeparture() {
  return nextFullHour(new Date());
}

function departureWindow(now = new Date(), timeZone) {
  return {
    min: formatInZone(new Date(now.getTime() - 60 * 60 * 1000), timeZone),
    max: formatInZone(new Date(now.getTime() + 13 * 24 * 60 * 60 * 1000), timeZone),
  };
}

function activeWindow(hasOrigin, timeZone) {
  // The viewer's clock must not reject a time that is still ahead at the origin.
  if (hasOrigin && !timeZone) return { min: "1970-01-01T00:00", max: "9999-12-31T23:59" };
  return departureWindow(new Date(), timeZone);
}

function zoneAbbrev(timeZone, date = new Date()) {
  if (!timeZone) return "";
  const parts = dateFormatter("en-US", {
    timeZone,
    hour: "numeric",
    timeZoneName: "short",
  }).formatToParts(date);
  return parts.find((part) => part.type === "timeZoneName")?.value || "";
}

function parseLocal(value) {
  const [day, clock] = value.split("T");
  const [year, month, date] = day.split("-").map(Number);
  const [hour, minute] = clock.split(":").map(Number);
  return new Date(year, month - 1, date, hour, minute);
}

function sameDay(a, b) {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

function clockParts(value) {
  const date = parseLocal(value);
  const hour24 = date.getHours();
  return { hour: hour24 % 12 || 12, minute: date.getMinutes(), ap: hour24 >= 12 ? "PM" : "AM" };
}

function dayAllowed(day, min, max) {
  const start = new Date(day.getFullYear(), day.getMonth(), day.getDate());
  const end = new Date(day.getFullYear(), day.getMonth(), day.getDate(), 23, 59);
  return end >= parseLocal(min) && start <= parseLocal(max);
}

function monthCells(month) {
  const first = new Date(month.getFullYear(), month.getMonth(), 1);
  const cursor = new Date(first);
  cursor.setDate(1 - first.getDay());
  return Array.from({ length: 42 }, () => {
    const day = new Date(cursor);
    cursor.setDate(cursor.getDate() + 1);
    return day;
  });
}

function departureLabel(value) {
  return dateFormatter("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(parseLocal(value));
}

function DepartureField({ id, value, min, max, invalid, onChange }) {
  const [open, setOpen] = useState(false);
  const [place, setPlace] = useState(null);
  const [month, setMonth] = useState(() => parseLocal(value));
  const [hourDraft, setHourDraft] = useState(null);
  const [minuteDraft, setMinuteDraft] = useState(null);
  const root = useRef(null);
  const button = useRef(null);
  const pop = useRef(null);
  const parts = clockParts(value);
  const view = new Date(month.getFullYear(), month.getMonth(), 1);
  const monthKey = view.getFullYear() * 12 + view.getMonth();
  const minKey = (() => { const d = parseLocal(min); return d.getFullYear() * 12 + d.getMonth(); })();
  const maxKey = (() => { const d = parseLocal(max); return d.getFullYear() * 12 + d.getMonth(); })();

  function write(date) {
    let next = formatLocal(date);
    if (next < min) next = min;
    else if (next > max) next = max;
    onChange(next);
    return next;
  }

  useEffect(() => {
    if (!open) return undefined;
    const rect = button.current.getBoundingClientRect();
    const width = pop.current?.offsetWidth || 292;
    const height = pop.current?.offsetHeight || 360;
    const left = Math.max(8, Math.min(rect.left, window.innerWidth - width - 8));
    let top = rect.bottom + 6;
    if (top + height > window.innerHeight - 8) top = Math.max(8, rect.top - height - 6);
    setPlace({ top, left });
    const onDoc = (event) => {
      if (!root.current?.contains(event.target)) setOpen(false);
    };
    const onKey = (event) => {
      if (event.key !== "Escape") return;
      setOpen(false);
      button.current?.focus();
    };
    const close = () => setOpen(false);
    const stopWheel = (event) => event.preventDefault();
    const node = pop.current;
    node?.addEventListener("wheel", stopWheel, { passive: false });
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    window.addEventListener("resize", close);
    window.addEventListener("scroll", close, true);
    return () => {
      node?.removeEventListener("wheel", stopWheel);
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", close);
      window.removeEventListener("scroll", close, true);
    };
  }, [open]);

  function onHour(text) {
    const digits = text.replace(/\D/g, "").slice(-2);
    setHourDraft(digits);
    const n = Number(digits);
    if (!digits || n < 1 || n > 12) return;
    const date = parseLocal(value);
    date.setHours((n % 12) + (date.getHours() >= 12 ? 12 : 0));
    const next = clockParts(write(date));
    if (next.hour !== n) setHourDraft(null);
  }

  function onMinute(text) {
    const digits = text.replace(/\D/g, "").slice(-2);
    setMinuteDraft(digits);
    if (digits === "" || Number(digits) > 59) return;
    const date = parseLocal(value);
    date.setMinutes(Number(digits));
    const next = clockParts(write(date));
    if (next.minute !== Number(digits)) setMinuteDraft(null);
  }

  function setAp(ap) {
    const date = parseLocal(value);
    const pm = date.getHours() >= 12;
    if (ap === "PM" && !pm) date.setHours(date.getHours() + 12);
    if (ap === "AM" && pm) date.setHours(date.getHours() - 12);
    write(date);
    setHourDraft(null);
  }

  function onGridKey(event) {
    const step = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 }[event.key];
    if (!step || !event.target.classList.contains("when-day")) return;
    const days = [...event.currentTarget.querySelectorAll(".when-day")];
    let index = days.indexOf(event.target) + step;
    while (days[index]?.disabled) index += Math.sign(step);
    if (!days[index]) return;
    event.preventDefault();
    days[index].focus();
  }

  return (
    <div className="when" ref={root}>
      <button
        ref={button}
        id={id}
        type="button"
        className={invalid ? "when-trigger invalid" : "when-trigger"}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-invalid={invalid || undefined}
        aria-labelledby={`departure-label ${id}-value`}
        onClick={() => {
          if (open) {
            setOpen(false);
            return;
          }
          setMonth(parseLocal(value));
          setHourDraft(null);
          setMinuteDraft(null);
          setOpen(true);
        }}
      >
        <span id={`${id}-value`}>{departureLabel(value)}</span>
        <Icon>
          <rect x="2.25" y="3.25" width="11.5" height="10.5" rx="1.5" {...stroke()} />
          <path d="M2.25 6.5h11.5M5.25 2v2.5M10.75 2v2.5" {...stroke()} />
        </Icon>
      </button>
      {open && (
        <div
          ref={pop}
          className="when-pop"
          role="dialog"
          aria-label="Departure date and time"
          style={{ top: place?.top ?? 0, left: place?.left ?? 0, visibility: place ? "visible" : "hidden" }}
        >
          <div className="when-head">
            <button type="button" className="when-nav" aria-label="Previous month" disabled={monthKey <= minKey} onClick={() => setMonth(new Date(view.getFullYear(), view.getMonth() - 1, 1))}>
              <Icon><path d="M9.5 3.5 5.5 8l4 4.5" {...stroke()} /></Icon>
            </button>
            <strong>
              {view.toLocaleDateString("en-US", { month: "long", year: "numeric" })}
            </strong>
            <button type="button" className="when-nav" aria-label="Next month" disabled={monthKey >= maxKey} onClick={() => setMonth(new Date(view.getFullYear(), view.getMonth() + 1, 1))}>
              <Icon><path d="M6.5 3.5 10.5 8l-4 4.5" {...stroke()} /></Icon>
            </button>
          </div>
          <div className="when-grid" onKeyDown={onGridKey}>
            {["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"].map((name) => (
              <span key={name} className="when-dow">{name}</span>
            ))}
            {monthCells(view).map((day) => {
              const allowed = dayAllowed(day, min, max);
              const selected = sameDay(day, parseLocal(value));
              return (
                <button
                  key={formatLocal(day).slice(0, 10)}
                  type="button"
                  className={["when-day", selected ? "on" : "", sameDay(day, new Date()) ? "today" : ""].filter(Boolean).join(" ")}
                  disabled={!allowed}
                  aria-pressed={selected}
                  aria-label={day.toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" })}
                  onClick={() => {
                    const next = new Date(day);
                    const current = parseLocal(value);
                    next.setHours(current.getHours(), current.getMinutes(), 0, 0);
                    write(next);
                    setMonth(next);
                    setHourDraft(null);
                    setMinuteDraft(null);
                  }}
                >
                  {day.getDate()}
                </button>
              );
            })}
          </div>
          <div className="when-time">
            <span>Time</span>
            <div className="when-clock">
              <input
                aria-label="Hour"
                inputMode="numeric"
                value={hourDraft ?? String(parts.hour)}
                onFocus={(event) => event.target.select()}
                onChange={(event) => onHour(event.target.value)}
                onBlur={() => setHourDraft(null)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") event.preventDefault();
                }}
              />
              <span className="when-colon" aria-hidden="true">:</span>
              <input
                aria-label="Minute"
                inputMode="numeric"
                value={minuteDraft ?? String(parts.minute).padStart(2, "0")}
                onFocus={(event) => event.target.select()}
                onChange={(event) => onMinute(event.target.value)}
                onBlur={() => setMinuteDraft(null)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") event.preventDefault();
                }}
              />
            </div>
            <div className="when-ap" role="group" aria-label="AM or PM">
              {["AM", "PM"].map((ap) => (
                <button key={ap} type="button" className={parts.ap === ap ? "on" : ""} aria-pressed={parts.ap === ap} onClick={() => setAp(ap)}>
                  {ap}
                </button>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
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

const RouteCard = memo(function RouteCard({ route, plan, selected, onSelect }) {
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
});

const Checkpoints = memo(function Checkpoints({ route, onHover, onFocus }) {
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
});

function midnightMarks(forecastStart, timeZone) {
  const start = new Date(forecastStart).getTime();
  const marks = [];
  for (let hour = 0; hour <= 48; hour += 1) {
    const date = new Date(start + hour * 3600000);
    const parts = zonedParts(date, timeZone);
    if (parts.hour === 0 && parts.minute === 0) {
      marks.push({ hour, label: formatWeekday(date.toISOString(), timeZone) });
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
  const [originTz, setOriginTz] = useState("");
  const departureTouched = useRef(false);
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
    if (!originPick) {
      setOriginTz("");
      return undefined;
    }
    let cancelled = false;
    fetchTimezone(originPick.lat, originPick.lng)
      .then((tz) => {
        if (cancelled || !tz) return;
        setOriginTz(tz);
        setDeparture((current) => {
          const { min, max } = departureWindow(new Date(), tz);
          const next = departureTouched.current ? current : nextFullHour(new Date(), tz);
          if (next < min) return min;
          if (next > max) return max;
          return next;
        });
      })
      .catch(() => {
        if (!cancelled) setOriginTz("");
      });
    return () => {
      cancelled = true;
    };
  }, [originPick]);
  // Plan in the background once both places are picked, so the click finds the answer ready.
  const early = useRef({ key: "", request: null });

  function planBody(origin, destination) {
    return {
      origin,
      destination,
      departure,
      load_lb: Number(String(load).replace(/[^\d.]/g, "")),
      spacing_miles: Number(spacing),
    };
  }

  useEffect(() => {
    if (!originPick || !destPick) return undefined;
    const body = planBody(originPick, destPick);
    if (!(body.load_lb > 0)) return undefined;
    const key = JSON.stringify(body);
    if (early.current.key === key) return undefined;
    const timer = setTimeout(() => {
      const request = planTrip(body);
      // A failed early request is simply retried by the click.
      request.catch(() => {
        if (early.current.request === request) early.current = { key: "", request: null };
      });
      early.current = { key, request };
    }, 500);
    return () => clearTimeout(timer);
    // planBody reads exactly these values.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [originPick, destPick, departure, load, spacing]);

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
    const { min, max } = activeWindow(Boolean(originPick), originTz);
    if (departure < min || departure > max) {
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
      const body = planBody(origin, destination);
      const key = JSON.stringify(body);
      const next = await (early.current.key === key
        ? early.current.request.catch(() => planTrip(body))
        : planTrip(body));
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

  const { min: departureMin, max: departureMax } = activeWindow(Boolean(originPick), originTz);
  const departureAbbrev = zoneAbbrev(originTz);
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
  const days = plan ? midnightMarks(plan.forecast_start, plan.origin?.tz) : [];

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
                      <label id="departure-label" htmlFor="departure">
                        Departure time{departureAbbrev ? ` (${departureAbbrev})` : ""}
                      </label>
                      <DepartureField
                        id="departure"
                        value={departure}
                        min={departureMin}
                        max={departureMax}
                        invalid={/departure/i.test(error)}
                        onChange={(value) => {
                          departureTouched.current = true;
                          setDeparture(value);
                        }}
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
                  <strong>{formatDayClock(forecastIso, plan.origin?.tz)}</strong>
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
