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

export function formatClock(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(date);
}

export function formatWeekday(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat(undefined, { weekday: "short" }).format(date);
}

export function formatDayClock(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat(undefined, {
    weekday: "short",
    hour: "numeric",
    minute: "2-digit",
  }).format(date).replace(/,/g, "");
}

export function formatTripStamp(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(date);
}

export function dayOffset(fromIso, toIso) {
  const from = new Date(fromIso);
  const to = new Date(toIso);
  if (Number.isNaN(from.getTime()) || Number.isNaN(to.getTime())) return 0;
  const start = new Date(from.getFullYear(), from.getMonth(), from.getDate());
  const end = new Date(to.getFullYear(), to.getMonth(), to.getDate());
  return Math.round((end.getTime() - start.getTime()) / 86400000);
}

export function formatWhen(iso) {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat(undefined, {
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
