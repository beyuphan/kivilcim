// The map and the layers drawn on it. Markers are drawn on one canvas, so hundreds of them cost
// about as much as a few; the base tiles are only requested when the map stops moving.

import { cssVar } from "./ui.js";

export const TURKEY = [[35.8, 25.6], [42.2, 44.9]];
const BLANK_TILE = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7";
export { BLANK_TILE };

let map = null;
let canvas = null;

export function getMap() { return map; }
export function getCanvas() { return canvas; }

export function createMap(element, config) {
  map = L.map(element, {
    preferCanvas: true,
    zoomControl: false,
    attributionControl: true,
    zoomSnap: 0.5,
    minZoom: 4,
    maxZoom: 15,
    worldCopyJump: false,
  });
  canvas = L.canvas({ padding: 0.4 });
  map.attributionControl.setPrefix('<a href="https://leafletjs.com">Leaflet</a>');
  L.control.zoom({ position: "topright", zoomInTitle: "Yakınlaştır", zoomOutTitle: "Uzaklaştır" }).addTo(map);
  L.tileLayer(config.tileUrl, {
    maxZoom: 18,
    attribution: config.tileAttribution,
    updateWhenIdle: true,
    keepBuffer: 2,
    crossOrigin: false,
  }).addTo(map);
  map.fitBounds(TURKEY, { animate: false, ...padding() });
  return map;
}

/** How much of the map the panel covers, so fitted views land in the part you can see. */
export function padding() {
  const panel = document.getElementById("panel");
  if (!panel) return { padding: [16, 16] };
  const narrow = matchMedia("(max-width: 899px)").matches;
  if (narrow) {
    const visible = panel.getBoundingClientRect();
    const covered = Math.max(0, window.innerHeight - visible.top - 56);
    return { paddingTopLeft: [16, 64], paddingBottomRight: [16, Math.min(covered, window.innerHeight * 0.6) + 16] };
  }
  return { paddingTopLeft: [16, 16], paddingBottomRight: [60, 16] };
}

export function fit(bounds, options = {}) {
  map.fitBounds(bounds, { animate: !matchMedia("(prefers-reduced-motion: reduce)").matches, maxZoom: 12, ...padding(), ...options });
}

export function flyTo(lat, lon, zoom) {
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const wide = !matchMedia("(max-width: 899px)").matches;
  // Look at the point from the middle of the visible part of the map, not the middle of the whole map.
  const target = map.project([lat, lon], zoom);
  const shift = wide ? [0, 0] : [0, Math.min(window.innerHeight * 0.2, 160)];
  const center = map.unproject([target.x + shift[0], target.y + shift[1]], zoom);
  if (reduce) map.setView(center, zoom, { animate: false });
  else map.flyTo(center, zoom, { duration: 0.8 });
}

// ---- colors from the design tokens ----------------------------------------------------------------

/** The solid color of a danger level. Read from CSS each time so dark mode is followed. */
export function levelColor(level) {
  return cssVar(`--lv-${level || "unknown"}`) || cssVar("--lv-unknown");
}

// A dark-to-light ramp for time (early to late): dark violet, magenta, orange, yellow.
const TIME_STOPS = ["#2b1a7a", "#6a1b9a", "#b5367a", "#ee6c4d", "#f9b233", "#fde725"];
export function timeColor(t) {
  const x = Math.min(Math.max(t, 0), 1) * (TIME_STOPS.length - 1);
  const i = Math.min(Math.floor(x), TIME_STOPS.length - 2);
  const f = x - i;
  const a = TIME_STOPS[i].match(/\w\w/g).map((h) => parseInt(h, 16));
  const b = TIME_STOPS[i + 1].match(/\w\w/g).map((h) => parseInt(h, 16));
  return "#" + a.map((v, k) => Math.round(v + (b[k] - v) * f).toString(16).padStart(2, "0")).join("");
}

export function heatRadius(frp, min = 6, max = 22, scale = 1.7) {
  return Math.min(max, min + Math.sqrt(Math.max(frp, 0)) / scale);
}
