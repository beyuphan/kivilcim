// The shell: one map, one panel, and a small router that swaps what the panel and the map show.
// Views live in their own modules and are loaded only when first visited.

import { $, el, clear, announce, agoFrom, fmt } from "./ui.js";
import { api } from "./api.js";
import { createMap, getMap, addBaseSwitch } from "./map.js";
import { createSearch, fold, loadIndex } from "./search.js";
import { initSheet } from "./sheet.js";

const config = JSON.parse(document.getElementById("kv-config")?.textContent || "{}");
const panel = $("#panel");
const body = $("#panel-body");
const mapwrap = $("#mapwrap");
const sheet = initSheet(panel, $("#sheet-handle"));

createMap($("#map"), config);
const map = getMap();
const layer = L.layerGroup();
addBaseSwitch(mapwrap, layer);

// ---- search box ------------------------------------------------------------------------------

// A view can take over what happens when a place is picked in the search box (the past fires filter by it).
let pickHandler = null;

const search = createSearch({
  onPick: (entry) => pickHandler ? pickHandler(entry) : navigate(`/?lat=${entry.lat.toFixed(4)}&lon=${entry.lon.toFixed(4)}&r=${entry.radius}&name=${encodeURIComponent(entry.kind === "il" ? entry.name : `${entry.name}, ${entry.sub}`)}`),
  onLocate: locate,
});
$("#search-slot").replaceWith(search.root);

function locate() {
  if (!("geolocation" in navigator)) return announce("Bu cihaz konum vermiyor. İl veya ilçe arayın.");
  announce("Konum alınıyor");
  navigator.geolocation.getCurrentPosition(
    (p) => navigate(`/?lat=${p.coords.latitude.toFixed(4)}&lon=${p.coords.longitude.toFixed(4)}&r=25&name=${encodeURIComponent("Bulunduğum yer")}`),
    () => announce("Konum alınamadı. İl veya ilçe arayın."),
    { enableHighAccuracy: false, timeout: 10000, maximumAge: 300000 },
  );
}

// ---- router ----------------------------------------------------------------------------------

let current = null; // { controller, cleanups }
const modules = {};

const slugOf = (name) => fold(name).replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");

function route(url) {
  const path = url.pathname.replace(/\/+$/, "") || "/";
  if (path === "/check") return { name: "check", module: "check", nav: "check", params: url.searchParams };
  if (path === "/history") return { name: "history", module: "history", nav: "history" };
  const event = path.match(/^\/history\/([^/]+)$/);
  if (event) return { name: "event", module: "event", nav: "history", params: { id: decodeURIComponent(event[1]) } };
  if (path === "/" && url.searchParams.has("lat")) return { name: "place", module: "place", nav: "map", params: url.searchParams };
  if (path === "/") return { name: "home", module: "home", nav: "map", params: url.searchParams };
  return null;
}

export function navigate(href, { replace = false } = {}) {
  const url = new URL(href, location.origin);
  if (url.origin !== location.origin || !(route(url) || url.pathname.startsWith("/il/"))) { location.assign(href); return; }
  if (url.pathname + url.search !== location.pathname + location.search) history[replace ? "replaceState" : "pushState"]({}, "", url);
  render();
}

async function render() {
  const url = new URL(location.href);
  let target = route(url);
  // A province page (/il/nigde) shows that province on the map.
  const province = url.pathname.match(/^\/il\/([a-z0-9-]+)\/?$/);
  if (province) {
    const entries = await loadIndex().catch(() => []);
    const found = entries.find((e) => e.kind === "il" && slugOf(e.name) === province[1]);
    target = found
      ? { name: "place", module: "place", nav: "map", params: new URLSearchParams({ lat: found.lat, lon: found.lon, r: 100, name: found.name }) }
      : { name: "home", module: "home", nav: "map", params: url.searchParams };
  }
  if (!target) return;
  if (current) {
    current.controller.abort();
    current.cleanups.forEach((fn) => { try { fn(); } catch { /* a view failing to clean up must not block the next */ } });
  }
  const controller = new AbortController();
  const cleanups = [];
  current = { controller, cleanups };
  layer.clearLayers();
  clear(body);
  panel.scrollTop = 0;
  document.querySelectorAll(".nav a").forEach((a) => {
    if (a.dataset.nav === target.nav) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  if (matchMedia("(max-width: 899px)").matches && target.name !== "home") sheet.set("half");

  const ctx = {
    map, layer, body, mapwrap, panel,
    signal: controller.signal,
    navigate,
    onCleanup: (fn) => cleanups.push(fn),
    setPick(fn) { pickHandler = fn; cleanups.push(() => { if (pickHandler === fn) pickHandler = null; }); },
    setTitle(text) {
      document.title = text === "Aktif yangın bölgeleri" ? "Kıvılcım · Türkiye'de uydudan yangın izleme" : `${text} · Kıvılcım`;
      announce(text);
    },
  };
  try {
    modules[target.module] ||= await import(`./${target.module}.js`);
    if (controller.signal.aborted) return;
    modules[target.module].mount(ctx, target.params);
  } catch (error) {
    if (controller.signal.aborted) return;
    console.error(error);
    body.append(el("div", { class: "card empty" }, el("h3", {}, "Bir şey ters gitti"), el("p", {}, "Sayfayı yenileyin. Sorun sürerse daha sonra tekrar deneyin."), el("a", { class: "btn btn-secondary", href: "/", "data-link": "" }, "Ana sayfa")));
  }
}

document.addEventListener("click", (event) => {
  const link = event.target.closest?.("a[data-link]");
  if (!link || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  event.preventDefault();
  navigate(link.getAttribute("href"));
});
window.addEventListener("popstate", render);

// ---- data status chip --------------------------------------------------------------------------

const chip = $("#status-chip");
const FRESH = { fresh: ["Güncel", "fresh"], warning: ["Gecikmeli", "warning"], stale: ["Eski veri", "stale"], unknown: ["Bilinmiyor", "unknown"] };
let popover = null;

async function refreshStatus() {
  try {
    const status = await api.status();
    const [label, state] = FRESH[status.data_as_of.freshness] || FRESH.unknown;
    chip.dataset.state = state;
    chip.querySelector(".label").textContent = label;
    chip.setAttribute("aria-label", `Veri durumu: ${label}. Ayrıntı için dokunun.`);
    chip.__status = status;
  } catch { /* the chip keeps its last value */ }
}

function togglePopover() {
  if (popover) { popover.remove(); popover = null; chip.setAttribute("aria-expanded", "false"); return; }
  const s = chip.__status;
  if (!s) return;
  const d = s.data_as_of;
  popover = el("div", { class: "popover", role: "dialog", "aria-label": "Veri durumu" },
    el("h3", { class: "card-title" }, "Verinin durumu"),
    el("dl", {},
      el("dt", {}, "Son gözlem"), el("dd", {}, d.latest_observation_at ? `${fmt.time(d.latest_observation_at)} (${agoFrom(d.latest_observation_at)})` : "yok"),
      el("dt", {}, "Son güncelleme"), el("dd", {}, d.last_ingest_at ? agoFrom(d.last_ingest_at) : "yok"),
      el("dt", {}, "Hava sorgusu"), el("dd", {}, `${fmt.num(s.weather_calls_today)} / ${fmt.num(s.weather_daily_quota)}`)),
    el("p", { class: "card-sub" }, s.disclaimer),
    el("a", { href: "/methodology" }, "Veri ve yöntem hakkında"));
  document.body.append(popover);
  chip.setAttribute("aria-expanded", "true");
  const close = (e) => {
    if (e.type === "keydown" && e.key !== "Escape") return;
    if (e.type === "click" && (popover?.contains(e.target) || chip.contains(e.target))) return;
    popover?.remove(); popover = null; chip.setAttribute("aria-expanded", "false");
    document.removeEventListener("click", close); document.removeEventListener("keydown", close);
    if (e.type === "keydown") chip.focus();
  };
  setTimeout(() => { document.addEventListener("click", close); document.addEventListener("keydown", close); }, 0);
}

chip.addEventListener("click", togglePopover);
refreshStatus();
setInterval(refreshStatus, 120000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) refreshStatus(); });

// Leaflet needs to be told when the map's size changes (rotation, window resize).
new ResizeObserver(() => map.invalidateSize({ pan: false })).observe(mapwrap);

render();
