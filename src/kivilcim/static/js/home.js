// The home view: what the satellites saw in the last 24 hours, and a way into the past.

import { api } from "./api.js";
import { el, clear, icon, fmt, levelPill, LEVELS, FACTORS, agoFrom, ago, announce } from "./ui.js";
import { levelColor, fit, TURKEY, getCanvas } from "./map.js";
import { eventCard } from "./history.js";
import * as saved from "./saved.js";
import { distanceKm } from "./tilemath.js";
import { windRelation, describe } from "./wind.js";

const SMALL = 2; // zones with this many detections or fewer are tucked away by default

const zoneName = (z) => `${z.district}, ${z.province}`;
const plural = (n, one, many) => `${fmt.num(n)} ${n === 1 ? one : many}`;

export function mount(ctx) {
  const { body, map, layer, signal } = ctx;
  ctx.setTitle("Aktif yangın bölgeleri");
  let zones = [];
  let showSmall = false;
  let markers = new Map();
  let weatherReady = false;

  const summary = el("div", { class: "card card-pad", "aria-live": "polite" });
  const list = el("div", { class: "rows", role: "list" });
  const listCard = el("div", { class: "card" }, list);
  const smallToggle = el("button", { class: "btn btn-ghost btn-small", type: "button", hidden: true, onclick: () => { showSmall = !showSmall; renderList(); } });
  const featured = el("div", { class: "panel-body" });

  const mine = el("div", { class: "panel-body", hidden: true });
  const toolTiles = tools();
  api.bot(signal).then((bot) => {
    if (bot.enabled && bot.username) toolTiles.append(el("a", { class: "tile tile-wide", href: `https://t.me/${bot.username}`, target: "_blank", rel: "noopener" }, icon("alert", 22), el("b", {}, "Yakınımda yangın çıkınca haber ver"), el("span", {}, "Telegram botuna yerinizi gönderin; uydu yeni bir yangın görünce size yazar")));
  }).catch(() => {});
  body.append(
    mine,
    toolTiles,
    el("div", { class: "section-title" }, el("h2", {}, "Şu an aktif bölgeler")),
    summary,
    explainer(),
    listCard,
    smallToggle,
    el("div", { class: "section-title" }, el("h2", {}, "Geçmiş yangınlar"), el("a", { href: "/history", "data-link": "" }, "Tümünü gör")),
    el("p", { class: "lede" }, "Büyük yangınların öncesi ve sonrası uydu görüntüsüyle. Birine dokunun, haritada yayılışını izleyin."),
    featured,
  );
  // Placeholders the size of the real content, so nothing moves when it arrives.
  summary.append(skeleton(138));
  list.append(skeleton(86), skeleton(86), skeleton(86), skeleton(86));
  featured.append(skeleton(280));

  const legend = el("div", { class: "map-card legend" },
    el("h3", {}, "Daire rengi: yangın tehlike endeksi"),
    el("div", { class: "scale" }, ["low", "moderate", "high", "very_high", "extreme"].map((l) => levelPill({ level: l }))),
    el("p", { class: "legend-note", id: "legend-note" }, "Daire boyutu tespit sayısını gösterir. Hava durumu yükleniyor…"));
  ctx.mapwrap.append(legend);
  ctx.onCleanup(() => legend.remove());

  load();
  loadFeatured();
  loadMine();

  async function load() {
    try {
      // First the zones alone, which answers at once, then again with the weather index.
      const quick = await api.zones(false, signal);
      zones = quick.zones;
      draw();
      renderSummary(quick);
      renderList();
      const full = await api.zones(true, signal);
      zones = full.zones;
      weatherReady = true;
      recolor();
      renderSummary(full);
      renderList();
      const note = legend.querySelector("#legend-note");
      if (note) note.textContent = "Daire boyutu tespit sayısını gösterir. Endeks deneyseldir ve geçmişte yangının yayılmasını öngörmedi.";
    } catch (error) {
      if (error.name === "AbortError") return;
      clear(summary).append(el("p", {}, "Bölgeler yüklenemedi. Bağlantınızı kontrol edip sayfayı yenileyin."));
      clear(list);
    }
  }

  function draw() {
    layer.clearLayers();
    markers = new Map();
    const bounds = [];
    for (const z of zones) {
      const radius = 7 + Math.min(14, Math.sqrt(z.detections) * 2);
      const marker = L.circleMarker([z.latitude, z.longitude], {
        renderer: getCanvas(), radius, weight: 2, color: "#fff", fillOpacity: 0.85, fillColor: levelColor(z.risk?.level),
      });
      marker.on("click", () => open(z));
      marker.bindTooltip(zoneName(z), { direction: "top", className: "marker-label", offset: [0, -radius] });
      marker.addTo(layer);
      markers.set(z.id, marker);
      bounds.push([z.latitude, z.longitude]);
    }
    if (bounds.length) fit(bounds.length === 1 ? L.latLng(bounds[0]).toBounds(40000) : bounds, { maxZoom: 9 });
    else fit(TURKEY, { animate: false });
  }

  function recolor() {
    for (const z of zones) markers.get(z.id)?.setStyle({ fillColor: levelColor(z.risk?.level) });
  }

  function open(z) {
    ctx.navigate(`/?lat=${z.latitude.toFixed(4)}&lon=${z.longitude.toFixed(4)}&r=${Math.ceil(z.radius_km) + 5}&z=${encodeURIComponent(z.id)}&name=${encodeURIComponent(zoneName(z))}`);
  }

  function renderSummary(data) {
    const detections = zones.reduce((n, z) => n + z.detections, 0);
    clear(summary);
    if (!zones.length) {
      summary.append(el("h3", { class: "card-title" }, "Son 24 saatte aktif bölge yok"),
        el("p", { class: "card-sub" }, `Uydular Türkiye'de yangın sıcaklığı gözlemlemedi. Son veri: ${ago(data.data_as_of.latest_observation_at ? (Date.now() - new Date(data.data_as_of.latest_observation_at)) / 60000 : 0)}.`));
      announce("Son 24 saatte aktif bölge yok.");
      return;
    }
    summary.append(
      el("p", { class: "card-title" }, `Son 24 saatte uydular ${plural(zones.length, "bölgede", "bölgede")} yangın sıcaklığı gördü.`),
      el("div", { class: "stats", style: { marginTop: "12px" } },
        stat(fmt.num(zones.length), "bölge"),
        stat(fmt.num(detections), "uydu tespiti"),
        stat(data.data_as_of.latest_observation_at ? agoFrom(data.data_as_of.latest_observation_at) : "-", "son gözlem")),
    );
    announce(`${zones.length} aktif bölge, ${detections} tespit.`);
  }

  function renderList() {
    clear(list);
    const sorted = [...zones].sort((a, b) => (b.risk?.score ?? -1) - (a.risk?.score ?? -1) || b.detections - a.detections);
    const small = sorted.filter((z) => z.detections <= SMALL);
    const visible = showSmall ? sorted : sorted.filter((z) => z.detections > SMALL);
    const shown = visible.length ? visible : sorted.slice(0, 5);
    for (const z of shown) list.append(zoneRow(z));
    smallToggle.hidden = !(small.length && visible.length);
    smallToggle.textContent = showSmall ? "Küçük bölgeleri gizle" : `Küçük bölgeleri göster (${small.length})`;
    if (!sorted.length) list.append(el("div", { class: "empty" }, el("h3", {}, "Gösterilecek bölge yok"), el("p", {}, "Yeni bir uydu geçişinde burası güncellenir.")));

  }

  function zoneRow(z) {
    const risk = z.risk;
    const drivers = risk?.drivers?.length ? ` · ${risk.drivers.map((d) => FACTORS[d] || d).join(", ")}` : "";
    const row = el("button", { class: "row", type: "button", role: "listitem", onclick: () => open(z),
      onpointerenter: () => markers.get(z.id)?.setStyle({ weight: 4 }), onpointerleave: () => markers.get(z.id)?.setStyle({ weight: 2 }) },
      el("span", { class: "row-title" }, zoneName(z)),
      el("span", { class: "row-side" }, levelPill(risk && weatherReady ? risk : null, { plain: false }), el("span", { class: "card-sub" }, plural(z.detections, "tespit", "tespit"))),
      el("span", { class: "row-meta" }, `${agoFrom(z.latest_observation_at)}${weatherReady ? drivers : ""}`),
    );
    if (risk && weatherReady) row.append(el("span", { class: "meter", dataset: { level: risk.level }, "aria-hidden": "true" }, el("i", { style: { width: `${Math.max(4, Math.round(risk.score))}%` } })));
    return row;
  }

  // The places saved in this browser, each with what the satellites saw within 10 km in the last day.
  async function loadMine() {
    const places = saved.list();
    mine.hidden = !places.length;
    if (!places.length) return;
    const rows = el("div", { class: "rows", role: "list" });
    const card = el("div", { class: "card" }, rows);
    clear(mine).append(el("div", { class: "section-title" }, el("h2", {}, "Yerlerim")), card);
    for (const place of places) {
      const meta = el("span", { class: "row-meta" }, "Kontrol ediliyor…");
      const side = el("span", { class: "row-side" });
      const open = () => ctx.navigate(`/?lat=${place.lat.toFixed(4)}&lon=${place.lon.toFixed(4)}&r=${place.r || 25}&name=${encodeURIComponent(place.name)}`);
      const row = el("div", { class: "row row-split", role: "listitem" },
        el("button", { class: "row-main", type: "button", onclick: open }, el("span", { class: "row-title" }, place.name), meta),
        side,
        el("button", { class: "btn btn-ghost btn-icon btn-small", type: "button", "aria-label": `${place.name} yerlerimden çıkar`, onclick: () => { saved.remove(place.lat, place.lon); loadMine(); } }, icon("x", 18)));
      rows.append(row);
      api.fires(place.lat, place.lon, 10, 24, signal).then((f) => {
        const near = f.fires[0];
        meta.textContent = near ? `10 km içinde ${plural(f.count, "tespit", "tespit")} · en yakını ${fmt.dec(distanceKm(place.lat, place.lon, near.latitude, near.longitude))} km, ${agoFrom(near.acquired_at)}` : `10 km içinde son 24 saatte tespit yok · son veri ${agoFrom(f.data_as_of.latest_observation_at)}`;
        const pill = el("span", { class: "pill", dataset: { level: near ? "very_high" : "low" } }, near ? "Yangın yakında" : "Tespit yok");
        clear(side).append(pill);
        if (near) {
          // Which way is the wind blowing, relative to the nearest fire?
          api.weather(place.lat, place.lon, signal).then((w) => {
            if (!w) return;
            const rel = windRelation({ lat: near.latitude, lon: near.longitude }, place, w.weather.wind_direction_deg, w.weather.wind_speed_kmh);
            const words = describe(rel.relation, distanceKm(place.lat, place.lon, near.latitude, near.longitude));
            meta.textContent += ` · ${words.short.toLowerCase()}`;
            if (rel.relation === "toward") pill.dataset.level = words.level;
          });
        }
      }).catch((error) => { if (error.name !== "AbortError") meta.textContent = "Kontrol edilemedi"; });
    }
  }

  async function loadFeatured() {
    try {
      const data = await api.events(signal);
      const top = [...data.events].filter((e) => e.in_turkey).sort((a, b) => b.total_frp_mw - a.total_frp_mw).slice(0, 3);
      clear(featured);
      if (!top.length) {
        featured.append(el("div", { class: "card empty" }, el("h3", {}, "Henüz kayıtlı yangın yok"), el("p", {}, "Geçmiş veri yüklendiğinde burada görünür.")));
        return;
      }
      top.forEach((event) => featured.append(eventCard(ctx, event)));
    } catch (error) {
      if (error.name === "AbortError") return;
      clear(featured).append(el("div", { class: "card empty" }, el("p", {}, "Geçmiş yangınlar yüklenemedi.")));
    }
  }
}

/** What this site is for, as three things to do. */
function tools() {
  const go = (href, ...children) => el("a", { class: "tile", href, "data-link": "" }, ...children);
  return el("div", { class: "tiles" },
    el("button", { class: "tile", type: "button", onclick: () => document.getElementById("place-search")?.focus() },
      icon("search", 22), el("b", {}, "Yakınımda yangın var mı?"), el("span", {}, "İl ya da ilçe arayın, son 24 saatin uydu tespitlerini görün")),
    go("/check", icon("layers", 22), el("b", {}, "Yerim yandı mı?"), el("span", {}, "Bir yerin yangından önceki ve sonraki uydu görüntüsü")));
}

function explainer() {
  return el("details", { class: "card disclosure" },
    el("summary", {}, icon("info", 18), "Bu liste neyi gösteriyor?", icon("chevron-down", 18)),
    el("div", { class: "body" },
      el("p", {}, el("b", {}, "Bölge: "), "Uydunun son 24 saatte birbirine yakın yerlerde gördüğü sıcak noktaların toplandığı alan. Bir bölge bir yangın olabilir, bir sanayi tesisi de."),
      el("p", {}, el("b", {}, "Tespit: "), "Uydunun tek bir geçişte gördüğü bir sıcak nokta. Çok tespit, genelde daha büyük ya da uzun süren ateş demektir."),
      el("p", {}, el("b", {}, "Renk ve kelime: "), `Hava durumundan hesaplanan yangın tehlike endeksi (${Object.values(LEVELS).join(", ").toLowerCase()}). Yangının şu an büyüklüğünü değil, koşulların ne kadar elverişli olduğunu söyler. Deneyseldir.`),
      el("p", {}, el("b", {}, "Küçük bölgeler: "), `${SMALL} ya da daha az tespitli bölgeler çoğunlukla tek bir geçişte görülen küçük ateşlerdir; liste kısa kalsın diye gizlenir.`),
    ));
}

const stat = (value, label) => el("div", { class: "stat" }, el("b", {}, value), el("span", {}, label));
const skeleton = (height) => el("div", { class: "skeleton", style: { height: `${height}px` }, "aria-hidden": "true" });
