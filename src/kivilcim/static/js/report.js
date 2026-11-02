// A one-page report of the satellite images before and after a fire, made to be printed or saved as a PDF:
// the two images side by side, the dates, where they come from, and what they cannot tell.

import { api } from "./api.js";
import { el, clear, fmt } from "./ui.js";
import { describe } from "./compare.js";

const root = document.getElementById("report");
const params = new URLSearchParams(location.search);
const printButton = document.getElementById("print");
printButton.addEventListener("click", () => window.print());

async function load() {
  const eventId = params.get("event");
  if (eventId) {
    const [detail, imagery] = await Promise.all([api.event(eventId), api.imagery(eventId)]);
    const e = detail.event;
    return {
      title: `${e.district}, ${e.province}`,
      subtitle: `Uydu tespitlerine göre yangın: ${fmt.range(e.start, e.end)} · ${e.days} gün · ${fmt.num(e.detections)} tespit`,
      place: `${e.center_lat.toFixed(3)}° K, ${e.center_lon.toFixed(3)}° D (yaklaşık, ilçe merkezine göre ${e.district})`,
      imagery,
      card: `/events/${encodeURIComponent(eventId)}/card.jpg`,
    };
  }
  const lat = Number(params.get("lat"));
  const lon = Number(params.get("lon"));
  const start = params.get("start");
  const end = params.get("end") || start;
  if (!Number.isFinite(lat) || !Number.isFinite(lon) || !start) throw new Error("missing");
  const imagery = await api.placeImagery(lat, lon, start, end);
  const name = (params.get("name") || "").slice(0, 60);
  const card = `/imagery/place/card.jpg?${new URLSearchParams({ lat: lat.toFixed(3), lon: lon.toFixed(3), start, end, ...(name ? { name } : {}) })}`;
  return {
    title: name || "Seçilen yer",
    subtitle: `Kullanıcının belirttiği yangın tarihi: ${fmt.range(start, end)}`,
    place: `${lat.toFixed(2)}° K, ${lon.toFixed(2)}° D (yaklaşık 1 km'ye yuvarlanmış)`,
    imagery,
    card,
  };
}

const QUALITY = { clear: "açık", partly_cloudy: "az bulutlu", cloudy: "bulutlu", partial: "kısmi" };

function sideRow(label, image) {
  if (!image) return el("tr", {}, el("td", {}, label), el("td", { colspan: 4 }, "Bulutsuz görüntü bulunamadı"));
  return el("tr", {}, el("td", {}, label), el("td", {}, fmt.day(image.date)), el("td", {}, image.source), el("td", {}, `${image.resolution_m} m`),
    el("td", {}, `${QUALITY[image.quality] || image.quality} (bulut ≈ %${Math.round(image.cloud * 100)})`));
}

load().then((data) => {
  document.title = `${data.title} · uydu raporu · Kıvılcım`;
  const { imagery } = data;
  const has = imagery.before || imagery.after;
  clear(root).append(
    el("p", { class: "muted" }, "Kıvılcım · uydu raporu"),
    el("h1", {}, data.title),
    el("p", {}, data.subtitle),
    el("p", { class: "muted" }, data.place),
    has ? el("img", { class: "report-image", src: data.card, alt: `${data.title}: yangından önceki ve sonraki uydu görüntüsü yan yana`, width: 1200, height: 630 }) : el("div", { class: "notice" }, "Bu tarihler için bulutsuz uydu görüntüsü bulunamadı."),
    el("h2", {}, "Görüntüler"),
    el("div", { class: "table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {}, ["", "Tarih", "Uydu", "Çözünürlük", "Hava durumu"].map((h) => el("th", {}, h)))),
      el("tbody", {}, sideRow("Öncesi", imagery.before), sideRow("Sonrası", imagery.after)))),
    imagery.low_resolution ? el("p", {}, "Bir taraf düşük çözünürlüklü (250 m) günlük görüntüdür; ayrıntılar bulanık olabilir.") : "",
    el("h2", {}, "Bu rapor ne söyler, ne söylemez"),
    el("ul", {},
      el("li", {}, "Görüntüler, yangından önceki ve sonraki, bulutu %10'un altında olan en yakın günlere aittir; olay anına ait değildir."),
      el("li", {}, "Yanık izi görüntüde koyu kahverengi ya da siyah görünür; bunu görüntüye bakıp kendiniz değerlendirirsiniz. Bulut gölgesi, sulak alan ve sürülmüş tarla yanık izi gibi görünebilir."),
      el("li", {}, "Bu bir hasar tespiti, bilirkişi raporu ya da resmi kayıt değildir. Sigorta, hukuki ya da resmi işlemler için uzman görüşü gerekir."),
      el("li", {}, "30 m çözünürlük, küçük yapıları (tek bir ev) göstermez; yalnızca arazi ölçeğinde değişimi gösterir.")),
    el("h2", {}, "Kaynak"),
    el("p", {}, "NASA GIBS / ESDIS: Harmonized Landsat Sentinel-2 (30 m), VIIRS ve MODIS (250 m, yedek). Görüntüler herkese açık veridir."),
    el("p", { class: "muted" }, `Rapor ${fmt.time(new Date().toISOString())} tarihinde oluşturuldu. Bağlantı: ${location.href}`),
  );
  printButton.hidden = false;
}).catch((error) => {
  clear(root).append(el("div", { class: "notice" }, error.status === 422
    ? "Bu tarihler için rapor hazırlanamaz: tarihler 2016'dan bugüne olmalı ve en fazla 120 gün arayla seçilmelidir."
    : "Rapor hazırlanamadı. Yer ve tarih bilgisini kontrol edip yeniden deneyin."));
});
