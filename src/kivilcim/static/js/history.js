// Past fires: a card for each with its satellite before and after, and a map of where they were.

import { api } from "./api.js";
import { el, clear, fmt, icon } from "./ui.js";
import { fit, timeColor, getCanvas, TURKEY } from "./map.js";
import { tileFor, tileUrl } from "./tilemath.js";

const PAGE = 20;

export const eventName = (e) => `${e.district}, ${e.province}`;
export const eventMeta = (e) => `${fmt.range(e.start, e.end)} · ${e.days} gün · ${fmt.num(e.detections)} tespit`;

// ---- thumbnails: the imagery endpoint does real work the first time, so ask for it gently ----------

const queue = [];
let running = 0;
let limited = false;
const done = new Map();

function schedule(id, signal) {
  if (done.has(id)) return done.get(id);
  const promise = new Promise((resolve) => {
    queue.push({ id, signal, resolve });
    pump();
  });
  done.set(id, promise);
  return promise;
}

function pump() {
  while (running < 2 && queue.length) {
    const job = queue.shift();
    if (job.signal?.aborted) { done.delete(job.id); job.resolve(null); continue; }
    running += 1;
    api.imagery(job.id, job.signal)
      .then((data) => job.resolve(data))
      .catch((error) => {
        done.delete(job.id);
        if (error.status === 429) limited = true;
        job.resolve(null);
      })
      .finally(() => { running -= 1; pump(); });
  }
}

function thumb(side, text) {
  return el("figure", { class: "thumb", style: { margin: 0 } }, el("div", { class: "skeleton", style: { height: "100%" } }), el("figcaption", {}, text));
}

function fillThumbs(box, event, signal) {
  const [before, after] = box.children;
  const put = (figure, image, label) => {
    clear(figure);
    if (!image) {
      figure.append(el("div", { class: "thumb-missing" }, limited ? "Görüntü için açın" : "Uygun görüntü bulunamadı"));
      return;
    }
    const z = Math.min(event.extent_km > 30 ? 10 : 11, image.max_native_zoom);
    const { x, y } = tileFor(event.center_lat, event.center_lon, z);
    const img = el("img", { src: tileUrl(image.tile_url, z, x, y), alt: `${label} uydu görüntüsü, ${fmt.day(image.date)}`, width: 256, height: 256, decoding: "async" });
    img.addEventListener("error", () => { clear(figure).append(el("div", { class: "thumb-missing" }, "Görüntü yüklenemedi")); });
    figure.append(img, el("figcaption", {}, `${label} · ${fmt.shortDay(image.date)}`));
  };
  schedule(event.id, signal).then((data) => {
    if (signal?.aborted || !box.isConnected) return;
    put(before, data?.before, "Öncesi");
    put(after, data?.after, "Sonrası");
  });
}

const watcher = typeof IntersectionObserver === "undefined" ? null : new IntersectionObserver((entries) => {
  for (const entry of entries) {
    if (!entry.isIntersecting) continue;
    watcher.unobserve(entry.target);
    entry.target.__load?.();
  }
}, { rootMargin: "200px" });

export function eventCard(ctx, event, { eager = false } = {}) {
  const thumbs = el("div", { class: "thumbs" }, thumb("before", "Öncesi"), thumb("after", "Sonrası"));
  const card = el("a", { class: "event-card", href: `/history/${encodeURIComponent(event.id)}`, "data-link": "" },
    thumbs,
    el("span", { class: "info" }, el("strong", {}, eventName(event)), el("span", {}, eventMeta(event))));
  const load = () => fillThumbs(thumbs, event, ctx.signal);
  if (eager || !watcher) load();
  else { thumbs.__load = load; watcher.observe(thumbs); }
  return card;
}

// ---- the list view ---------------------------------------------------------------------------------

export function mount(ctx) {
  const { body, layer, signal } = ctx;
  ctx.setTitle("Geçmiş yangınlar");
  let events = [];
  let year = "all";
  let shown = PAGE;
  let markers = new Map();

  const chips = el("div", { class: "chips", role: "group", "aria-label": "Yıla göre süz" });
  const list = el("div", { class: "panel-body" });
  const more = el("button", { class: "btn btn-secondary", type: "button", hidden: true, onclick: () => { shown += PAGE; renderList(); } }, "Daha fazla göster");
  const heading = el("p", { class: "lede" });

  body.append(
    el("div", { class: "section-title" }, el("h2", {}, "Geçmiş yangınlar")),
    heading,
    chips,
    list,
    more,
  );
  list.append(el("div", { class: "skeleton", style: { height: "170px" } }), el("div", { class: "skeleton", style: { height: "170px" } }));

  api.events(signal).then((data) => {
    events = [...data.events].sort((a, b) => (a.start < b.start ? 1 : -1));
    renderChips();
    renderList();
    draw();
    body.append(el("details", { class: "card disclosure" }, el("summary", {}, icon("info", 18), "Bu yangınlar nasıl bulundu?", icon("chevron-down", 18)),
      el("div", { class: "body" },
        el("p", {}, "Olaylar, uydu tespitlerinin güç, yer ve zamana göre otomatik gruplanmasıdır; doğrulanmış olaylar değildir. Bazıları tarla yakması ya da başka ısı kaynakları olabilir."),
        el("p", {}, "Resmi bir kayıt değildir. Yer adları en yakın ilçe merkezine göredir ve yaklaşıktır."))));
  }).catch((error) => {
    if (error.name === "AbortError") return;
    clear(list).append(el("div", { class: "card empty" }, el("h3", {}, "Liste yüklenemedi"), el("p", {}, "Bağlantınızı kontrol edip sayfayı yenileyin.")));
  });

  const years = () => [...new Set(events.map((e) => e.start.slice(0, 4)))].sort().reverse();
  const visible = () => events.filter((e) => year === "all" || e.start.startsWith(year));

  function renderChips() {
    clear(chips);
    const options = ["all", ...years()];
    for (const value of options) {
      chips.append(el("button", { class: "chip", type: "button", "aria-pressed": String(value === year), onclick: () => { year = value; shown = PAGE; renderChips(); renderList(); draw(); } }, value === "all" ? "Tümü" : value));
    }
  }

  function renderList() {
    clear(list);
    const all = visible();
    heading.textContent = all.length
      ? `${fmt.num(all.length)} yangın kaydı. Her birinde uydunun yangından önceki ve sonraki görüntüsü var.`
      : "Bu dönemde kayıtlı yangın yok.";
    all.slice(0, shown).forEach((event, i) => list.append(eventCard(ctx, event, { eager: i < 2 })));
    more.hidden = all.length <= shown;
  }

  function draw() {
    layer.clearLayers();
    markers = new Map();
    const all = visible();
    if (!all.length) return fit(TURKEY, { animate: false });
    const first = Math.min(...all.map((e) => Date.parse(e.start)));
    const last = Math.max(...all.map((e) => Date.parse(e.start)));
    const span = Math.max(1, last - first);
    for (const e of all) {
      const marker = L.circleMarker([e.center_lat, e.center_lon], {
        renderer: getCanvas(), radius: 7 + Math.min(13, Math.sqrt(e.extent_km)), weight: 2, color: "#fff", fillOpacity: 0.85,
        fillColor: timeColor((Date.parse(e.start) - first) / span),
      });
      marker.on("click", () => ctx.navigate(`/history/${encodeURIComponent(e.id)}`));
      marker.bindTooltip(`${eventName(e)} · ${fmt.shortDay(e.start)}`, { direction: "top", className: "marker-label" });
      marker.addTo(layer);
      markers.set(e.id, marker);
    }
    fit(all.map((e) => [e.center_lat, e.center_lon]), { maxZoom: 8 });
  }
}
