import { useEffect, useRef, useState } from "react";
import L from "leaflet";
import { RISK_COLORS, formatClock, formatDayClock, formatDrive, routeLetter } from "./api";

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
    const pane = map.getPane("corridor") || map.getPanes().overlayPane;
    pane.appendChild(this._canvas);
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
      ctx.fillStyle = rgba(RISK_COLORS[level] || RISK_COLORS.Unassessed, 0.45);
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

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[ch]));
}

function chipHtml(route) {
  const check = route.recommended
    ? `<svg width="12" height="12" viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8.2 6.2 11.2 13 4.2" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>`
    : "";
  return `<span class="chip">${check}<span>${esc(routeLetter(route.id))} · ${esc(formatDrive(route.duration_seconds))}</span></span>`;
}

const LABEL_DIRECTION = ["right", "left", "top"];
const LABEL_W = 88;
const LABEL_H = 24;

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

function dot(level, size) {
  const color = RISK_COLORS[level] || RISK_COLORS.Unassessed;
  return `<i class="dot" style="width:${size}px;height:${size}px;background:${color}"></i>`;
}

function popupHtml(point) {
  const mile = Math.round(Number(point.mile) || 0);
  const level = point.level ? `${dot(point.level, 8)}<span>${esc(point.level)}</span>` : "No data";
  const row = (label, value, unit, own) => {
    const shown = value == null ? "—" : `${esc(value)} ${unit}`;
    const mark = own ? dot(own, 6) : "";
    return `<div class="wx-row"><dt>${label}</dt><dd><span>${shown}</span>${mark}</dd></div>`;
  };
  return `
    <div class="popup">
      <button type="button" class="popup-close" aria-label="Close">
        <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4 4 12" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>
      </button>
      <div class="popup-top"><strong>Mile ${mile}</strong><span class="popup-risk">${level}</span></div>
      <div class="popup-eta">${esc(formatDayClock(point.eta))}</div>
      <dl class="wx">
        ${row("Wind", point.wind_mph, "mph", point.wind_level)}
        ${row("Rain", point.rain_in_hr, "in/hr", point.rain_level)}
        ${row("Snow", point.snow_in_hr, "in/hr", point.snow_level)}
      </dl>
    </div>
  `;
}

function tipLine(point) {
  const mile = Math.round(Number(point.mile) || 0);
  return `Mile ${mile} · ${formatClock(point.eta)} · ${point.level || "No data"}`;
}

function token(name, fallback) {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || fallback;
}

function IconButton({ label, onClick, children }) {
  return (
    <button type="button" aria-label={label} onClick={onClick}>
      {children}
    </button>
  );
}

export default function MapView({
  plan,
  selected,
  hour,
  onSelect,
  showHeat = true,
  sliderRef,
  loading = false,
  highlightCheckpoint = null,
  focusCheckpoint = null,
}) {
  const node = useRef(null);
  const mapRef = useRef(null);
  const layersRef = useRef(null);
  const layerBoxRef = useRef(null);
  const heatRef = useRef(null);
  const linesRef = useRef([]);
  const marksRef = useRef([]);
  const labelsRef = useRef([]);
  const hourRef = useRef(hour);
  const showRef = useRef(showHeat);
  const onSelectRef = useRef(onSelect);
  const selectedRef = useRef(selected);
  const loadingRef = useRef(loading);
  const highlightRef = useRef(highlightCheckpoint);
  const [base, setBase] = useState("map");
  const [menu, setMenu] = useState(false);
  hourRef.current = hour;
  showRef.current = showHeat;
  onSelectRef.current = onSelect;
  selectedRef.current = selected;
  loadingRef.current = loading;
  highlightRef.current = highlightCheckpoint;

  useEffect(() => {
    const map = L.map(node.current, {
      preferCanvas: true,
      zoomControl: false,
      fadeAnimation: false,
    });
    const streets = L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
      subdomains: "abcd",
      maxZoom: 19,
      attribution: "© OpenStreetMap contributors © CARTO",
    });
    const satellite = L.tileLayer(
      "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
      {
        maxZoom: 19,
        attribution:
          "Tiles © Esri — Source: Esri, Maxar, Earthstar Geographics, and the GIS User Community",
      },
    );
    map.createPane("labels");
    map.getPane("labels").style.zIndex = 350;
    map.getPane("labels").style.pointerEvents = "none";
    const placeNames = L.tileLayer(
      "https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}",
      { maxZoom: 19, pane: "labels" },
    );
    map.createPane("corridor");
    map.getPane("corridor").style.zIndex = 250;
    map.getPane("corridor").style.pointerEvents = "none";
    streets.addTo(map);
    layersRef.current = { streets, satellite, placeNames };
    map.createPane("routes");
    map.getPane("routes").style.zIndex = 450;
    map.createPane("checks");
    map.getPane("checks").style.zIndex = 460;
    map.setView([39.8, -98.6], 4);
    mapRef.current = map;
    let frame = 0;
    const onResize = () => {
      if (!mapRef.current) return;
      map.invalidateSize();
    };
    window.addEventListener("resize", onResize);
    frame = requestAnimationFrame(onResize);
    return () => {
      cancelAnimationFrame(frame);
      mapRef.current = null;
      window.removeEventListener("resize", onResize);
      map.remove();
      layersRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    const layers = layersRef.current;
    if (!map || !layers) return;
    const { streets, satellite, placeNames } = layers;
    if (base === "satellite") {
      if (map.hasLayer(streets)) map.removeLayer(streets);
      satellite.addTo(map);
      placeNames.addTo(map);
    } else {
      if (map.hasLayer(satellite)) map.removeLayer(satellite);
      if (map.hasLayer(placeNames)) map.removeLayer(placeNames);
      streets.addTo(map);
    }
  }, [base]);

  useEffect(() => {
    if (!menu) return undefined;
    const close = (event) => {
      if (!layerBoxRef.current?.contains(event.target)) setMenu(false);
    };
    document.addEventListener("pointerdown", close);
    return () => document.removeEventListener("pointerdown", close);
  }, [menu]);

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

    const text = token("--text", "#101828");
    const muted = token("--text-3", "#98A2B3");
    const surface = token("--surface", "#FFFFFF");
    const bounds = [];
    const labels = [];
    const spots = placeLabels(plan.routes, map);
    plan.routes.forEach((route, index) => {
      const positions = routePositions(route);
      const gray = L.polyline(positions, {
        pane: "routes",
        color: muted,
        weight: 3,
        opacity: 0.9,
        lineCap: "round",
        lineJoin: "round",
      });
      gray.on("mouseover", () => {
        if (selectedRef.current === route.id || loadingRef.current) return;
        gray.setStyle({ weight: 4 });
      });
      gray.on("mouseout", () => {
        if (selectedRef.current === route.id) return;
        gray.setStyle({ weight: 3 });
      });
      gray.on("click", () => onSelectRef.current?.(route.id));
      const casing = L.polyline(positions, {
        pane: "routes",
        color: surface,
        weight: 9,
        opacity: 0,
        interactive: false,
        lineCap: "round",
        lineJoin: "round",
      });
      const segments = route.segments.map((segment) => {
        const line = L.polyline(segment.positions, {
          pane: "routes",
          color: RISK_COLORS[segment.level] || RISK_COLORS.Unassessed,
          weight: 5,
          opacity: 0,
          lineCap: "round",
          lineJoin: "round",
        });
        line.on("click", () => onSelectRef.current?.(route.id));
        segment.positions.forEach((position) => bounds.push(position));
        return line;
      });
      const lineGroup = L.featureGroup([gray, casing, ...segments]);
      const mid = spots[index];
      let label = null;
      if (mid) {
        label = L.circleMarker(mid, {
          pane: "checks",
          radius: 1,
          opacity: 0,
          fillOpacity: 0,
          interactive: false,
          summary: true,
          routeId: route.id,
        });
        label.bindTooltip(chipHtml(route), {
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
      linesRef.current.push({ id: route.id, gray, casing, segments });

      const markers = [];
      route.checkpoints.forEach((point, pointIndex) => {
        const marker = L.circleMarker([point.lat, point.lng], {
          pane: "checks",
          radius: 4,
          color: surface,
          weight: 2,
          fillColor: RISK_COLORS[point.level] || RISK_COLORS.Unassessed,
          fillOpacity: 0,
          opacity: 0,
        });
        marker.bindTooltip(tipLine(point), {
          direction: "top",
          className: "checkpoint-tip",
          opacity: 1,
        });
        marker.bindPopup(popupHtml(point), { closeButton: false, autoPan: false });
        marker.on("popupopen", () => {
          const button = marker.getPopup()?.getElement()?.querySelector(".popup-close");
          if (button) button.onclick = () => marker.closePopup();
        });
        marker.on("mouseover", () => marker.setRadius(6));
        marker.on("mouseout", () => {
          const hot = highlightRef.current
            && highlightRef.current.routeId === route.id
            && highlightRef.current.index === pointIndex;
          marker.setRadius(hot ? 6 : 4);
        });
        marker.on("click", () => onSelectRef.current?.(route.id));
        marker.addTo(marks);
        markers.push({ marker, index: pointIndex });
      });
      marksRef.current.push({ id: route.id, markers });
    });
    labelsRef.current = labels;

    if (plan.origin) {
      const marker = L.circleMarker([plan.origin.lat, plan.origin.lng], {
        pane: "checks",
        radius: 6,
        color: text,
        weight: 3,
        fillColor: surface,
        fillOpacity: 1,
      });
      marker.bindTooltip(esc(plan.origin.name), { direction: "top", className: "place-tip", opacity: 1 });
      marker.addTo(marks);
      bounds.push([plan.origin.lat, plan.origin.lng]);
    }
    if (plan.destination) {
      L.circleMarker([plan.destination.lat, plan.destination.lng], {
        pane: "checks",
        radius: 8,
        color: text,
        weight: 1,
        fillOpacity: 0,
        interactive: false,
      }).addTo(marks);
      const marker = L.circleMarker([plan.destination.lat, plan.destination.lng], {
        pane: "checks",
        radius: 6,
        color: surface,
        weight: 3,
        fillColor: text,
        fillOpacity: 1,
      });
      marker.bindTooltip(esc(plan.destination.name), { direction: "top", className: "place-tip", opacity: 1 });
      marker.addTo(marks);
      bounds.push([plan.destination.lat, plan.destination.lng]);
    }

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
    linesRef.current.forEach((entry) => {
      const on = entry.id === selected;
      const shown = loading ? 0.4 : 1;
      entry.gray.setStyle({ opacity: on ? 0 : (loading ? 0.4 : 0.9), weight: 3 });
      entry.casing.setStyle({ opacity: on ? shown : 0 });
      entry.gray.options.interactive = !on;
      entry.segments.forEach((line) => {
        line.options.interactive = on;
        line.setStyle({ opacity: on ? shown : 0, weight: 5 });
        if (line._path) line._path.style.pointerEvents = on ? "auto" : "none";
      });
      if (entry.gray._path) entry.gray._path.style.pointerEvents = on ? "none" : "auto";
      if (on) {
        entry.casing.bringToFront();
        entry.segments.forEach((line) => line.bringToFront());
      } else if (entry.gray.bringToBack) {
        entry.gray.bringToBack();
      }
    });
    marksRef.current.forEach((entry) => {
      const on = entry.id === selected;
      entry.markers.forEach(({ marker, index }) => {
        const hot = highlightCheckpoint
          && highlightCheckpoint.routeId === entry.id
          && highlightCheckpoint.index === index;
        const shown = on ? (loading ? 0.4 : 1) : 0;
        marker.options.interactive = on;
        if (!on && marker.isPopupOpen?.()) marker.closePopup();
        marker.setStyle({ opacity: shown, fillOpacity: shown });
        marker.setRadius(on && hot ? 6 : 4);
        if (marker._path) marker._path.style.pointerEvents = on ? "auto" : "none";
      });
    });
    labelsRef.current.forEach((label) => {
      if (!label) return;
      const tip = label.getTooltip();
      if (!tip) return;
      const on = label.options.routeId === selected;
      const el = tip.getElement();
      if (el) {
        el.classList.toggle("selected", on);
        el.style.opacity = loading ? "0.4" : "1";
      }
      if (on) tip.bringToFront();
    });
  }, [plan, selected, loading, highlightCheckpoint]);

  useEffect(() => {
    if (!focusCheckpoint || !mapRef.current) return;
    const entry = marksRef.current.find((item) => item.id === focusCheckpoint.routeId);
    const found = entry?.markers.find((item) => item.index === focusCheckpoint.index);
    if (!found) return;
    mapRef.current.panTo(found.marker.getLatLng());
    found.marker.openPopup();
  }, [focusCheckpoint]);

  return (
    <div className="map-wrap">
      {loading && <div className="load-bar" aria-hidden="true" />}
      <div ref={node} className="map" aria-label="Route map" />
      <div className="map-zoom">
        <IconButton label="Zoom in" onClick={() => mapRef.current?.zoomIn()}>
          <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
            <path d="M8 3.5v9M3.5 8h9" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          </svg>
        </IconButton>
        <IconButton label="Zoom out" onClick={() => mapRef.current?.zoomOut()}>
          <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
            <path d="M3.5 8h9" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          </svg>
        </IconButton>
      </div>
      <div className="map-layers" ref={layerBoxRef}>
        <button
          type="button"
          className="layer-btn"
          aria-label="Map layers"
          aria-expanded={menu}
          onClick={() => setMenu((open) => !open)}
        >
          <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
            <path d="M8 2.5 13.5 5.25 8 8 2.5 5.25 8 2.5zM2.5 8 8 10.75 13.5 8M2.5 10.75 8 13.5 13.5 10.75" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
          </svg>
        </button>
        {menu && (
          <div className="layer-menu" role="radiogroup" aria-label="Base map">
            <label>
              <input
                type="radio"
                name="basemap"
                checked={base === "map"}
                onChange={() => setBase("map")}
              />
              Map
            </label>
            <label>
              <input
                type="radio"
                name="basemap"
                checked={base === "satellite"}
                onChange={() => setBase("satellite")}
              />
              Satellite
            </label>
          </div>
        )}
      </div>
    </div>
  );
}
