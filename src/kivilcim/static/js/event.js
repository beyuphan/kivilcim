// One past fire: the satellite before and after on the map, and how it spread day by day.

import { api } from "./api.js";
import { el, clear, icon, fmt, announce, sleep } from "./ui.js";
import { fit, timeColor, getCanvas } from "./map.js";
import { createImagePanel } from "./imagepanel.js";
import { eventName, eventMeta } from "./history.js";

export function mount(ctx, params) {
  const { body, layer, signal, map, mapwrap } = ctx;
  const id = params.id;
  let detail = null;
  let panel = null;
  let mode = "satellite";
  let day = 0;
  let playing = false;
  let dots = [];

  const head = el("div", { class: "section-title" }, el("h2", {}, "Yangın kaydı"));
  const wait = el("div", { class: "card card-pad" }, el("div", { class: "skeleton", style: { height: "120px" } }));
  body.append(
    el("a", { class: "btn btn-ghost btn-small", href: "/history", "data-link": "", style: { alignSelf: "flex-start" } }, icon("arrow-left", 18), "Geçmiş yangınlar"),
    head, wait);
  ctx.setTitle("Yangın kaydı");
  ctx.onCleanup(() => { playing = false; panel?.destroy(); });

  const imageryPromise = api.imagery(id, signal);
  imageryPromise.catch(() => {}); // the panel reports a failure itself
  api.event(id, signal).then(async (data) => {
    detail = data;
    const e = data.event;
    ctx.setTitle(eventName(e));
    clear(head).append(el("h2", {}, eventName(e)));
    clear(wait);
    wait.remove();
    drawDots();
    showAll();
    fit(L.latLngBounds([[e.bbox[0], e.bbox[1]], [e.bbox[2], e.bbox[3]]]).pad(0.25), { maxZoom: 13 });
    renderSummary();
    panel = createImagePanel(ctx, { box: imageryBox, noteBox, subject: "yangın" });
    panel.start(imageryPromise, () => api.imagery(id, signal));
  }).catch((error) => {
    if (error.name === "AbortError") return;
    clear(wait).append(el("h3", { class: "card-title" }, error.status === 404 ? "Bu kayıt bulunamadı" : "Kayıt yüklenemedi"),
      el("p", { class: "card-sub" }, "Geçmiş yangınlar listesine dönüp tekrar deneyin."));
  });

  // ---- panel pieces ----------------------------------------------------------------------------------

  const summaryBox = el("div", { class: "card card-pad" });
  const modeBox = el("div", { class: "segmented", role: "group", "aria-label": "Görünüm" });
  const imageryBox = el("div", { class: "card card-pad" });
  const timelineBox = el("div", { class: "card card-pad", hidden: true });
  const noteBox = el("details", { class: "card disclosure" });
  body.append(summaryBox, modeBox, imageryBox, timelineBox, noteBox);
  summaryBox.hidden = modeBox.hidden = imageryBox.hidden = noteBox.hidden = true;

  function renderSummary() {
    const e = detail.event;
    summaryBox.hidden = false;
    clear(summaryBox).append(
      el("p", { class: "card-sub" }, eventMeta(e)),
      el("div", { class: "stats", style: { marginTop: "12px" } },
        stat(`${e.days}`, "gün sürdü"),
        stat(fmt.num(e.detections), "uydu tespiti"),
        stat(`${fmt.dec(e.extent_km)} km`, "yayılma çapı")),
      el("p", { class: "card-sub", style: { marginTop: "12px" } }, `Yaklaşık yeri: ${eventName(e)} çevresi (en yakın ilçe merkezine göre).`));
    modeBox.hidden = false;
    clear(modeBox).append(
      modeButton("satellite", "Uydu: öncesi ve sonrası"),
      modeButton("spread", "Yayılma"));
  }

  const modeButton = (value, text) => el("button", { type: "button", "aria-pressed": String(mode === value), onclick: () => setMode(value) }, text);

  function setMode(value) {
    mode = value;
    [...modeBox.children].forEach((b, i) => b.setAttribute("aria-pressed", String((i === 0) === (value === "satellite"))));
    imageryBox.hidden = value !== "satellite";
    timelineBox.hidden = value !== "spread";
    if (value === "spread") {
      panel?.show(false);
      showUpTo(detail.days.length - 1);
    } else {
      playing = false;
      showAll();
      panel?.show(true);
    }
    announce(value === "spread" ? "Yayılma görünümü" : "Uydu görünümü");
  }

  // ---- the spread view -------------------------------------------------------------------------------

  function drawDots() {
    layer.clearLayers();
    dots = [];
    const first = detail.window_start;
    const total = Math.max(1, detail.days.length - 1);
    for (const d of detail.detections) {
      const index = Math.round((Date.parse(d.acquired_at.slice(0, 10)) - Date.parse(first)) / 86400000);
      const marker = L.circleMarker([d.latitude, d.longitude], {
        renderer: getCanvas(), radius: 5, weight: 1, color: "#fff", fillOpacity: 0.9, fillColor: timeColor(index / total),
      });
      marker.__day = index;
      marker.addTo(layer);
      dots.push(marker);
    }
  }

  // On top of satellite images the dots are small and see-through, so the ground stays readable.
  function showAll() {
    dots.forEach((m) => { m.setRadius(3.5); m.setStyle({ opacity: 0.8, fillOpacity: 0.55, weight: 1 }); });
  }

  function showUpTo(index) {
    day = index;
    dots.forEach((m) => {
      const on = m.__day <= index;
      m.setRadius(5);
      m.setStyle({ opacity: on ? 1 : 0, fillOpacity: on ? (m.__day === index ? 1 : 0.7) : 0 });
    });
    renderTimeline(true);
  }

  function renderTimeline(keepFocus = false) {
    const days = detail.days;
    const first = detail.window_start;
    const max = Math.max(...days.map((d) => d.detections), 1);
    const at = (i) => new Date(Date.parse(first) + i * 86400000).toISOString().slice(0, 10);
    const count = days.length;
    const current = days[Math.min(day, count - 1)];
    const focused = keepFocus && document.activeElement?.closest?.(".timeline");
    const slider = el("input", { class: "range", type: "range", min: "0", max: String(count - 1), value: String(day), "aria-label": "Gün", "aria-valuetext": fmt.day(at(day)), oninput: (e) => { playing = false; showUpTo(Number(e.target.value)); } });
    const play = el("button", { class: "btn btn-secondary btn-icon", type: "button", "aria-label": playing ? "Durdur" : "Oynat", onclick: togglePlay }, icon(playing ? "pause" : "play", 18));
    clear(timelineBox).append(el("div", { class: "timeline" },
      el("div", { class: "timeline-head" }, el("b", {}, fmt.day(at(day))), el("span", { class: "card-sub" }, `${fmt.num(current?.detections ?? 0)} tespit o gün`)),
      el("div", { class: "bars", "aria-hidden": "true" }, days.map((d, i) => el("button", { class: "bar", type: "button", tabindex: "-1", dataset: { after: String(i > day) }, "aria-current": String(i === day), style: { height: `${Math.max(6, (d.detections / max) * 100)}%`, background: timeColor(i / Math.max(1, count - 1)) }, onclick: () => { playing = false; showUpTo(i); } }))),
      el("div", { class: "controls" }, el("button", { class: "btn btn-secondary btn-icon", type: "button", "aria-label": "Önceki gün", onclick: () => { playing = false; showUpTo(Math.max(0, day - 1)); } }, icon("skip-back", 18)), play, el("button", { class: "btn btn-secondary btn-icon", type: "button", "aria-label": "Sonraki gün", onclick: () => { playing = false; showUpTo(Math.min(count - 1, day + 1)); } }, icon("skip-forward", 18)), slider),
      el("div", { class: "axis" }, el("span", {}, fmt.shortDay(at(0))), el("span", {}, fmt.shortDay(at(count - 1)))),
      el("div", { class: "ramp", "aria-hidden": "true" }),
      el("p", { class: "legend-note" }, "Noktaların rengi tespitin günüdür: koyu mordan sarıya, erken günden geç güne.")));
    if (focused) timelineBox.querySelector(".range")?.focus();
  }

  async function togglePlay() {
    if (playing) { playing = false; renderTimeline(); return; }
    playing = true;
    if (day >= detail.days.length - 1) day = 0;
    renderTimeline();
    while (playing && day < detail.days.length - 1) {
      await sleep(matchMedia("(prefers-reduced-motion: reduce)").matches ? 900 : 650);
      if (!playing) break;
      showUpTo(day + 1);
    }
    playing = false;
    renderTimeline();
  }
}

const stat = (value, label) => el("div", { class: "stat" }, el("b", {}, value), el("span", {}, label));
