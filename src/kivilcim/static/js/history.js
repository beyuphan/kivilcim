// Past fires: a card for each with its satellite before and after, and a map of where they were.

import { api } from "./api.js";
import { el, clear, fmt, icon } from "./ui.js";
import { fit, timeColor, pin, TURKEY } from "./map.js";
import { tileFor, tileUrl, distanceKm } from "./tilemath.js";

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
  while (running < 3 && queue.length) {
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
      figure.append(el("div", { class: "thumb-missing" }, limited ? "Görüntü için açın" : "Bulutsuz görüntü yok"));
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
  let sort = "recent";
  let where = null; // a province or district picked in the search box: only its fires are listed
  let shown = PAGE;
  let markers = new Map();

  const years = el("div", { class: "years", role: "group", "aria-label": "Yıla göre süz" });
  const list = el("div", { class: "panel-body" });
  const more = el("button", { class: "btn btn-secondary", type: "button", hidden: true, onclick: () => { shown += PAGE; renderList(); } }, "Daha fazla göster");
  const heading = el("p", { class: "lede" });
  const whereChip = el("div", { class: "chips", hidden: true });
  const sorter = el("div", { class: "segmented", role: "group", "aria-label": "Sıralama" },
    ["recent", "largest"].map((key) => el("button", { type: "button", "aria-pressed": String(key === sort), onclick: () => { sort = key; shown = PAGE; renderSort(); renderList(); } }, key === "recent" ? "En yeni" : "En büyük")));
  const renderSort = () => [...sorter.children].forEach((b, i) => b.setAttribute("aria-pressed", String((i === 0) === (sort === "recent"))));

  body.append(
    el("div", { class: "section-title" }, el("h2", {}, "Geçmiş yangınlar")),
    heading,
    whereChip,
    years,
    sorter,
    list,
    more,
  );
  list.append(el("div", { class: "skeleton", style: { height: "170px" } }), el("div", { class: "skeleton", style: { height: "170px" } }));

  const legend = el("div", { class: "map-card legend" },
    el("h3", {}, "Yangınların yılı"), el("div", { class: "ramp", "aria-hidden": "true" }),
    el("div", { class: "heat-ends" }, el("span", { id: "year-first" }, ""), el("span", { id: "year-last" }, "")),
    el("p", { class: "legend-note" }, "Pinin boyutu yangının gücünü gösterir. Bir pine dokunun."));
  ctx.mapwrap.append(legend);
  ctx.onCleanup(() => legend.remove());

  api.events(signal, { sort: "recent", limit: 500 }).then((data) => {
    events = data.events;
    renderYears();
    renderList();
    draw();
    const set = new Set(events.map((e) => e.start.slice(0, 4)));
    const sorted = [...set].sort();
    legend.querySelector("#year-first").textContent = sorted[0] || "";
    legend.querySelector("#year-last").textContent = sorted[sorted.length - 1] || "";
    body.append(el("details", { class: "card disclosure" }, el("summary", {}, icon("info", 18), "Bu yangınlar nasıl bulundu?", icon("chevron-down", 18)),
      el("div", { class: "body" },
        el("p", {}, "Olaylar, uydu tespitlerinin güç, yer ve zamana göre otomatik gruplanmasıdır; doğrulanmış olaylar değildir. Bazıları tarla yakması ya da başka ısı kaynakları olabilir."),
        el("p", {}, "Eski yıllarda daha az uydu olduğu için (Suomi NPP 2012'den, NOAA-20 2018'den, NOAA-21 2023'ten beri) eşikler o günlerin uydu sayısına göre ayarlanır."),
        el("p", {}, "Resmi bir kayıt değildir. Yer adları en yakın ilçe merkezine göredir ve yaklaşık."))));
  }).catch((error) => {
    if (error.name === "AbortError") return;
    clear(list).append(el("div", { class: "card empty" }, el("h3", {}, "Liste yüklenemedi"), el("p", {}, "Bağlantınızı kontrol edip sayfayı yenileyin.")));
  });

  // The search box filters the past fires by place instead of leaving for the place's own page.
  ctx.setPick((entry) => {
    where = entry;
    year = "all";
    shown = PAGE;
    renderWhere();
    renderYears();
    renderList();
    draw();
  });

  const inPlace = (e) => !where || (where.kind === "il" ? e.province === where.name : distanceKm(where.lat, where.lon, e.center_lat, e.center_lon) <= 40);

  function renderWhere() {
    whereChip.hidden = !where;
    clear(whereChip);
    if (!where) return;
    whereChip.append(el("button", { class: "chip", type: "button", "aria-pressed": "true", onclick: () => { where = null; renderWhere(); renderYears(); renderList(); draw(); } },
      icon("x", 14), where.kind === "il" ? where.name : `${where.name}, ${where.sub}${where.kind === "ilçe" ? " ve çevresi" : ""}`));
  }

  const visible = () => events
    .filter(inPlace)
    .filter((e) => year === "all" || e.start.startsWith(year))
    .sort(sort === "largest" ? (a, b) => b.total_frp_mw - a.total_frp_mw : (a, b) => (a.start < b.start ? 1 : -1));

  // A bar for each year, as tall as the fire it saw: pick a year to see only its fires.
  function renderYears() {
    clear(years);
    const by = new Map();
    for (const e of events.filter(inPlace)) {
      const y = e.start.slice(0, 4);
      const item = by.get(y) || { count: 0, power: 0 };
      item.count += 1;
      item.power += e.total_frp_mw;
      by.set(y, item);
    }
    const all = [...by.keys()].sort();
    if (!all.length) return;
    const first = Number(all[0]);
    const last = Number(all[all.length - 1]);
    const max = Math.max(...[...by.values()].map((v) => v.power), 1);
    years.append(el("button", { class: "chip year-all", type: "button", "aria-pressed": String(year === "all"), onclick: () => pickYear("all") }, "Tümü"));
    const bars = el("div", { class: "year-bars" });
    for (let y = first; y <= last; y++) {
      const item = by.get(String(y));
      bars.append(el("button", {
        class: "year-bar", type: "button", disabled: !item, "aria-pressed": String(year === String(y)),
        "aria-label": item ? `${y}: ${item.count} yangın` : `${y}: kayıt yok`, title: item ? `${y}: ${item.count} yangın` : `${y}: kayıt yok`,
        onclick: () => pickYear(String(y)),
      }, el("i", { style: { height: item ? `${Math.max(8, Math.round((item.power / max) * 100))}%` : "3px" } }), el("span", {}, String(y).slice(2))));
    }
    years.append(bars);
  }

  function pickYear(value) {
    year = value;
    shown = PAGE;
    renderYears();
    renderList();
    draw();
  }

  function renderList() {
    clear(list);
    const all = visible();
    heading.textContent = all.length
      ? `${fmt.num(all.length)} yangın kaydı${year === "all" ? "" : `, ${year}`}. Her birinde uydunun yangından önceki ve sonraki görüntüsü var.`
      : where ? "Bu bölgede uydunun gördüğü büyük bir yangın kaydı yok. Bir yerin öncesi-sonrası görüntüsüne bakmak için 'Yerim yandı mı?' bölümünü kullanın." : "Bu dönemde kayıtlı yangın yok.";
    all.slice(0, shown).forEach((event, i) => {
      const card = eventCard(ctx, event, { eager: i < 2 });
      card.addEventListener("pointerenter", () => markers.get(event.id)?.getElement()?.querySelector(".pin")?.setAttribute("data-hover", ""));
      card.addEventListener("pointerleave", () => markers.get(event.id)?.getElement()?.querySelector(".pin")?.removeAttribute("data-hover"));
      list.append(card);
    });
    more.hidden = all.length <= shown;
  }

  function draw() {
    layer.clearLayers();
    markers = new Map();
    const all = visible();
    if (!all.length) return where ? fit(L.latLng(where.lat, where.lon).toBounds(60000), { maxZoom: 9 }) : fit(TURKEY, { animate: false });
    const first = Math.min(...events.map((e) => Date.parse(e.start)));
    const last = Math.max(...events.map((e) => Date.parse(e.start)));
    const span = Math.max(1, last - first);
    for (const e of [...all].sort((a, b) => a.total_frp_mw - b.total_frp_mw)) {
      const size = 20 + Math.min(26, Math.sqrt(e.total_frp_mw) / 6);
      const marker = pin([e.center_lat, e.center_lon], {
        color: timeColor((Date.parse(e.start) - first) / span), label: size >= 28 ? e.start.slice(2, 4) : "", size, pulse: false,
        title: `${eventName(e)}, ${fmt.shortDay(e.start)} ${e.start.slice(0, 4)}`,
        content: el("div", { class: "pop" },
          el("b", {}, eventName(e)),
          el("span", { class: "pop-meta" }, eventMeta(e)),
          el("a", { class: "btn btn-primary btn-small", href: `/history/${encodeURIComponent(e.id)}`, "data-link": "" }, "Öncesi ve sonrası", icon("chevron-right", 16))),
      });
      marker.bindTooltip(`${eventName(e)} · ${e.start.slice(0, 4)}`, { direction: "top", className: "marker-label", offset: [0, -size / 2] });
      marker.addTo(layer);
      markers.set(e.id, marker);
    }
    fit(all.map((e) => [e.center_lat, e.center_lon]), { maxZoom: 8 });
  }
}
