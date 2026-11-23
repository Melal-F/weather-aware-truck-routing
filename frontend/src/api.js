export const RISK_LEVELS = ["Low", "Moderate", "High", "Severe", "No Travel"];

export const RISK_COLORS = {
  Low: "#3B8F5A",
  Moderate: "#E0A100",
  High: "#E8590C",
  Severe: "#D92D20",
  "No Travel": "#101828",
  Unassessed: "#98A2B3",
};

export function routeLetter(id) {
  return String.fromCharCode(65 + Number(id || 0));
}

export function formatDrive(seconds) {
  const whole = Math.max(0, Math.round(Number(seconds) || 0));
  let hours = Math.floor(whole / 3600);
  let minutes = Math.round((whole % 3600) / 60);
  if (minutes === 60) {
    hours += 1;
    minutes = 0;
  }
  if (hours <= 0) return `${minutes} m`;
  return `${hours} h ${minutes} m`;
}

const formatters = new Map();

export function dateFormatter(locale, options) {
  const key = `${locale ?? ""}\0${JSON.stringify(options)}`;
  let cached = formatters.get(key);
  if (!cached) {
    cached = new Intl.DateTimeFormat(locale, options);
    formatters.set(key, cached);
  }
  return cached;
}

function zoned(iso, tz, options) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  const format = { ...options };
  if (tz) format.timeZone = tz;
  else delete format.timeZoneName;
  return dateFormatter(undefined, format).format(date);
}

export function formatClock(iso, tz) {
  const text = zoned(iso, tz, {
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: tz ? "short" : undefined,
  });
  return text || "—";
}

export function formatWeekday(iso, tz) {
  return zoned(iso, tz, { weekday: "short" }) || "";
}

export function formatDayClock(iso, tz) {
  const text = zoned(iso, tz, {
    weekday: "short",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: tz ? "short" : undefined,
  });
  return text ? text.replace(/,/g, "") : "—";
}

export function formatTripStamp(iso, tz) {
  return zoned(iso, tz, {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: tz ? "short" : undefined,
  }) || "—";
}

export function dayKey(iso, tz) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const parts = dateFormatter("en-US", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    ...(tz ? { timeZone: tz } : {}),
  }).formatToParts(date);
  const pick = (type) => parts.find((part) => part.type === type).value;
  return `${pick("year")}-${pick("month")}-${pick("day")}`;
}

export function dayOffset(fromIso, toIso, fromTz, toTz) {
  const start = Date.parse(dayKey(fromIso, fromTz));
  const end = Date.parse(dayKey(toIso, toTz || fromTz));
  if (Number.isNaN(start) || Number.isNaN(end)) return 0;
  return Math.round((end - start) / 86400000);
}

export function formatWhen(iso) {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return dateFormatter(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  }).format(date);
}

export function formatDuration(seconds) {
  const whole = Math.max(0, Math.round(seconds));
  let hours = Math.floor(whole / 3600);
  let minutes = Math.round((whole % 3600) / 60);
  if (minutes === 60) {
    hours += 1;
    minutes = 0;
  }
  if (hours <= 0) return `${minutes} min`;
  return `${hours} h ${minutes} min`;
}

async function read(response) {
  let body = {};
  try {
    body = await response.json();
  } catch {
    body = {};
  }
  if (!response.ok) {
    throw new Error(body.error || `Request failed (${response.status}).`);
  }
  return body;
}

export async function fetchTimezone(lat, lng) {
  const response = await fetch(`/api/timezone?lat=${encodeURIComponent(lat)}&lng=${encodeURIComponent(lng)}`);
  const body = await read(response);
  return body.tz;
}

export async function geocode(query) {
  const response = await fetch(`/api/geocode?q=${encodeURIComponent(query)}`);
  const body = await read(response);
  return body.results || [];
}

export async function planTrip(payload) {
  const response = await fetch("/api/plan", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return read(response);
}
