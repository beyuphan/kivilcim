// Small helpers shared by every view: building elements, formatting, announcing.

export const $ = (selector, root = document) => root.querySelector(selector);
export { icon } from "./icons.js";

/** el("div", { class: "card", dataset: { id: 1 }, onclick: fn }, "text", child, [more, ...]) */
export function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value == null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "dataset") Object.assign(node.dataset, value);
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
    else if (key === "style" && typeof value === "object") Object.assign(node.style, value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  append(node, children);
  return node;
}

export function append(node, children) {
  for (const child of children.flat(Infinity)) {
    if (child == null || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function clear(node) {
  node.replaceChildren();
  return node;
}

// ---- formatting ---------------------------------------------------------------------------

const zone = "Europe/Istanbul";
const timeFormat = new Intl.DateTimeFormat("tr-TR", { timeZone: zone, day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
const longDay = new Intl.DateTimeFormat("tr-TR", { timeZone: "UTC", day: "numeric", month: "long", year: "numeric" });
const shortDay = new Intl.DateTimeFormat("tr-TR", { timeZone: "UTC", day: "numeric", month: "short" });
const month = new Intl.DateTimeFormat("tr-TR", { timeZone: "UTC", month: "long" });
const numbers = new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 0 });
const decimals = new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 1 });

export const parseDay = (iso) => new Date(iso + "T00:00:00Z");
export const fmt = {
  time: (iso) => timeFormat.format(new Date(iso)),
  day: (iso) => longDay.format(parseDay(iso.slice(0, 10))),
  shortDay: (iso) => shortDay.format(parseDay(iso.slice(0, 10))),
  month: (iso) => month.format(parseDay(iso.slice(0, 10))),
  range(start, end) {
    return start === end ? fmt.day(start) : `${fmt.shortDay(start)} - ${fmt.day(end)}`;
  },
  num: (n) => numbers.format(n),
  dec: (n) => decimals.format(n),
};

export function ago(minutes) {
  const m = Math.max(0, Math.round(minutes));
  if (m < 1) return "az önce";
  if (m < 60) return `${m} dk önce`;
  if (m < 1440) return `${Math.floor(m / 60)} sa ${m % 60} dk önce`;
  return `${Math.floor(m / 1440)} gün önce`;
}
export const agoFrom = (iso) => ago((Date.now() - new Date(iso).getTime()) / 60000);

// ---- domain words --------------------------------------------------------------------------

export const LEVELS = {
  low: "Düşük",
  moderate: "Orta",
  high: "Yüksek",
  very_high: "Çok yüksek",
  extreme: "Aşırı",
};
export const FACTORS = { humidity: "kuru hava", wind: "güçlü rüzgar", temperature: "sıcak hava", rain: "yağışsız dönem" };
export const CONFIDENCE = { high: "yüksek güven", nominal: "orta güven", low: "düşük güven" };
export const SATELLITES = { N: "Suomi NPP", N20: "NOAA-20", N21: "NOAA-21", Terra: "Terra", Aqua: "Aqua" };
export const satellite = (f) => `${SATELLITES[f.satellite] || f.satellite} · ${f.instrument}`;
export const COMPASS = ["K", "KKD", "KD", "DKD", "D", "DGD", "GD", "GGD", "G", "GGB", "GB", "BGB", "B", "BKB", "KB", "KKB"];

export function levelPill(risk, { plain = false } = {}) {
  const level = risk ? risk.level : "unknown";
  return el("span", { class: "pill" + (plain ? " pill-plain" : ""), dataset: { level } }, risk ? LEVELS[level] || level : "Hava yok");
}

export function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

// ---- announcing and focus --------------------------------------------------------------------

export function announce(text) {
  const live = document.getElementById("live");
  if (!live) return;
  live.textContent = "";
  // A change of text is what screen readers read out, so set it on the next frame.
  requestAnimationFrame(() => { live.textContent = text; });
}

export function debounce(fn, ms) {
  let timer;
  return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); };
}

export const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
