// The methodology page's measured results, read from validation.json.

import { el } from "./ui.js";

const box = document.getElementById("validation");

fetch("/static/validation.json")
  .then((response) => {
    if (!response.ok) throw new Error("none");
    return response.json();
  })
  .then((v) => {
    box.replaceChildren(el("p", {}, v.summary));
    if (v.usage_note) box.append(el("p", { style: { fontWeight: "650" } }, v.usage_note));
    if (v.tests?.length) {
      box.append(el("div", { class: "table-wrap" }, el("table", {},
        el("thead", {}, el("tr", {}, ["Ölçüm", "Sonuç", "Örnek sayısı", "Yorum"].map((h) => el("th", {}, h)))),
        el("tbody", {}, v.tests.map((t) => el("tr", {}, el("td", {}, t.name), el("td", {}, t.result), el("td", { class: "num" }, String(t.n)), el("td", {}, t.comment)))))));
    }
    (v.caveats || []).forEach((c) => box.append(el("p", { class: "muted" }, c)));
    if (v.generated) box.append(el("p", { class: "muted" }, `Üretildi: ${v.generated} (indeks ${v.index_version})`));
  })
  .catch(() => box.replaceChildren(el("p", { class: "muted" }, "Doğrulama sonuçları henüz yayımlanmadı.")));
