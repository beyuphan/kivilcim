// How a fire spread. The satellite sees a fire as pixels of about 375 m; each detection is drawn as the
// square it covers, once, on the first day it was seen, so the burning area grows on the map as the days
// pass. The panel says how large the area is each day, with a chart of its growth and a player.
//
// The area counted here is the area of the pixels in which the satellite saw fire. It is not a measurement
// of burned land: a pixel is marked if fire burned anywhere in it, and pixels near the edge of a scan are
// larger than 375 m.

import { el, clear, icon, fmt, sleep, announce } from "./ui.js";
import { getCanvas, timeColor, pin, heatColor } from "./map.js";

const CELL_KM = 0.375;
const CELL_AREA = CELL_KM * CELL_KM; // km², one VIIRS pixel at nadir
const KM_PER_DEG = 111.32;
const DAY = 86400000;
const SVG = "http://www.w3.org/2000/svg";

const svg = (tag, attrs = {}, ...children) => {
  const node = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, String(v));
  children.forEach((c) => node.append(c));
  return node;
};

/** Cells and days of an event: where fire was first seen, and when. */
export function buildFootprint(detail) {
  const e = detail.event;
  const pad = 0.03;
  const from = Date.parse(e.start);
  const to = Date.parse(e.end);
  const lat0 = e.center_lat;
  const kmLon = KM_PER_DEG * Math.cos((lat0 * Math.PI) / 180);
  const cells = new Map(); // key -> { cy, cx, day, frp }
  let first = null; // the strongest detection of the first day, inside the fire's box
  let strongest = null;
  for (const d of detail.detections) {
    if (d.latitude < e.min_lat - pad || d.latitude > e.max_lat + pad || d.longitude < e.min_lon - pad || d.longitude > e.max_lon + pad) continue;
    const t = Date.parse(d.acquired_at.slice(0, 10));
    if (t < from - DAY || t > to + DAY) continue;
    const day = Math.min(Math.max(Math.round((t - from) / DAY), 0), Math.round((to - from) / DAY));
    const cy = Math.floor((d.latitude * KM_PER_DEG) / CELL_KM);
    const cx = Math.floor((d.longitude * kmLon) / CELL_KM);
    const key = `${cy}:${cx}`;
    if ((d.frp ?? 0) > (strongest?.frp ?? -1)) strongest = d;
    if (day === 0 && (d.frp ?? 0) > (first?.frp ?? -1)) first = d;
    const seen = cells.get(key);
    if (!seen) cells.set(key, { cy, cx, day, frp: d.frp ?? 0 });
    else {
      seen.day = Math.min(seen.day, day);
      seen.frp = Math.max(seen.frp, d.frp ?? 0);
    }
  }
  const count = Math.round((to - from) / DAY) + 1;
  const added = new Array(count).fill(0);
  for (const c of cells.values()) added[c.day] += 1;
  let total = 0;
  const cumulative = added.map((n) => (total += n));
  return {
    cells: [...cells.values()], kmLon, count, start: e.start,
    added: added.map((n) => n * CELL_AREA), cumulative: cumulative.map((n) => n * CELL_AREA),
    first, strongest,
    dayLabel: (i) => new Date(from + i * DAY).toISOString().slice(0, 10),
  };
}

function bounds(cell, kmLon) {
  const south = (cell.cy * CELL_KM) / KM_PER_DEG;
  const west = (cell.cx * CELL_KM) / kmLon;
  return [[south, west], [south + CELL_KM / KM_PER_DEG, west + CELL_KM / kmLon]];
}

export function createSpread(ctx, detail, box) {
  const { map } = ctx;
  const fp = buildFootprint(detail);
  const group = L.layerGroup().addTo(map);
  const byDay = Array.from({ length: fp.count }, () => L.featureGroup());
  const rects = [];
  const renderer = getCanvas();
  for (const c of fp.cells) {
    const rect = L.rectangle(bounds(c, fp.kmLon), { renderer, interactive: false, stroke: false, fillOpacity: 0.8, fillColor: timeColor(fp.count > 1 ? c.day / (fp.count - 1) : 1) });
    rect.__day = c.day;
    byDay[c.day].addLayer(rect);
    rects.push(rect);
  }
  let ignition = null;
  if (fp.first) {
    ignition = pin([fp.first.latitude, fp.first.longitude], {
      color: heatColor(fp.first.frp ?? 30), size: 34, pulse: true, title: "İlk tespit",
      content: el("div", { class: "pop" }, el("b", {}, "İlk tespit"), el("span", { class: "pop-meta" }, `${fmt.time(fp.first.acquired_at)}${fp.first.frp != null ? ` · ${fmt.dec(fp.first.frp)} MW` : ""}`)),
    });
    ignition.bindTooltip("İlk tespit", { direction: "top", className: "marker-label", offset: [0, -17] });
  }

  let mode = "outline"; // "outline": a thin outline of everything, over the satellite pictures; "play": the growth
  let day = fp.count - 1;
  let playing = false;
  let speed = 1;
  let token = 0;

  function paint() {
    group.clearLayers();
    if (mode === "outline") {
      rects.forEach((r) => r.setStyle({ fill: false, stroke: true, color: "#FF8A1F", weight: 1, opacity: 0.85 }));
      byDay.forEach((g) => group.addLayer(g));
      ignition?.remove();
      return;
    }
    byDay.forEach((g, i) => { if (i <= day) group.addLayer(g); });
    byDay.forEach((g, i) => g.eachLayer((r) => {
      const today = i === day;
      r.setStyle(today
        ? { fill: true, fillColor: "#FFF1C2", fillOpacity: 0.95, stroke: true, color: "#FFFFFF", weight: 1, opacity: 1 }
        : { fill: true, fillColor: timeColor(fp.count > 1 ? i / (fp.count - 1) : 1), fillOpacity: 0.78, stroke: false });
    }));
    if (ignition) ignition.addTo(group);
  }

  // ---- the panel -------------------------------------------------------------------------------------

  const total = fp.cumulative[fp.cumulative.length - 1] || 0;
  const peak = fp.added.reduce((best, v, i) => (v > fp.added[best] ? i : best), 0);

  function chart() {
    const W = 360;
    const H = 110;
    const pad = { l: 4, r: 4, t: 8, b: 4 };
    const x = (i) => pad.l + (fp.count > 1 ? (i / (fp.count - 1)) * (W - pad.l - pad.r) : (W - pad.l - pad.r) / 2);
    const y = (v) => H - pad.b - (total ? (v / total) * (H - pad.t - pad.b) : 0);
    const barW = Math.max(3, (W - pad.l - pad.r) / fp.count - 4);
    const maxAdded = Math.max(...fp.added, 0.0001);
    const line = fp.cumulative.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
    const area = `${line} L${x(fp.count - 1).toFixed(1)},${H - pad.b} L${x(0).toFixed(1)},${H - pad.b} Z`;
    const node = svg("svg", { viewBox: `0 0 ${W} ${H}`, class: "spread-chart", role: "img", "aria-label": `Yangının uydunun gördüğü alanı günlere göre: toplam ${fmt.num(Math.round(total))} kilometrekare` },
      svg("defs", {}, svg("linearGradient", { id: "sg", x1: 0, y1: 0, x2: 0, y2: 1 }, svg("stop", { offset: "0%", "stop-color": "#FF8A1F", "stop-opacity": 0.55 }), svg("stop", { offset: "100%", "stop-color": "#FF8A1F", "stop-opacity": 0.02 }))),
      svg("path", { d: area, fill: "url(#sg)" }),
      ...fp.added.map((v, i) => svg("rect", { x: x(i) - barW / 2, y: H - pad.b - (v / maxAdded) * 34, width: barW, height: Math.max(1, (v / maxAdded) * 34), rx: 2, fill: timeColor(fp.count > 1 ? i / (fp.count - 1) : 1), opacity: i === day ? 1 : 0.55 })),
      svg("path", { d: line, fill: "none", stroke: "#FFB066", "stroke-width": 2, "stroke-linejoin": "round" }),
      svg("line", { x1: x(day), x2: x(day), y1: 0, y2: H, stroke: "#fff", "stroke-width": 1.5, "stroke-dasharray": "3 3", opacity: 0.8 }),
      svg("circle", { cx: x(day), cy: y(fp.cumulative[day]), r: 5, fill: "#fff", stroke: "#FF6A1A", "stroke-width": 2.5 }));
    node.addEventListener("click", (ev) => {
      const r = node.getBoundingClientRect();
      const px = ((ev.clientX - r.left) / r.width) * W;
      const i = ((px - pad.l) / (W - pad.l - pad.r)) * (fp.count - 1);
      go(Math.min(fp.count - 1, Math.max(0, Math.round(i))), true);
    });
    return node;
  }

  function render() {
    const prev = day > 0 ? fp.cumulative[day - 1] : 0;
    const gained = fp.cumulative[day] - prev;
    const focused = box.contains(document.activeElement) && document.activeElement.classList.contains("range");
    clear(box).append(el("div", { class: "spread" },
      el("div", { class: "spread-head" },
        el("div", {}, el("small", {}, `${day + 1}. gün / ${fp.count}`), el("b", {}, fmt.day(fp.dayLabel(day)))),
        el("div", { class: "spread-area" }, el("b", {}, `${fmt.num(Math.round(fp.cumulative[day]))} km²`), el("small", {}, day === 0 ? "ilk gün" : `bugün +${fmt.num(Math.round(gained))} km²`))),
      chart(),
      el("div", { class: "controls" },
        el("button", { class: "btn btn-secondary btn-icon", type: "button", "aria-label": "Önceki gün", onclick: () => go(Math.max(0, day - 1), true) }, icon("skip-back", 18)),
        el("button", { class: "btn btn-primary btn-icon", type: "button", "aria-label": playing ? "Durdur" : "Oynat", onclick: toggle }, icon(playing ? "pause" : "play", 18)),
        el("button", { class: "btn btn-secondary btn-icon", type: "button", "aria-label": "Sonraki gün", onclick: () => go(Math.min(fp.count - 1, day + 1), true) }, icon("skip-forward", 18)),
        el("input", { class: "range", type: "range", min: "0", max: String(fp.count - 1), value: String(day), "aria-label": "Gün", "aria-valuetext": fmt.day(fp.dayLabel(day)), oninput: (e) => go(Number(e.target.value), true) })),
      el("div", { class: "chips", role: "group", "aria-label": "Oynatma hızı" }, [1, 2, 4].map((s) => el("button", { class: "chip", type: "button", "aria-pressed": String(speed === s), onclick: () => { speed = s; render(); } }, `${s}×`))),
      el("div", { class: "hero-facts" },
        fact(`${fmt.num(Math.round(total))} km²`, "toplam alan"),
        fact(fmt.shortDay(fp.dayLabel(peak)), `en hızlı gün, +${fmt.num(Math.round(fp.added[peak]))} km²`),
        fact(fp.strongest ? `${fmt.dec(fp.strongest.frp ?? 0)} MW` : "-", "en güçlü tespit")),
      el("div", { class: "ramp", "aria-hidden": "true" }),
      el("div", { class: "heat-ends" }, el("span", {}, fmt.shortDay(fp.dayLabel(0))), el("span", {}, fmt.shortDay(fp.dayLabel(fp.count - 1)))),
      el("p", { class: "legend-note" }, "Renk, alanın ilk görüldüğü gündür (koyu mor erken, sarı geç); o günün alanı beyaz parlar. Alan, uydunun yangın gördüğü 375 m'lik karelerin toplamıdır; yanan arazinin ölçümü değildir, gerçeğinden küçük ya da büyük olabilir.")));
    if (focused) box.querySelector(".range")?.focus();
  }

  function go(i, user = false) {
    if (user) { playing = false; token += 1; }
    day = i;
    paint();
    render();
  }

  async function toggle() {
    if (playing) { playing = false; token += 1; render(); return; }
    playing = true;
    const mine = ++token;
    if (day >= fp.count - 1) day = 0;
    paint();
    render();
    const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
    while (playing && mine === token && day < fp.count - 1) {
      await sleep((reduce ? 1200 : 900) / speed);
      if (!playing || mine !== token) return;
      day += 1;
      paint();
      render();
    }
    if (mine === token) { playing = false; render(); }
  }

  return {
    /** The thin outline of the whole fire, over the satellite pictures. */
    outline() { mode = "outline"; playing = false; token += 1; paint(); },
    /** The growth view; plays once from the first day unless motion is reduced. */
    play() {
      mode = "play";
      const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
      day = reduce ? fp.count - 1 : 0;
      paint();
      render();
      announce("Yayılma görünümü");
      if (!reduce && fp.count > 1) toggle();
    },
    bounds() {
      const all = fp.cells.map((c) => bounds(c, fp.kmLon));
      return all.length ? L.latLngBounds(all.flat()) : null;
    },
    destroy() { playing = false; token += 1; group.remove(); ignition?.remove(); },
  };
}

const fact = (value, label) => el("div", { class: "fact" }, el("b", {}, value), el("span", {}, label));
