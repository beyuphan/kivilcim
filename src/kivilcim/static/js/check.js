// "Did it burn?": the satellite before and after a fire at any place, for a house, a field or a forest.
// The dates are filled in from what the satellites saw nearby when there was a fire, and can be changed.

import { api } from "./api.js";
import { el, clear, icon, fmt, announce } from "./ui.js";
import { flyTo, getCanvas } from "./map.js";
import { createImagePanel } from "./imagepanel.js";
import { loadIndex, nearestName } from "./search.js";

const FIRST = "2016-01-01";
const DAY = 86400000;

const today = () => new Date().toISOString().slice(0, 10);

export function mount(ctx, params) {
  const { body, layer, signal } = ctx;
  const lat = Number(params.get("lat"));
  const lon = Number(params.get("lon"));
  let name = params.get("name") || "";
  ctx.setTitle("Yerim yandı mı?");

  body.append(
    el("a", { class: "btn btn-ghost btn-small", href: "/", "data-link": "", style: { alignSelf: "flex-start" } }, icon("arrow-left", 18), "Ana sayfa"),
    el("div", { class: "section-title" }, el("h2", {}, "Yerim yandı mı?")),
    el("p", { class: "lede" }, "Bir yerin yangından önceki ve sonraki uydu görüntüsünü yan yana görün. Ev, tarla ya da orman için yanık izine kendiniz bakabilirsiniz."),
  );

  if (!Number.isFinite(lat) || !Number.isFinite(lon) || Math.abs(lat) > 90 || Math.abs(lon) > 180) {
    body.append(el("div", { class: "card card-pad" },
      el("p", { class: "card-title" }, "Önce yeri seçin"),
      el("p", { class: "card-sub", style: { marginTop: "6px" } }, "Yukarıdaki kutudan il ya da ilçe arayın, ya da konum düğmesine basın. Sonra bu sayfada \"Yerim yandı mı?\" düğmesine basın."),
      el("button", { class: "btn btn-primary", type: "button", style: { marginTop: "12px" }, onclick: () => document.getElementById("place-search")?.focus() }, icon("search", 18), "Yer ara")));
    return;
  }

  const title = el("p", { class: "card-title" }, name || "Seçilen yer");
  const hint = el("p", { class: "card-sub", style: { marginTop: "6px" } }, "Yangının tarihleri aranıyor…");
  const start = el("input", { class: "input date", type: "date", id: "check-start", min: FIRST, max: today(), required: true, value: params.get("start") || "" });
  const end = el("input", { class: "input date", type: "date", id: "check-end", min: FIRST, max: today(), value: params.get("end") || "" });
  const error = el("p", { class: "field-error", role: "alert", hidden: true });
  const go = el("button", { class: "btn btn-primary", type: "submit" }, icon("layers", 18), "Uydu görüntülerini göster");
  const form = el("form", { class: "card card-pad form", novalidate: true },
    title, hint,
    el("label", { for: "check-start" }, "Yangın ne zaman başladı?"), start,
    el("label", { for: "check-end" }, "Ne zaman bitti? (boşsa aynı gün; yangın en fazla 120 gün sürmüş olabilir)"), end,
    error, go);
  const box = el("div", { class: "card card-pad", hidden: true });
  const noteBox = el("details", { class: "card disclosure", hidden: true });
  body.append(form, box, noteBox);

  layer.clearLayers();
  L.circleMarker([lat, lon], { renderer: getCanvas(), radius: 8, weight: 4, color: "#fff", fillColor: "#2563EB", fillOpacity: 1, interactive: false }).addTo(layer);
  flyTo(lat, lon, 12);

  let cardUrl = "";
  let reportUrl = "";
  const panel = createImagePanel(ctx, { box, noteBox, subject: "yangın", card: () => cardUrl, report: () => reportUrl });
  ctx.onCleanup(() => panel.destroy());

  if (!name) loadIndex().then((entries) => {
    name = nearestName(entries, lat, lon) || "";
    if (name) title.textContent = name;
  }).catch(() => {});

  // The fires the satellites saw near this place in the whole archive (2016 to today): pick one, or type dates.
  api.suggest(lat, lon, signal).then((s) => {
    if (!s.found) {
      hint.textContent = "Uydular bu yerin yakınında (yaklaşık 15 km) 2016'dan beri güçlü bir yangın görmemiş. Bir yangın olduysa tarihini kendiniz girin: uydu görüntüleri 2016'dan bugüne her yer için var, tespit olmasa da.";
      return;
    }
    hint.textContent = s.episodes.length > 1
      ? "Uydunun bu yerin yakınında (yaklaşık 15 km) gördüğü yangınlar aşağıda. Birine dokunun, tarihler dolsun; ya da kendi tarihinizi yazın."
      : `Uydu bu yerin yaklaşık 15 km çevresinde ${fmt.range(s.start, s.end)} arasında ${fmt.num(s.detections)} güçlü tespit gördü. Tarihler buna göre dolduruldu; yangın başka bir tarihteyse değiştirin.`;
    if (s.episodes.length > 1) {
      const chips = el("div", { class: "chips", role: "group", "aria-label": "Bu yerin yakınındaki yangınlar" });
      for (const e of s.episodes) {
        const chip = el("button", { class: "chip", type: "button", "aria-pressed": "false", title: `${fmt.num(e.detections)} tespit, en güçlüsü ${fmt.dec(e.max_frp_mw)} MW`, onclick: () => {
          start.value = e.start;
          end.value = e.end;
          chips.querySelectorAll(".chip").forEach((c) => c.setAttribute("aria-pressed", String(c === chip)));
          run();
        } }, `${fmt.shortDay(e.start)} ${e.start.slice(0, 4)}`);
        chips.append(chip);
      }
      hint.after(chips);
    }
    if (!start.value) { start.value = s.start; end.value = s.end; }
    if (params.get("auto") !== null || params.get("start")) run();
  }).catch(() => { hint.textContent = "Yangının tarihlerini girin."; });

  form.addEventListener("submit", (event) => { event.preventDefault(); run(); });

  function run() {
    const from = start.value;
    const to = end.value || from;
    const problem = !from ? "Başlangıç tarihini girin."
      : from < FIRST || to > today() ? "Tarihler 2016'dan bugüne olmalı."
        : to < from ? "Bitiş tarihi başlangıçtan önce olamaz."
          : (Date.parse(to) - Date.parse(from)) / DAY > 120 ? "Yangın başlangıcıyla bitişi arası en fazla 120 gün olabilir."
            : "";
    error.hidden = !problem;
    error.textContent = problem;
    if (problem) { start.focus(); return; }
    const url = new URL(location.href);
    url.searchParams.set("start", from);
    url.searchParams.set("end", to);
    url.searchParams.delete("auto");
    history.replaceState({}, "", url);
    cardUrl = `/imagery/place/card.jpg?${new URLSearchParams({ lat: lat.toFixed(3), lon: lon.toFixed(3), start: from, end: to, ...(name ? { name } : {}) })}`;
    reportUrl = `/report?${new URLSearchParams({ lat: lat.toFixed(3), lon: lon.toFixed(3), start: from, end: to, ...(name ? { name } : {}) })}`;
    announce("Uydu görüntüleri aranıyor");
    panel.start(api.placeImagery(lat, lon, from, to, signal), () => api.placeImagery(lat, lon, from, to, signal));
  }
}
