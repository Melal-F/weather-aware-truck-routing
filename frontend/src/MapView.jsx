import { useEffect, useRef } from "react";
import L from "leaflet";
import { RISK_COLORS, formatWhen } from "./api";

const HeatLayer = L.Layer.extend({
  initialize(points) {
    this._points = points || [];
    this._hour = 0;
  },

  setHour(hour) {
    this._hour = hour;
    if (this._map) this._paint();
  },

  onAdd(map) {
    this._map = map;
    this._canvas = L.DomUtil.create("canvas", "corridor-heat");
    this._canvas.style.pointerEvents = "none";
    map.getPanes().overlayPane.appendChild(this._canvas);
    this._onMove = () => {
      if (this._frame) return;
      this._frame = requestAnimationFrame(() => {
        this._frame = 0;
        this._paint();
      });
    };
    map.on("move zoom resize", this._onMove, this);
    this._paint();
  },

  onRemove(map) {
    map.off("move zoom resize", this._onMove, this);
    if (this._frame) cancelAnimationFrame(this._frame);
    this._canvas.remove();
    this._map = null;
  },

  _paint() {
    const map = this._map;
    const canvas = this._canvas;
    if (!map || !canvas) return;
    const size = map.getSize();
    const ratio = window.devicePixelRatio || 1;
    canvas.width = Math.max(1, size.x) * ratio;
    canvas.height = Math.max(1, size.y) * ratio;
    canvas.style.width = `${size.x}px`;
    canvas.style.height = `${size.y}px`;
    L.DomUtil.setPosition(canvas, map.containerPointToLayerPoint([0, 0]));
    const ctx = canvas.getContext("2d");
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, size.x, size.y);
    const hour = this._hour;
    for (const point of this._points) {
      const level = point.risks[hour];
      const at = map.latLngToContainerPoint([point.lat, point.lng]);
      if (at.x < -40 || at.y < -40 || at.x > size.x + 40 || at.y > size.y + 40) continue;
      ctx.beginPath();
      ctx.fillStyle = RISK_COLORS[level] || "#8a8a8a";
      ctx.globalAlpha = level ? 0.42 : 0.18;
      ctx.arc(at.x, at.y, 18, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.globalAlpha = 1;
  },
});

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

export default function MapView({ plan, selected, hour, onSelect }) {
  const node = useRef(null);
  const mapRef = useRef(null);
  const heatRef = useRef(null);
  const linesRef = useRef([]);
  const marksRef = useRef([]);
  const hourRef = useRef(hour);
  const onSelectRef = useRef(onSelect);
  hourRef.current = hour;
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
        marker.bindPopup(popupHtml(point));
        marker.on("click", () => onSelectRef.current?.(route.id));
        markGroup.addLayer(marker);
      });
      markGroup.addTo(marks);
      marksRef.current.push({ id: route.id, group: markGroup });
    });

    const heat = new HeatLayer(plan.corridor);
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
    const paint = (entry, on) => {
      entry.group.eachLayer((layer) => {
        if (!layer.setStyle) return;
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
