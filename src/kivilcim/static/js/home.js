// The home view: what the satellites saw in the last 24 hours, and a way into the past.

import { api } from "./api.js";
import { el, clear, icon, fmt, levelPill, FACTORS, agoFrom, announce } from "./ui.js";
import { fit, TURKEY, pin, heatColor } from "./map.js";
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
  let selected = null;

  const hero = el("div", { class: "hero", "aria-live": "polite" });
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
    hero,
    toolTiles,
    mine,
    el("div", { class: "section-title" }, el("h2", {}, "Aktif bölgeler")),
    explainer(),
    listCard,
    smallToggle,
    el("div", { class: "section-title" }, el("h2", {}, "Geçmişin en büyük yangınları"), el("a", { href: "/history", "data-link": "" }, "Tümünü gör")),
    el("p", { class: "lede" }, "Yıllara yayılan arşivden en büyükler: her birinin yangından önceki ve sonraki uydu görüntüsü yan yana."),
    featured,
  );
  // Placeholders the size of the real content, so nothing moves when it arrives.
  hero.append(skeleton(168));
  list.append(skeleton(86), skeleton(86), skeleton(86), skeleton(86));
  featured.append(skeleton(280));

  const legend = el("div", { class: "map-card legend" },
    el("h3", {}, "Pin = uydunun gördüğü ısı"),
    el("div", { class: "heat-ramp", "aria-hidden": "true" }),
    el("div", { class: "heat-ends" }, el("span", {}, "hafif"), el("span", {}, "çok güçlü")),
    el("p", { class: "legend-note" }, "Sayı, o bölgedeki uydu tespiti sayısıdır. Bir pine dokunun."));
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
      renderHero(quick);
      renderList();
      const full = await api.zones(true, signal);
      zones = full.zones;
      weatherReady = true;
      renderHero(full);
      renderList();
    } catch (error) {
      if (error.name === "AbortError") return;
      clear(hero).append(el("p", {}, "Bölgeler yüklenemedi. Bağlantınızı kontrol edip sayfayı yenileyin."));
      clear(list);
    }
  }

  function strength(z) { return z.total_frp_mw; }

  function draw() {
    layer.clearLayers();
    markers = new Map();
    const bounds = [];
    for (const z of [...zones].sort((a, b) => strength(a) - strength(b))) {
      const size = 30 + Math.min(26, Math.sqrt(z.detections) * 3.2);
      const marker = pin([z.latitude, z.longitude], {
        color: heatColor(strength(z)), label: String(z.detections), size,
        title: `${zoneName(z)}: ${plural(z.detections, "tespit", "tespit")}`,
        content: popupFor(z),
        onSelect: () => select(z, false),
      });
      marker.bindTooltip(zoneName(z), { direction: "top", className: "marker-label", offset: [0, -size / 2] });
      marker.addTo(layer);
      markers.set(z.id, marker);
      bounds.push([z.latitude, z.longitude]);
    }
    if (bounds.length) fit(bounds.length === 1 ? L.latLng(bounds[0]).toBounds(40000) : bounds, { maxZoom: 9 });
    else fit(TURKEY, { animate: false });
  }

  // What shows in the card on a pin: the name, what was seen, and a way in.
  function popupFor(z) {
    return el("div", { class: "pop" },
      el("b", {}, zoneName(z)),
      el("span", { class: "pop-meta" }, `${plural(z.detections, "tespit", "tespit")} · ${fmt.dec(z.total_frp_mw)} MW · ${agoFrom(z.latest_observation_at)}`),
      el("button", { class: "btn btn-primary btn-small", type: "button", onclick: () => open(z) }, "Ayrıntıları aç", icon("chevron-right", 16)));
  }

  /** Mark a zone as the one in focus, on the map and in the list. */
  function select(z, pan = true) {
    selected = z.id;
    markers.forEach((m, id) => m.getElement()?.querySelector(".pin")?.toggleAttribute("data-active", id === z.id));
    list.querySelectorAll(".row").forEach((row) => row.toggleAttribute("aria-current", row.dataset.id === z.id));
    const marker = markers.get(z.id);
    if (pan && marker) { map.panTo(marker.getLatLng(), { animate: true }); marker.openPopup(); }
  }

  function open(z) {
    ctx.navigate(`/?lat=${z.latitude.toFixed(4)}&lon=${z.longitude.toFixed(4)}&r=${Math.ceil(z.radius_km) + 5}&z=${encodeURIComponent(z.id)}&name=${encodeURIComponent(zoneName(z))}`);
  }

  function renderHero(data) {
    const detections = zones.reduce((n, z) => n + z.detections, 0);
    const latest = data.data_as_of.latest_observation_at;
    clear(hero);
    hero.append(el("div", { class: "live" }, el("i", { "aria-hidden": "true" }), latest ? `Canlı · son gözlem ${agoFrom(latest)}` : "Canlı"));
    if (!zones.length) {
      hero.append(el("h2", { class: "hero-title" }, "Son 24 saatte aktif bölge yok"),
        el("p", { class: "hero-text" }, "Uydular Türkiye'de yangın sıcaklığı gözlemlemedi. Bu, yangın olmadığı anlamına gelmez: uydular günde birkaç kez geçer."));
      announce("Son 24 saatte aktif bölge yok.");
      return;
    }
    hero.append(
      el("div", { class: "hero-num" }, el("b", { class: "count", dataset: { to: String(zones.length) } }, fmt.num(zones.length)), el("span", {}, "aktif bölge")),
      el("p", { class: "hero-text" }, `Son 24 saatte uydular Türkiye'de ${fmt.num(zones.length)} bölgede, toplam ${fmt.num(detections)} yangın sıcaklığı tespiti gördü.`),
      el("div", { class: "hero-facts" },
        fact(fmt.num(detections), "tespit"),
        fact(fmt.num(zones.filter((z) => z.detections > SMALL).length), "belirgin bölge"),
        fact(`${fmt.num(Math.round(zones.reduce((n, z) => n + z.total_frp_mw, 0)))} MW`, "toplam ısı gücü")),
    );
    countUp(hero.querySelector(".count"));
    announce(`${zones.length} aktif bölge, ${detections} tespit.`);
  }

  function renderList() {
    clear(list);
    const sorted = [...zones].sort((a, b) => strength(b) - strength(a));
    const small = sorted.filter((z) => z.detections <= SMALL);
    const visible = showSmall ? sorted : sorted.filter((z) => z.detections > SMALL);
    const shown = visible.length ? visible : sorted.slice(0, 5);
    for (const z of shown) list.append(zoneRow(z));
    smallToggle.hidden = !(small.length && visible.length);
    smallToggle.textContent = showSmall ? "Küçük bölgeleri gizle" : `Küçük bölgeleri göster (${small.length})`;
    if (!sorted.length) list.append(el("div", { class: "empty" }, el("h3", {}, "Gösterilecek bölge yok"), el("p", {}, "Yeni bir uydu geçişinde burası güncellenir.")));
    if (selected) select(zones.find((z) => z.id === selected) || { id: null }, false);
  }

  function zoneRow(z) {
    const risk = z.risk;
    const drivers = risk?.drivers?.length ? ` · ${risk.drivers.map((d) => FACTORS[d] || d).join(", ")}` : "";
    const heat = el("i", { class: "row-heat", style: { background: heatColor(strength(z)) }, "aria-hidden": "true" });
    const hover = (on) => markers.get(z.id)?.getElement()?.querySelector(".pin")?.toggleAttribute("data-hover", on);
    const row = el("div", { class: "row row-split", role: "listitem", dataset: { id: z.id }, onpointerenter: () => hover(true), onpointerleave: () => hover(false) },
      el("button", { class: "row-main", type: "button", onclick: () => select(z) },
        el("span", { class: "row-title" }, heat, zoneName(z)),
        el("span", { class: "row-meta" }, `${plural(z.detections, "tespit", "tespit")} · ${agoFrom(z.latest_observation_at)} · ${fmt.num(Math.round(z.total_frp_mw))} MW${weatherReady ? drivers : ""}`)),
      el("span", { class: "row-side" }, levelPill(risk && weatherReady ? risk : null, { plain: false })),
      el("button", { class: "btn btn-ghost btn-icon btn-small", type: "button", "aria-label": `${zoneName(z)} ayrıntılarını aç`, onclick: () => open(z) }, icon("chevron-right", 18)));
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
      const openPlace = () => ctx.navigate(`/?lat=${place.lat.toFixed(4)}&lon=${place.lon.toFixed(4)}&r=${place.r || 25}&name=${encodeURIComponent(place.name)}`);
      const row = el("div", { class: "row row-split", role: "listitem" },
        el("button", { class: "row-main", type: "button", onclick: openPlace }, el("span", { class: "row-title" }, place.name), meta),
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
      const data = await api.events(signal, { sort: "largest", limit: 3 });
      clear(featured);
      if (!data.events.length) {
        featured.append(el("div", { class: "card empty" }, el("h3", {}, "Henüz kayıtlı yangın yok"), el("p", {}, "Geçmiş veri yüklendiğinde burada görünür.")));
        return;
      }
      data.events.forEach((event) => featured.append(eventCard(ctx, event)));
    } catch (error) {
      if (error.name === "AbortError") return;
      clear(featured).append(el("div", { class: "card empty" }, el("p", {}, "Geçmiş yangınlar yüklenemedi.")));
    }
  }
}

/** What this site is for, as things to do. */
function tools() {
  const go = (href, ...children) => el("a", { class: "tile", href, "data-link": "" }, ...children);
  return el("div", { class: "tiles" },
    el("button", { class: "tile", type: "button", onclick: () => document.getElementById("place-search")?.focus() },
      icon("search", 22), el("b", {}, "Yakınımda yangın var mı?"), el("span", {}, "İl ya da ilçe arayın, son 24 saatin uydu tespitlerini görün")),
    go("/check", icon("layers", 22), el("b", {}, "Yerim yandı mı?"), el("span", {}, "Bir yerin yangından önceki ve sonraki uydu görüntüsü")));
}

function explainer() {
  return el("details", { class: "card disclosure" },
    el("summary", {}, icon("info", 18), "Pinler neyi gösteriyor?", icon("chevron-down", 18)),
    el("div", { class: "body" },
      el("p", {}, el("b", {}, "Bölge: "), "Uydunun son 24 saatte birbirine yakın yerlerde gördüğü sıcak noktaların toplandığı alan. Bir bölge bir yangın olabilir, bir sanayi tesisi de."),
      el("p", {}, el("b", {}, "Pinin sayısı: "), "Uydunun o bölgede gördüğü sıcak nokta (tespit) sayısı. Çok tespit, genelde daha büyük ya da uzun süren ateş demektir."),
      el("p", {}, el("b", {}, "Pinin rengi: "), "Uydunun ölçtüğü ısı gücü: sarı hafif, turuncu orta, kırmızı ve koyu kırmızı çok güçlü."),
      el("p", {}, el("b", {}, "Listedeki etiket: "), "Hava durumundan hesaplanan yangın tehlike endeksi (düşükten aşırıya). Yangının şu anki büyüklüğünü değil, koşulların ne kadar elverişli olduğunu söyler. Deneyseldir."),
      el("p", {}, el("b", {}, "Küçük bölgeler: "), `${SMALL} ya da daha az tespitli bölgeler çoğunlukla tek bir geçişte görülen küçük ateşlerdir; liste kısa kalsın diye gizlenir.`),
    ));
}

const fact = (value, label) => el("div", { class: "fact" }, el("b", {}, value), el("span", {}, label));
const skeleton = (height) => el("div", { class: "skeleton", style: { height: `${height}px` }, "aria-hidden": "true" });

/** The big number counts up once, unless the person asked for less motion. */
function countUp(node) {
  const to = Number(node?.dataset.to);
  if (!node || !Number.isFinite(to) || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const start = performance.now();
  const step = (now) => {
    const t = Math.min(1, (now - start) / 700);
    node.textContent = fmt.num(Math.round(to * (1 - (1 - t) ** 3)));
    if (t < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}
