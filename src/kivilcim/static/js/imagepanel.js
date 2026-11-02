// The before/after panel shared by a past fire and a place check: what was found, the days to choose
// from, and the comparison on the map. It says plainly when an image is missing and why.

import { icon, el, clear, fmt, announce } from "./ui.js";
import { createCompare, describe } from "./compare.js";

const QUALITY_TAG = { clear: "Açık", partly_cloudy: "Az bulutlu", cloudy: "Bulutlu", partial: "Kısmi" };

export function createImagePanel(ctx, { box, noteBox, subject = "yangın" }) {
  let imagery = null;
  let compare = null;
  let visible = true;
  let reload = null;

  const side = (image, label) => {
    if (!image) return el("div", { class: "compare-card" }, el("b", {}, label), el("span", {}, "Bulutsuz görüntü bulunamadı"));
    const level = image.quality === "clear" ? "low" : "moderate";
    return el("div", { class: "compare-card" }, el("b", {}, `${label} · ${fmt.shortDay(image.date)}`), el("span", {}, describe(image)),
      el("span", { class: "pill pill-plain tag", dataset: { level } }, QUALITY_TAG[image.quality] || image.quality));
  };

  function placeholder() {
    box.hidden = false;
    clear(box).append(
      el("p", { class: "card-title" }, "Uydu görüntüleri aranıyor…"),
      el("p", { class: "card-sub", style: { marginTop: "6px" } }, `${subject[0].toUpperCase()}${subject.slice(1)}dan önceki ve sonraki en iyi açık günü buluyoruz. İlk seferde birkaç saniye sürebilir.`),
      el("div", { class: "skeleton", style: { height: "56px", marginTop: "12px" } }));
  }

  function mountCompare() {
    if (compare || !visible || !imagery || imagery.error || (!imagery.before && !imagery.after)) return;
    compare = createCompare(ctx.map, ctx.mapwrap, imagery);
    ctx.layer.eachLayer((l) => l.bringToFront?.());
  }

  // Other usable days for one side. Choosing one swaps the image on the map.
  function dateChips(key, list, current) {
    const name = key === "before" ? "Öncesi" : "Sonrası";
    const group = el("div", { class: "chips", role: "group", "aria-label": `${name} için başka gün`, style: { marginTop: "12px" } },
      el("span", { class: "card-sub", style: { alignSelf: "center" } }, `${name}:`));
    for (const option of list) {
      const chip = el("button", { class: "chip", type: "button", "aria-pressed": String(option.date === current?.date), onclick: () => {
        compare?.swap(key, option);
        [...group.querySelectorAll(".chip")].forEach((c) => c.setAttribute("aria-pressed", String(c === chip)));
        box.querySelectorAll(".compare-card")[key === "before" ? 0 : 1]?.replaceWith(side(option, name));
        announce(`${name} görüntüsü: ${fmt.day(option.date)}`);
      } }, fmt.shortDay(option.date));
      group.append(chip);
    }
    return group;
  }

  function render() {
    clear(box);
    if (!imagery || imagery.error) {
      const limited = imagery?.error?.status === 429;
      const rejected = imagery?.error?.status === 422;
      box.append(
        el("p", { class: "card-title" }, limited ? "Çok sık istek yapıldı" : rejected ? "Bu tarihler için görüntü aranamaz" : "Uydu görüntüleri şu an alınamadı"),
        el("p", { class: "card-sub", style: { marginTop: "6px" } }, limited ? "Bir dakika sonra yeniden deneyin." : rejected ? "Tarihler 2016'dan bugüne olmalı, en fazla 120 gün arayla." : "Daha sonra yeniden deneyin."),
        reload && !rejected ? el("button", { class: "btn btn-secondary btn-small", type: "button", style: { marginTop: "12px" }, onclick: () => start(reload()) }, icon("refresh", 16), "Yeniden dene") : null);
      return;
    }
    const { before, after } = imagery;
    if (!after && imagery.waiting) {
      box.append(el("p", { class: "card-sub" }, `Sonrası görüntüsü henüz yok: ${subject} söndükten sonra dumanın dağılması için birkaç gün beklenir ve uydu görüntüleri 1-2 gün geç yayımlanır. Birkaç gün sonra yeniden bakın.`));
    }
    if (!before && !after) {
      box.append(el("p", { class: "card-title" }, `Bu ${subject} için kullanılabilir uydu görüntüsü bulunamadı`),
        el("p", { class: "card-sub", style: { marginTop: "6px" } }, "Bu tarihlerde bulutsuz görüntü yok; bulutlu görüntüleri duman sanılmasın diye göstermiyoruz. Tarihleri değiştirmeyi ya da birkaç gün sonra yeniden bakmayı deneyin."));
      return;
    }
    box.append(
      el("p", { class: "card-sub" }, `Haritadaki çizgiyi sağa sola sürükleyin. Soldaki ${subject}dan önce, sağdaki sonra. Yanık yerler koyu kahverengi ya da siyah görünür.`),
      el("div", { class: "compare-cards", style: { marginTop: "12px" } }, side(before, "Öncesi"), side(after, "Sonrası")));
    for (const [key, list, current] of [["before", imagery.before_options, before], ["after", imagery.after_options, after]]) {
      if (list?.length > 1) box.append(dateChips(key, list, current));
    }
    if (imagery.low_resolution) {
      box.append(el("p", { class: "card-sub", style: { marginTop: "12px" } }, "Bir taraf düşük çözünürlüklü (250 m) günlük görüntü. Bu tarihlerde 30 m çözünürlükte kullanılabilir bir görüntü bulamadık; ayrıntılar bulanık görünebilir."));
    }
    if (noteBox) {
      noteBox.hidden = false;
      clear(noteBox).append(el("summary", {}, icon("info", 18), "Bu görüntüler nereden?", icon("chevron-down", 18)),
        el("div", { class: "body" },
          el("p", {}, `${subject[0].toUpperCase()}${subject.slice(1)}dan önceki ve sonraki en yakın açık günün uydu görüntüleri. Görüntüler olay anına değil, gösterilen güne aittir; yanık izi silik olabilir, bulut onu gizleyebilir.`),
          el("p", {}, "Kaynak: NASA GIBS / ESDIS (Harmonized Landsat Sentinel-2, VIIRS, MODIS)."),
          el("p", {}, "Resmi bir kayıt ya da hasar tespiti değildir.")));
    }
    mountCompare();
  }

  /** Show the placeholder, wait for the images, then show them. `promise` resolves to the API answer. */
  function start(promise, retry) {
    if (retry) reload = retry;
    compare?.destroy();
    compare = null;
    placeholder();
    promise.then((data) => { imagery = data; }, (error) => { imagery = error.name === "AbortError" ? null : { error }; })
      .then(() => { if (!ctx.signal.aborted && imagery !== null) render(); });
  }

  return {
    start,
    /** Hide or show the comparison on the map (the spread view of a fire replaces it). */
    show(on) {
      visible = on;
      if (on) mountCompare();
      else { compare?.destroy(); compare = null; }
    },
    destroy() { compare?.destroy(); compare = null; },
  };
}
