export const RISK_LEVELS = ["Low", "Moderate", "High", "Severe", "No Travel"];

export const RISK_COLORS = {
  Low: "#2e7d32",
  Moderate: "#f9a825",
  High: "#ef6c00",
  Severe: "#c62828",
  "No Travel": "#161616",
};

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
  }).format(date);
}

export function formatDuration(seconds) {
  const whole = Math.max(0, Math.round(seconds));
  const hours = Math.floor(whole / 3600);
  const minutes = Math.round((whole % 3600) / 60);
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
