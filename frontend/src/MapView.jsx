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

const LEVEL_RANK = { Low: 1, Moderate: 2, High: 3, Severe: 4, "No Travel": 5 };

const HeatLayer = L.Layer.extend({
  initialize(points, cellDeg) {
    const cell = Number(cellDeg);
    this._points = points || [];
    this._cellDeg = cell > 0 ? cell : 0.2;
    this._hour = 0;
    this._enabled = true;
    this._hourCache = new Map();
    this._cacheView = "";
    this._cacheCellPx = null;
    this._cacheBlur = null;
    this._cachePad = null;
    this._origin = null;
  },

  setHour(hour) {
    this._hour = hour;
    this._schedule();
  },

  setEnabled(enabled) {
    this._enabled = enabled;
    this._schedule();
  },

  onAdd(map) {
    this._map = map;
    this._canvas = L.DomUtil.create("canvas", "corridor-heat leaflet-zoom-hide");
    this._canvas.style.pointerEvents = "none";
    map.getPanes().overlayPane.appendChild(this._canvas);
    this._onMove = () => {
      if (this._origin) L.DomUtil.setPosition(this._canvas, this._origin);
    };
    this._onSettle = () => this._schedule();
    map.on("move", this._onMove, this);
    map.on("moveend zoomend resize", this._onSettle, this);
    this._schedule();
  },

  onRemove(map) {
    map.off("move", this._onMove, this);
    map.off("moveend zoomend resize", this._onSettle, this);
    if (this._frame) cancelAnimationFrame(this._frame);
    this._canvas.remove();
    this._hourCache.clear();
    this._map = null;
  },

  _schedule() {
    if (!this._map || this._frame) return;
    this._frame = requestAnimationFrame(() => {
      this._frame = 0;
      this._paint();
    });
  },

  _viewKey() {
    const map = this._map;
    const center = map.getCenter();
    const size = map.getSize();
    return `${map.getZoom().toFixed(2)}:${center.lat.toFixed(3)}:${center.lng.toFixed(3)}:${size.x}x${size.y}`;
  },

  _cellPx() {
    const map = this._map;
    const center = map.getCenter();
    const left = map.latLngToContainerPoint([center.lat, center.lng]);
    const right = map.latLngToContainerPoint([center.lat, center.lng + this._cellDeg]);
    return Math.hypot(right.x - left.x, right.y - left.y);
  },

  // Drawn low to high so the worst risk sits on top.
  // Each item is the center of a cell; the rect is cellDeg wide and tall.
  // User space is container pixels; the transform shifts drawing by +pad.
  _drawHour(ctx, ratio, hour, pad) {
    const map = this._map;
    const size = map.getSize();
    const cell = this._cellDeg;
    const half = cell / 2;
    ctx.setTransform(ratio, 0, 0, ratio, pad * ratio, pad * ratio);
    ctx.clearRect(-pad, -pad, size.x + pad * 2, size.y + pad * 2);
    const order = this._points
      .map((point, index) => ({ point, index, rank: LEVEL_RANK[point.risks[hour]] || 0 }))
      .sort((a, b) => a.rank - b.rank || a.index - b.index);
    for (const item of order) {
      const point = item.point;
      const level = point.risks[hour];
      const nw = map.latLngToContainerPoint([point.lat + half, point.lng - half]);
      const se = map.latLngToContainerPoint([point.lat - half, point.lng + half]);
      const x = Math.min(nw.x, se.x);
      const y = Math.min(nw.y, se.y);
      const w = Math.abs(se.x - nw.x);
      const h = Math.abs(se.y - nw.y);
      if (w <= 0 || h <= 0 || x > size.x + pad || y > size.y + pad || x + w < -pad || y + h < -pad) continue;
      ctx.fillStyle = rgba(RISK_COLORS[level] || "#8a8a8a", 0.45);
      ctx.fillRect(x, y, w, h);
    }
  },

  _remember(hour, source) {
    if (this._hourCache.has(hour)) this._hourCache.delete(hour);
    if (this._hourCache.size >= 6) {
      this._hourCache.delete(this._hourCache.keys().next().value);
    }
    const copy = document.createElement("canvas");
    copy.width = source.width;
    copy.height = source.height;
    copy.getContext("2d").drawImage(source, 0, 0);
    this._hourCache.set(hour, copy);
  },

  _paint() {
    const map = this._map;
    const canvas = this._canvas;
    if (!map || !canvas) return;
    const size = map.getSize();
    const ratio = window.devicePixelRatio || 1;
    const cellPx = this._cellPx();
    const blur = Math.min(24, Math.round(cellPx * 0.35));
    const pad = 2 * blur;
    const cssW = size.x + pad * 2;
    const cssH = size.y + pad * 2;
    canvas.width = Math.max(1, cssW) * ratio;
    canvas.height = Math.max(1, cssH) * ratio;
    canvas.style.width = `${cssW}px`;
    canvas.style.height = `${cssH}px`;
    canvas.style.filter = `blur(${blur}px)`;
    this._origin = map.containerPointToLayerPoint([-pad, -pad]);
    L.DomUtil.setPosition(canvas, this._origin);
    const ctx = canvas.getContext("2d");
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (!this._enabled) return;
    const view = this._viewKey();
    if (
      view !== this._cacheView ||
      cellPx !== this._cacheCellPx ||
      blur !== this._cacheBlur ||
      pad !== this._cachePad
    ) {
      this._hourCache.clear();
      this._cacheView = view;
      this._cacheCellPx = cellPx;
      this._cacheBlur = blur;
      this._cachePad = pad;
    }
    const frame = this._hourCache.get(this._hour);
    if (frame && frame.width === canvas.width && frame.height === canvas.height) {
      ctx.drawImage(frame, 0, 0);
      return;
    }
    this._drawHour(ctx, ratio, this._hour, pad);
    this._remember(this._hour, canvas);
  },
});

function summaryHtml(route) {
  const risk = route.average_risk == null ? "—" : Number(route.average_risk).toFixed(2);
  const badge = route.recommended ? " · Recommended" : "";
  const severe = Number(route.miles?.Severe || 0).toFixed(1);
  const high = Number(route.miles?.High || 0).toFixed(1);
  return `<div class="route-tip"><strong>Route ${route.id + 1}${badge}</strong><div>${Number(route.distance_miles).toFixed(0)} mi · ${formatDuration(route.duration_seconds)}</div><div>Severe ${severe} mi · High ${high} mi</div><div>Average risk ${risk}</div></div>`;
}

const LABEL_DIRECTION = ["right", "left", "top"];
const LABEL_W = 170;
const LABEL_H = 70;

function routePositions(route) {
  return route.segments.flatMap((segment) => segment.positions);
}

function samplePosition(positions, fraction) {
  const index = Math.round(fraction * (positions.length - 1));
  return positions[Math.min(positions.length - 1, Math.max(0, index))];
}

function labelSamples(route) {
  const positions = routePositions(route);
  if (!positions.length) {
    const point = route.checkpoints[0];
    return point ? [[point.lat, point.lng]] : [];
  }
  const samples = [];
  for (let step = 4; step <= 16; step += 1) samples.push(samplePosition(positions, step / 20));
  return samples;
}

// Tooltip box for a direction: right of the anchor, left of it, or above it.
// The 6px shift matches Leaflet's direction margin plus the tooltip offset.
function labelBox(at, direction) {
  const w = LABEL_W;
  const h = LABEL_H;
  if (direction === "left") return { x: at.x - w - 6, y: at.y - h / 2 - 6, w, h };
  if (direction === "top") return { x: at.x - w / 2, y: at.y - h - 12, w, h };
  return { x: at.x + 6, y: at.y - h / 2 - 6, w, h };
}

function boxesOverlap(a, b) {
  return a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y;
}

// Recommended route first, then the rest by id. One pass so later labels
// see boxes already placed. Samples are 20%–80% every 5%.
function placeLabels(routes, map) {
  const spots = new Array(routes.length).fill(null);
  if (!routes.length) return spots;
  const recommended = routes.findIndex((route) => route.recommended);
  const first = recommended < 0 ? 0 : recommended;
  const order = [first];
  routes
    .map((route, index) => ({ index, id: route.id }))
    .filter((item) => item.index !== first)
    .sort((a, b) => a.id - b.id)
    .forEach((item) => order.push(item.index));

  const linePx = routes.map((route) =>
    routePositions(route).map((position) => map.latLngToContainerPoint(position)),
  );
  const placed = [];

  for (const index of order) {
    const route = routes[index];
    const positions = routePositions(route);
    const samples = labelSamples(route);
    if (!samples.length) continue;
    const direction = LABEL_DIRECTION[index % 3];
    const others = [];
    linePx.forEach((pts, otherIndex) => {
      if (otherIndex !== index) others.push(...pts);
    });
    if (!others.length && !placed.length && positions.length) {
      const chosen = samplePosition(positions, 0.5);
      const at = map.latLngToContainerPoint(chosen);
      const box = labelBox(at, direction);
      placed.push({ box, cx: box.x + box.w / 2, cy: box.y + box.h / 2 });
      spots[index] = chosen;
      continue;
    }
    let best = null;
    let bestScore = -1;
    let free = null;
    let freeScore = -1;
    for (const sample of samples) {
      const at = map.latLngToContainerPoint(sample);
      let nearest = Infinity;
      for (const pt of others) {
        const dist = Math.hypot(at.x - pt.x, at.y - pt.y);
        if (dist < nearest) nearest = dist;
      }
      for (const item of placed) {
        const dist = Math.hypot(at.x - item.cx, at.y - item.cy);
        if (dist < nearest) nearest = dist;
      }
      const box = labelBox(at, direction);
      if (nearest > bestScore) {
        bestScore = nearest;
        best = sample;
      }
      if (!placed.some((item) => boxesOverlap(box, item.box)) && nearest > freeScore) {
        freeScore = nearest;
        free = sample;
      }
    }
    const chosen = free || best;
    if (!chosen) continue;
    const at = map.latLngToContainerPoint(chosen);
    const box = labelBox(at, direction);
    placed.push({ box, cx: box.x + box.w / 2, cy: box.y + box.h / 2 });
    spots[index] = chosen;
  }
  return spots;
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

export default function MapView({ plan, selected, hour, onSelect, showHeat = true, sliderRef }) {
  const node = useRef(null);
  const mapRef = useRef(null);
  const heatRef = useRef(null);
  const linesRef = useRef([]);
  const marksRef = useRef([]);
  const labelsRef = useRef([]);
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
    labelsRef.current = [];
    if (!plan) {
      return () => {
        lines.remove();
        marks.remove();
      };
    }

    const bounds = [];
    const labels = [];
    const spots = placeLabels(plan.routes, map);
    plan.routes.forEach((route, index) => {
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
      const mid = spots[index];
      if (mid) {
        const label = L.circleMarker(mid, {
          pane: "checks",
          radius: 1,
          opacity: 0,
          fillOpacity: 0,
          interactive: false,
          summary: true,
          routeId: route.id,
        });
        label.bindTooltip(summaryHtml(route), {
          permanent: true,
          direction: LABEL_DIRECTION[index % 3],
          offset: [0, -6],
          className: "route-label",
          opacity: 1,
        });
        lineGroup.addLayer(label);
        labels[index] = label;
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

    labelsRef.current = labels;

    const onZoom = () => {
      const next = placeLabels(plan.routes, map);
      plan.routes.forEach((_, index) => {
        const label = labels[index];
        const at = next[index];
        if (!label || !at) return;
        label.setLatLng(at);
        const tip = label.getTooltip();
        if (!tip) return;
        tip.options.direction = LABEL_DIRECTION[index % 3];
        if (tip._map) tip.update();
      });
    };
    map.on("zoomend", onZoom);

    const heat = new HeatLayer(plan.corridor, plan.corridor_cell_deg);
    heat.setEnabled(showRef.current);
    heat.setHour(hourRef.current);
    heat.addTo(map);
    heatRef.current = heat;

    if (bounds.length) {
      const sliderHeight = sliderRef?.current?.offsetHeight || 140;
      map.fitBounds(bounds, {
        paddingTopLeft: [40, 40],
        paddingBottomRight: [40, sliderHeight + 24],
        maxZoom: 12,
      });
    }
    return () => {
      map.off("zoomend", onZoom);
      heat.remove();
      heatRef.current = null;
      lines.remove();
      marks.remove();
      linesRef.current = [];
      marksRef.current = [];
      labelsRef.current = [];
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
    labelsRef.current.forEach((label) => {
      const tip = label.getTooltip();
      if (!tip) return;
      const on = label.options.routeId === selected;
      const el = tip.getElement();
      if (el) el.classList.toggle("selected", on);
      if (on) tip.bringToFront();
    });
  }, [plan, selected]);

  return <div ref={node} className="map" />;
}
