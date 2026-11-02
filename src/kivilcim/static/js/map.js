// The map and the layers drawn on it. Pins are real page elements (so they can be clicked, focused with
// the keyboard and read by a screen reader); the many small dots of a fire's spread are drawn on one canvas.

import { el, fmt } from "./ui.js";

export const TURKEY = [[35.8, 25.6], [42.2, 44.9]];

let map = null;
let canvas = null;
let mapwrap = null;
let osm = null;
let cloudless = null;
let daily = null;
let current = null;

export function getMap() { return map; }
export function getCanvas() { return canvas; }

// ---- base layers -------------------------------------------------------------------------------------

// A mosaic of the clearest Sentinel-2 pictures of a whole year, with the clouds removed (EOX, s2cloudless).
// It is the default: a picture of the ground with no cloud over it, however cloudy the day is.
const CLOUDLESS = "https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2024_3857/default/g/{z}/{y}/{x}.jpg";
const CLOUDLESS_CREDIT = 'Sentinel-2 cloudless <a href="https://s2maps.eu" target="_blank" rel="noopener">s2maps.eu</a> by EOX (contains modified Copernicus Sentinel data 2024)';
// Yesterday's daily picture of the region (250 m): clouds included on purpose, to tell them from smoke.
const DAILY = "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/VIIRS_SNPP_CorrectedReflectance_TrueColor/default/{date}/GoogleMapsCompatible_Level9/{z}/{y}/{x}.jpg";

function savedBase() {
  try {
    const value = localStorage.getItem("kv-base");
    return ["map", "sat", "daily"].includes(value) ? value : "sat";
  } catch {
    return "sat";
  }
}

export function createMap(element, config) {
  map = L.map(element, {
    preferCanvas: true,
    zoomControl: false,
    attributionControl: true,
    zoomSnap: 0.5,
    minZoom: 4,
    maxZoom: 16,
    worldCopyJump: false,
  });
  canvas = L.canvas({ padding: 0.4 });
  map.attributionControl.setPrefix('<a href="https://leafletjs.com">Leaflet</a>');
  L.control.zoom({ position: "bottomright", zoomInTitle: "Yakınlaştır", zoomOutTitle: "Uzaklaştır" }).addTo(map);
  osm = L.tileLayer(config.tileUrl, { maxZoom: 18, attribution: config.tileAttribution, updateWhenIdle: true, keepBuffer: 2, crossOrigin: false });
  cloudless = L.tileLayer(CLOUDLESS, { maxNativeZoom: 14, maxZoom: 18, attribution: CLOUDLESS_CREDIT, updateWhenIdle: true, keepBuffer: 2, crossOrigin: false });
  current = savedBase();
  const first = current === "map" ? osm : current === "sat" ? cloudless : cloudless; // "daily" starts on the cloudless one
  first.addTo(map);
  watchLayer(first, "base", current === "map" ? "Harita yükleniyor" : "Uydu görüntüsü yükleniyor");
  map.fitBounds(TURKEY, { animate: false, ...padding() });
  return map;
}

/**
 * The switch between the street map, the cloudless satellite picture and yesterday's picture. The new
 * layer fades in over the old one when its tiles have arrived, so the map never goes blank, and a
 * message with a spinner says it is loading.
 */
export function addBaseSwitch(wrap) {
  mapwrap = wrap;
  const date = new Date(Date.now() - 86400000).toISOString().slice(0, 10);
  const note = el("small", { hidden: true });
  const options = [["sat", "Uydu"], ["map", "Harita"], ["daily", "Dünkü"]];
  const buttons = options.map(([key, text]) => el("button", { type: "button", "aria-pressed": String(key === current), onclick: () => choose(key) }, text));
  const card = el("div", { class: "map-card map-toggle" }, el("div", { class: "segmented", role: "group", "aria-label": "Harita türü" }, buttons), note);
  wrap.append(card);
  describeBase();

  function describeBase() {
    note.hidden = current !== "daily";
    note.textContent = `Dünkü gerçek uydu görüntüsü (${fmt.shortDay(date)}, 250 m), bulutlar dahil. Kalın beyaz olan bulut, ince gri-kahverengi şerit duman olabilir.`;
  }

  function layerFor(key) {
    if (key === "map") return osm;
    if (key === "sat") return cloudless;
    daily ||= L.tileLayer(DAILY.replace("{date}", date), { maxNativeZoom: 9, maxZoom: 18, updateWhenIdle: true, attribution: "NASA GIBS / ESDIS", crossOrigin: false });
    return daily;
  }

  function choose(key) {
    if (key === current) return;
    const old = layerFor(current);
    const next = layerFor(key);
    current = key;
    try { localStorage.setItem("kv-base", key); } catch { /* the choice just is not remembered */ }
    buttons.forEach((b, i) => b.setAttribute("aria-pressed", String(options[i][0] === key)));
    describeBase();
    // The new layer starts invisible and fades in once it has its tiles; only then is the old one removed.
    next.setOpacity(0);
    next.addTo(map);
    const label = key === "map" ? "Harita yükleniyor" : key === "daily" ? "Dünkü görüntü yükleniyor" : "Bulutsuz uydu görüntüsü yükleniyor";
    loading("base", label);
    const reveal = () => {
      if (current !== key) return;
      next.setOpacity(1);
      if (old !== next) setTimeout(() => { if (current === key) map.removeLayer(old); }, 450);
      loaded("base");
    };
    next.once("load", reveal);
    setTimeout(reveal, 8000); // never leave the new layer invisible if a tile is slow
  }
}

// ---- the loading message ------------------------------------------------------------------------------

const pending = new Map();
let loader = null;

function renderLoader() {
  if (!mapwrap) return;
  if (!loader) {
    loader = el("div", { class: "map-loader", role: "status", hidden: true },
      el("span", { class: "spinner", "aria-hidden": "true" }), el("span", { class: "map-loader-text" }), el("i", { class: "map-loader-bar", "aria-hidden": "true" }));
    mapwrap.append(loader);
  }
  const texts = [...pending.values()];
  loader.hidden = !texts.length;
  loader.querySelector(".map-loader-text").textContent = texts[texts.length - 1] || "";
}

/** Show "loading" with a text, under a key. Several can be pending; the newest text is shown. */
export function loading(key, text) {
  pending.delete(key);
  pending.set(key, text);
  renderLoader();
}

export function loaded(key) {
  pending.delete(key);
  renderLoader();
}

/** Show the loading message while a tile layer fetches its tiles. */
export function watchLayer(layer, key, text) {
  layer.on("loading", () => loading(key, text));
  layer.on("load", () => loaded(key));
}

// ---- where to look ----------------------------------------------------------------------------------

/** How much of the map the panel covers, so fitted views land in the part you can see. */
export function padding() {
  const panel = document.getElementById("panel");
  if (!panel) return { padding: [16, 16] };
  if (matchMedia("(max-width: 899px)").matches) {
    const visible = panel.getBoundingClientRect();
    const covered = Math.max(0, window.innerHeight - visible.top - 56);
    return { paddingTopLeft: [16, 76], paddingBottomRight: [16, Math.min(covered, window.innerHeight * 0.6) + 16] };
  }
  const covered = panel.getBoundingClientRect().right + 16;
  return { paddingTopLeft: [covered, 84], paddingBottomRight: [72, 24] };
}

export function fit(bounds, options = {}) {
  map.fitBounds(bounds, { animate: !matchMedia("(prefers-reduced-motion: reduce)").matches, maxZoom: 12, ...padding(), ...options });
}

export function flyTo(lat, lon, zoom) {
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const wide = !matchMedia("(max-width: 899px)").matches;
  // Look at the point from the middle of the part of the map that is not under the panel.
  const target = map.project([lat, lon], zoom);
  const panel = document.getElementById("panel");
  const shift = wide ? [-(panel ? panel.getBoundingClientRect().right + 16 : 0) / 2, 0] : [0, Math.min(window.innerHeight * 0.2, 160)];
  const center = map.unproject([target.x + shift[0], target.y + shift[1]], zoom);
  if (reduce) map.setView(center, zoom, { animate: false });
  else map.flyTo(center, zoom, { duration: 0.8 });
}

// ---- colors -----------------------------------------------------------------------------------------

/** The solid color of a danger level, read from the design tokens. */
export function levelColor(level) {
  const style = getComputedStyle(document.documentElement);
  return (style.getPropertyValue(`--lv-${level || "unknown"}`) || style.getPropertyValue("--lv-unknown")).trim();
}

// Heat: the stronger the fire seen by the satellite, the deeper the color (amber, orange, red, crimson).
const HEAT = [[5, [255, 213, 74]], [30, [255, 162, 28]], [120, [255, 106, 26]], [400, [240, 48, 43]], [1200, [179, 0, 47]]];
export function heatColor(frp) {
  const x = Math.log10(Math.max(frp, 1));
  const stops = HEAT.map(([v, c]) => [Math.log10(v), c]);
  if (x <= stops[0][0]) return rgb(stops[0][1]);
  for (let i = 1; i < stops.length; i++) {
    if (x <= stops[i][0]) {
      const f = (x - stops[i - 1][0]) / (stops[i][0] - stops[i - 1][0]);
      return rgb(stops[i - 1][1].map((v, k) => v + (stops[i][1][k] - v) * f));
    }
  }
  return rgb(stops[stops.length - 1][1]);
}
const rgb = (c) => `rgb(${c.map((v) => Math.round(v)).join(" ")})`;

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

// ---- pins -------------------------------------------------------------------------------------------

/**
 * A pin on the map: a real element that can be clicked or reached with the keyboard.
 * `label` is the text inside (a number), `title` what a screen reader says, `content` a node shown in
 * a card when the pin is clicked.
 */
export function pin(latlng, { color, label = "", size = 34, title = "", content = null, pulse = true, onSelect = null }) {
  const icon = L.divIcon({
    className: "pin-wrap",
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
    html: `<span class="pin${pulse ? " pin-pulse" : ""}" style="--c:${color};--s:${size}px">${label ? `<b>${label}</b>` : ""}</span>`,
  });
  const marker = L.marker(latlng, { icon, title, alt: title, keyboard: true, riseOnHover: true, bubblingMouseEvents: false });
  if (content) {
    marker.bindPopup(content, { className: "kv-popup", offset: [0, -size / 2 + 4], maxWidth: 300, minWidth: 220, autoPanPaddingTopLeft: [padding().paddingTopLeft?.[0] ?? 16, 90], closeButton: true });
  }
  marker.on("click", () => onSelect?.(marker));
  return marker;
}
