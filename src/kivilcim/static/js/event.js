// One past fire: the satellite before and after on the map, and how it spread day by day.

import { api } from "./api.js";
import { el, clear, icon, fmt, announce } from "./ui.js";
import { fit } from "./map.js";
import { createSpread } from "./spread.js";
import { createImagePanel } from "./imagepanel.js";
import { eventName, eventMeta } from "./history.js";

export function mount(ctx, params) {
  const { body, signal } = ctx;
  const id = params.id;
  let detail = null;
  let panel = null;
  let mode = "satellite";
  let spread = null;

  const head = el("div", { class: "section-title" }, el("h2", {}, "Yangın kaydı"));
  const wait = el("div", { class: "card card-pad" }, el("div", { class: "skeleton", style: { height: "120px" } }));
  body.append(
    el("a", { class: "btn btn-ghost btn-small", href: "/history", "data-link": "", style: { alignSelf: "flex-start" } }, icon("arrow-left", 18), "Geçmiş yangınlar"),
    head, wait);
  ctx.setTitle("Yangın kaydı");
  ctx.onCleanup(() => { spread?.destroy(); panel?.destroy(); });

  const imageryPromise = api.imagery(id, signal);
  imageryPromise.catch(() => {}); // the panel reports a failure itself
  api.event(id, signal).then(async (data) => {
    detail = data;
    const e = data.event;
    ctx.setTitle(eventName(e));
    clear(head).append(el("h2", {}, eventName(e)));
    clear(wait);
    wait.remove();
    spread = createSpread(ctx, data, timelineBox);
    spread.outline();
    fit(spread.bounds() || L.latLngBounds([[e.bbox[0], e.bbox[1]], [e.bbox[2], e.bbox[3]]]).pad(0.25), { maxZoom: 13 });
    renderSummary();
    panel = createImagePanel(ctx, { box: imageryBox, noteBox, subject: "yangın", card: () => `/events/${encodeURIComponent(id)}/card.jpg`, report: () => `/report?event=${encodeURIComponent(id)}` });
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
      spread?.play();
    } else {
      spread?.outline();
      panel?.show(true);
    }
    announce(value === "spread" ? "Yayılma görünümü" : "Uydu görünümü");
  }
}

const stat = (value, label) => el("div", { class: "stat" }, el("b", {}, value), el("span", {}, label));
