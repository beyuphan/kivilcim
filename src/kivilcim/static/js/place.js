// One place: what the satellites saw within a radius of it, the weather there, and what it means.

import { api } from "./api.js";
import { el, clear, icon, fmt, agoFrom, levelPill, FACTORS, CONFIDENCE, satellite, COMPASS, announce } from "./ui.js";
import { flyTo, getCanvas, levelColor } from "./map.js";
import { loadIndex, nearestName } from "./search.js";
import * as saved from "./saved.js";

const HOURS = 24;

export function mount(ctx, params) {
  const { body, layer, signal } = ctx;
  const lat = Number(params.get("lat"));
  const lon = Number(params.get("lon"));
  const radius = Math.min(Math.max(Number(params.get("r")) || 25, 5), 150);
  const zoneId = params.get("z");
  let name = params.get("name") || "";

  if (!Number.isFinite(lat) || !Number.isFinite(lon) || Math.abs(lat) > 90 || Math.abs(lon) > 180) {
    ctx.setTitle("Konum bulunamadı");
    body.append(back(), el("div", { class: "card empty" }, el("h3", {}, "Bu konum geçerli değil"), el("p", {}, "Arama kutusundan bir il ya da ilçe seçin.")));
    return;
  }

  const title = el("h2", { class: "card-title", style: { fontSize: "var(--fs-xl)" } }, name || "Seçilen konum");
  const verdict = el("div", { class: "card card-pad" }, el("div", { class: "skeleton", style: { height: "60px" } }));
  const weatherBox = el("div", { class: "card card-pad" }, el("div", { class: "skeleton", style: { height: "90px" } }));
  const detail = el("div", { class: "card" });
  const actions = el("div", { class: "actions" });
  body.append(back(), el("div", { class: "section-title" }, title), verdict, actions, weatherBox, detail);
  renderActions();
  ctx.setTitle(name || "Seçilen konum");

  layer.clearLayers();
  L.circleMarker([lat, lon], { renderer: getCanvas(), radius: 8, weight: 4, color: "#fff", fillColor: "#2563EB", fillOpacity: 1, interactive: false }).addTo(layer);
  L.circle([lat, lon], { radius: radius * 1000, color: "#2563EB", weight: 2, fillOpacity: 0.06, interactive: false }).addTo(layer);
  const zoom = radius <= 15 ? 11 : radius <= 40 ? 10 : 8;
  flyTo(lat, lon, zoom);

  if (!name) loadIndex().then((entries) => {
    name = nearestName(entries, lat, lon) || "";
    if (name) { title.textContent = name; ctx.setTitle(name); renderActions(); }
  }).catch(() => {});

  Promise.all([api.fires(lat, lon, radius, HOURS, signal), api.weather(lat, lon, signal)]).then(([fires, weather]) => {
    renderVerdict(fires);
    renderWeather(weather, fires);
    renderDetail(fires);
    drawFires(fires);
  }).catch((error) => {
    if (error.name === "AbortError") return;
    clear(verdict).append(el("p", {}, "Veriler yüklenemedi. Bağlantınızı kontrol edip yeniden deneyin."));
    clear(weatherBox).remove();
  });

  function renderActions() {
    clear(actions);
    const keep = saved.has(lat, lon);
    actions.append(
      el("a", { class: "btn btn-secondary", href: `/check?lat=${lat.toFixed(4)}&lon=${lon.toFixed(4)}&name=${encodeURIComponent(name)}&auto=1`, "data-link": "" }, icon("layers", 18), "Yerim yandı mı?"),
      el("button", { class: "btn btn-secondary", type: "button", "aria-pressed": String(keep), onclick: () => {
        if (keep) saved.remove(lat, lon);
        else if (!saved.add({ name: name || "Seçilen yer", lat, lon, r: Math.min(radius, 25) })) return announce(`En fazla ${saved.limit} yer kaydedebilirsiniz.`);
        announce(keep ? "Yerlerimden çıkarıldı" : "Yerlerime eklendi");
        renderActions();
      } }, icon(keep ? "check" : "eye", 18), keep ? "Yerlerimde" : "Yerlerime ekle"));
  }

  function back() {
    return el("a", { class: "btn btn-ghost btn-small", href: "/", "data-link": "", style: { alignSelf: "flex-start" } }, icon("arrow-left", 18), "Tüm bölgeler");
  }

  function renderVerdict(fires) {
    clear(verdict);
    const n = fires.count;
    const nearest = fires.fires[0];
    if (!n) {
      verdict.append(
        el("p", { class: "card-title" }, `Son ${HOURS} saatte ${radius} km içinde uydu tespiti yok.`),
        el("p", { class: "card-sub", style: { marginTop: "6px" } }, `Bu, yangın olmadığı anlamına gelmez: uydular günde birkaç kez geçer ve küçük ya da kısa ateşleri kaçırabilir. Son veri ${agoFrom(fires.data_as_of.latest_observation_at)}.`));
      announce("Yakında uydu tespiti yok.");
    } else {
      verdict.append(
        el("p", { class: "card-title" }, `Son ${HOURS} saatte ${radius} km içinde ${fmt.num(n)} uydu tespiti var.`),
        el("p", { class: "card-sub", style: { marginTop: "6px" } }, `En yakını ${fmt.dec(nearest.distance_km)} km uzakta, ${agoFrom(nearest.acquired_at)} görüldü. Bu bir yangın doğrulaması değil, uydunun gördüğü sıcaklıktır.`));
      announce(`${n} uydu tespiti var.`);
    }
    verdict.append(el("p", { class: "card-sub", style: { marginTop: "8px" } }, "Acil durumda 112'yi arayın. Kıvılcım resmi bir uyarı sistemi değildir; uydu verisi geç gelebilir."));
  }

  async function renderWeather(weather, fires) {
    clear(weatherBox);
    const zone = zoneId ? (await api.zones(true, signal).catch(() => null))?.zones.find((z) => z.id === zoneId) : null;
    if (!weather) {
      weatherBox.append(el("p", { class: "card-sub" }, "Bu konum için hava durumu alınamadı."));
      return;
    }
    const w = weather.weather;
    const dir = w.wind_direction_deg == null ? "" : ` ${COMPASS[Math.round(w.wind_direction_deg / 22.5) % 16]}`;
    weatherBox.append(
      el("div", { class: "section-title" }, el("h3", { class: "card-title" }, "Hava durumu"), zone?.risk ? levelPill(zone.risk) : null),
      el("div", { class: "metrics", style: { marginTop: "12px" } },
        metric("thermometer", `${fmt.dec(w.temperature_c)}°C`, "sıcaklık"),
        metric("droplet", `%${fmt.num(w.humidity_pct)}`, "nem"),
        metric("wind", `${fmt.num(w.wind_speed_kmh)} km/sa${dir}`, "rüzgar"),
        metric("rain", `${fmt.dec(w.precip_72h_mm)} mm`, "son 72 saat yağış")),
    );
    if (zone?.risk) {
      const drivers = zone.risk.drivers.map((d) => FACTORS[d] || d);
      weatherBox.append(el("p", { class: "card-sub", style: { marginTop: "12px" } },
        drivers.length ? `Tehlike endeksini artıran etkenler: ${drivers.join(", ")}.` : "Hava koşulları şu an tehlikeyi belirgin artırmıyor.",
        " Endeks deneyseldir ve geçmişte yangının yayılmasını öngörmedi."));
    }
    weatherBox.append(el("p", { class: "legend-note", style: { marginTop: "8px" } }, "Hava verisi: Open-Meteo.com"));
    void fires;
  }

  function renderDetail(fires) {
    clear(detail);
    const rows = [...fires.fires.map((f) => ({ f, kind: f.confidence_level })),
      ...fires.recurring_sources.map((f) => ({ f, kind: "fixed" })),
      ...fires.low_confidence.map((f) => ({ f, kind: "low" }))];
    if (!rows.length) { detail.remove(); return; }
    detail.append(el("div", { class: "section-title", style: { padding: "12px 12px 4px" } }, el("h3", { class: "card-title" }, "Tespitler")));
    for (const { f, kind } of rows.slice(0, 40)) {
      const label = kind === "fixed" ? "Sürekli sıcak kaynak (tesis olabilir)" : kind === "low" ? "Düşük güven, yangın olmayabilir" : CONFIDENCE[kind];
      detail.append(el("div", { class: "detail-row" },
        el("span", { class: "dot", dataset: { c: kind } }),
        el("span", {}, el("b", {}, `${fmt.dec(f.distance_km)} km`), el("br"), el("small", { class: "card-sub" }, `${label} · ${satellite(f)}`)),
        el("small", { class: "card-sub" }, agoFrom(f.acquired_at))));
    }
  }

  function drawFires(fires) {
    const add = (f, color, hollow) => L.circleMarker([f.latitude, f.longitude], {
      renderer: getCanvas(), radius: 7, weight: 2, color: hollow ? color : "#fff", fillColor: color, fillOpacity: hollow ? 0 : 0.9,
    }).bindTooltip(`${fmt.dec(f.distance_km)} km · ${agoFrom(f.acquired_at)}`, { className: "marker-label" }).addTo(layer);
    fires.fires.forEach((f) => add(f, levelColor("very_high"), false));
    fires.low_confidence.forEach((f) => add(f, levelColor("unknown"), true));
    fires.recurring_sources.forEach((f) => add(f, levelColor("unknown"), true));
  }
}

const metric = (name, value, label) => el("div", { class: "metric" }, icon(name, 22), el("div", {}, el("b", {}, value), el("span", {}, label)));
