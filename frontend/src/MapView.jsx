import { useEffect, useRef } from "react";
import L from "leaflet";
import { RISK_COLORS, formatDuration, formatWhen } from "./api";

function rgba(hex, alpha) {
  const value = Number.parseInt(hex.slice(1), 16);
  const red = (value >> 16) & 255;
  const green = (value >> 8) & 255;
  const blue = value & 255;
  return `rgba(${red}, ${green}, ${blue}, ${alpha})`;
}

const HeatLayer = L.Layer.extend({
  initialize(points) {
    this._points = points || [];
    this._hour = 0;
    this._enabled = true;
    this._cache = [];
    this._cacheKey = "";
  },

  setHour(hour) {
    this._hour = hour;
    if (this._map) this._show();
  },

  setEnabled(enabled) {
    this._enabled = enabled;
    if (this._map) this._show();
  },

  onAdd(map) {
    this._map = map;
    this._canvas = L.DomUtil.create("canvas", "corridor-heat");
    this._canvas.style.pointerEvents = "none";
    map.getPanes().overlayPane.appendChild(this._canvas);
    this._onMove = () => {
      this._cacheKey = "";
      if (this._frame) return;
      this._frame = requestAnimationFrame(() => {
        this._frame = 0;
        this._show();
      });
    };
    this._onSettle = () => {
      this._cacheKey = "";
      this._buildCache();
      this._show();
    };
    map.on("move zoom resize", this._onMove, this);
    map.on("moveend zoomend", this._onSettle, this);
    this._buildCache();
    this._show();
  },

  onRemove(map) {
    map.off("move zoom resize", this._onMove, this);
    map.off("moveend zoomend", this._onSettle, this);
    if (this._frame) cancelAnimationFrame(this._frame);
    this._canvas.remove();
    this._cache = [];
    this._map = null;
  },

  _viewKey() {
    const map = this._map;
    const center = map.getCenter();
    const size = map.getSize();
    return `${map.getZoom().toFixed(2)}:${center.lat.toFixed(3)}:${center.lng.toFixed(3)}:${size.x}x${size.y}`;
  },

  _sizeCanvas(canvas) {
    const size = this._map.getSize();
    const ratio = window.devicePixelRatio || 1;
    canvas.width = Math.max(1, size.x) * ratio;
    canvas.height = Math.max(1, size.y) * ratio;
    if (canvas.style) {
      canvas.style.width = `${size.x}px`;
      canvas.style.height = `${size.y}px`;
    }
    return { size, ratio };
  },

  _radiusPx(lat) {
    const zoom = this._map.getZoom();
    const metersPerPixel = (156543.03392 * Math.cos((lat * Math.PI) / 180)) / 2 ** zoom;
    return Math.max(14, Math.min(140, 8000 / metersPerPixel));
  },

  _drawHour(ctx, ratio, hour) {
    const map = this._map;
    const size = map.getSize();
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, size.x, size.y);
    for (const point of this._points) {
      const level = point.risks[hour];
      const at = map.latLngToContainerPoint([point.lat, point.lng]);
      const radius = this._radiusPx(point.lat);
      if (at.x < -radius || at.y < -radius || at.x > size.x + radius || at.y > size.y + radius) continue;
      const color = RISK_COLORS[level] || "#8a8a8a";
      const paint = ctx.createRadialGradient(at.x, at.y, 0, at.x, at.y, radius);
      paint.addColorStop(0, rgba(color, level ? 0.55 : 0.2));
      paint.addColorStop(0.55, rgba(color, level ? 0.28 : 0.08));
      paint.addColorStop(1, rgba(color, 0));
      ctx.beginPath();
      ctx.fillStyle = paint;
      ctx.arc(at.x, at.y, radius, 0, Math.PI * 2);
      ctx.fill();
    }
  },

  _buildCache() {
    if (!this._map || !this._enabled) return;
    const key = this._viewKey();
    if (this._cacheKey === key && this._cache.length === 49) return;
    const frames = [];
    for (let hour = 0; hour < 49; hour += 1) {
      const canvas = document.createElement("canvas");
      const { ratio } = this._sizeCanvas(canvas);
      this._drawHour(canvas.getContext("2d"), ratio, hour);
      frames.push(canvas);
    }
    this._cache = frames;
    this._cacheKey = key;
  },

  _show() {
    const map = this._map;
    const canvas = this._canvas;
    if (!map || !canvas) return;
    const { size, ratio } = this._sizeCanvas(canvas);
    L.DomUtil.setPosition(canvas, map.containerPointToLayerPoint([0, 0]));
    const ctx = canvas.getContext("2d");
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (!this._enabled) return;
    const frame = this._cacheKey === this._viewKey() ? this._cache[this._hour] : null;
    if (frame) {
      ctx.drawImage(frame, 0, 0);
      return;
    }
    this._drawHour(ctx, ratio, this._hour);
    void size;
  },
});

function summaryHtml(route) {
  const risk = route.average_risk == null ? "—" : Number(route.average_risk).toFixed(2);
  const badge = route.recommended ? " · Recommended" : "";
  return `<div class="route-tip"><strong>Route ${route.id + 1}${badge}</strong><div>${Number(route.distance_miles).toFixed(0)} mi · ${formatDuration(route.duration_seconds)}</div><div>Average risk ${risk}</div></div>`;
}

function routeMidpoint(route) {
  const positions = route.segments.flatMap((segment) => segment.positions);
  if (!positions.length) {
    const point = route.checkpoints[0];
    return point ? [point.lat, point.lng] : null;
  }
  return positions[Math.floor(positions.length / 2)];
}

function popupHtml(point) {
  const reading = (label, value, unit, level) => {
    const shown = value == null ? "—" : `${value} ${unit}`;
    return `<div>${label} ${shown}${level ? ` · ${level}` : ""}</div>`;
  };
  return `
    <div class="popup">
      <strong>Mile ${Number(point.mile).toFixed(1)}</strong>
      <div>${formatWhen(point.eta)}</div>
      <div class="popup-level">${point.level || "No data"}</div>
      ${reading("Wind", point.wind_mph, "mph", point.wind_level)}
      ${reading("Rain", point.rain_in_hr, "in/hr", point.rain_level)}
      ${reading("Snow", point.snow_in_hr, "in/hr", point.snow_level)}
    </div>
  `;
}

export default function MapView({ plan, selected, hour, onSelect, showHeat = true }) {
  const node = useRef(null);
  const mapRef = useRef(null);
  const heatRef = useRef(null);
  const linesRef = useRef([]);
  const marksRef = useRef([]);
  const hourRef = useRef(hour);
  const showRef = useRef(showHeat);
  const onSelectRef = useRef(onSelect);
  hourRef.current = hour;
  showRef.current = showHeat;
  onSelectRef.current = onSelect;

  useEffect(() => {
    const map = L.map(node.current, {
      preferCanvas: true,
      zoomControl: true,
    });
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    }).addTo(map);
    map.createPane("routes");
    map.getPane("routes").style.zIndex = 450;
    map.createPane("checks");
    map.getPane("checks").style.zIndex = 460;
    map.setView([39.8, -98.6], 4);
    mapRef.current = map;
    const onResize = () => map.invalidateSize();
    window.addEventListener("resize", onResize);
    requestAnimationFrame(onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      map.remove();
      mapRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return undefined;
    const lines = L.layerGroup().addTo(map);
    const marks = L.layerGroup().addTo(map);
    linesRef.current = [];
    marksRef.current = [];
    if (!plan) {
      return () => {
        lines.remove();
        marks.remove();
      };
    }

    const bounds = [];
    plan.routes.forEach((route) => {
      const lineGroup = L.featureGroup();
      route.segments.forEach((segment) => {
        const line = L.polyline(segment.positions, {
          pane: "routes",
          color: RISK_COLORS[segment.level] || "#8a8a8a",
          weight: 5,
          opacity: 0.9,
          lineCap: "round",
          lineJoin: "round",
        });
        line.on("click", () => onSelectRef.current?.(route.id));
        lineGroup.addLayer(line);
        segment.positions.forEach((position) => bounds.push(position));
      });
      const mid = routeMidpoint(route);
      if (mid) {
        const label = L.circleMarker(mid, {
          pane: "checks",
          radius: 1,
          opacity: 0,
          fillOpacity: 0,
          interactive: false,
          summary: true,
        });
        label.bindTooltip(summaryHtml(route), {
          permanent: true,
          direction: "top",
          offset: [0, -6],
          className: "route-label",
          opacity: 1,
        });
        lineGroup.addLayer(label);
      }
      lineGroup.addTo(lines);
      linesRef.current.push({ id: route.id, group: lineGroup });

      const markGroup = L.featureGroup();
      route.checkpoints.forEach((point) => {
        const marker = L.circleMarker([point.lat, point.lng], {
          pane: "checks",
          radius: 6,
          color: "#fffdf8",
          weight: 2,
          fillColor: RISK_COLORS[point.level] || "#8a8a8a",
          fillOpacity: 1,
        });
        const detail = popupHtml(point);
        marker.bindTooltip(detail, {
          direction: "top",
          className: "checkpoint-tip",
          opacity: 1,
        });
        marker.bindPopup(detail);
        marker.on("click", () => onSelectRef.current?.(route.id));
        markGroup.addLayer(marker);
      });
      markGroup.addTo(marks);
      marksRef.current.push({ id: route.id, group: markGroup });
    });

    const heat = new HeatLayer(plan.corridor);
    heat.setEnabled(showRef.current);
    heat.setHour(hourRef.current);
    heat.addTo(map);
    heatRef.current = heat;

    if (bounds.length) {
      map.fitBounds(bounds, { padding: [40, 40], maxZoom: 12 });
    }
    return () => {
      heat.remove();
      heatRef.current = null;
      lines.remove();
      marks.remove();
      linesRef.current = [];
      marksRef.current = [];
    };
  }, [plan]);

  useEffect(() => {
    heatRef.current?.setHour(hour);
  }, [hour, plan]);

  useEffect(() => {
    heatRef.current?.setEnabled(showHeat);
  }, [showHeat, plan]);

  useEffect(() => {
    const paint = (entry, on) => {
      entry.group.eachLayer((layer) => {
        if (!layer.setStyle || layer.options?.summary) return;
        if (layer.setRadius && !layer.getLatLngs) {
          layer.setRadius(on ? 7 : 4);
          layer.setStyle({ opacity: on ? 1 : 0.45, fillOpacity: on ? 1 : 0.35 });
        } else {
          layer.setStyle({ weight: on ? 7 : 4, opacity: on ? 0.95 : 0.38 });
        }
        if (on && layer.bringToFront) layer.bringToFront();
      });
    };
    linesRef.current.forEach((entry) => paint(entry, entry.id === selected));
    marksRef.current.forEach((entry) => paint(entry, entry.id === selected));
  }, [plan, selected]);

  return <div ref={node} className="map" />;
}
