// Place search: type a province or district and pick one. Works on the center points in
// ilceler.json, loaded only when the box is first used, and ignores Turkish letter forms
// ("sanliurfa" finds Şanlıurfa).

import { api } from "./api.js";
import { el, clear, icon, debounce } from "./ui.js";

const FOLD = { "ı": "i", "İ": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g", "ü": "u", "Ü": "u", "ö": "o", "Ö": "o", "ç": "c", "Ç": "c", "â": "a", "î": "i", "û": "u" };
export function fold(text) {
  return text
    .replace(/[ıİşŞğĞüÜöÖçÇâîû]/g, (c) => FOLD[c])
    .toLocaleLowerCase("tr")
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .trim();
}

let index = null;
let loading = null;

export async function loadIndex() {
  if (index) return index;
  loading ||= api.places().then((data) => {
    index = [];
    for (const province of data.provinces) {
      index.push({ kind: "il", name: province.name, sub: "İl", lat: province.lat, lon: province.lon, radius: 100, zoom: 8, key: fold(province.name), rank: 0 });
      for (const district of province.districts) {
        index.push({ kind: "ilçe", name: district.name, sub: province.name, lat: district.lat, lon: district.lon, radius: 25, zoom: 10, key: fold(district.name), province: fold(province.name), rank: 1 });
      }
    }
    return index;
  });
  return loading;
}

/** The matches for a query, best first: names that start with it, then words that start with it, then the rest. */
export function find(entries, query, limit = 8) {
  const q = fold(query);
  if (q.length < 1) return [];
  const scored = [];
  for (const entry of entries) {
    let score = -1;
    if (entry.key === q) score = 0;
    else if (entry.key.startsWith(q)) score = 1;
    else if (entry.key.split(/[\s-]+/).some((word) => word.startsWith(q))) score = 2;
    else if (entry.key.includes(q)) score = 3;
    else if (entry.province && `${entry.key} ${entry.province}`.includes(q)) score = 4;
    if (score >= 0) scored.push({ entry, score: score * 2 + entry.rank });
  }
  scored.sort((a, b) => a.score - b.score || a.entry.name.localeCompare(b.entry.name, "tr"));
  return scored.slice(0, limit).map((s) => s.entry);
}

/** The nearest named place to a point (within maxKm), for titles such as "Çankaya, Ankara". */
export function nearestName(entries, lat, lon, maxKm = 20) {
  let best = null;
  let bestKm = maxKm;
  for (const entry of entries) {
    if (entry.kind !== "ilçe") continue;
    const km = Math.hypot((entry.lat - lat) * 111, (entry.lon - lon) * 111 * Math.cos((lat * Math.PI) / 180));
    if (km < bestKm) { best = entry; bestKm = km; }
  }
  return best ? `${best.name}, ${best.sub}` : null;
}

/**
 * Builds the search box. `onPick(entry)` is called when a result is chosen; `onLocate()` for the
 * "my location" button. Implements the combobox pattern: arrow keys move, Enter picks, Escape closes.
 */
export function createSearch({ onPick, onLocate }) {
  const input = el("input", {
    class: "input", type: "search", id: "place-search", placeholder: "İl veya ilçe ara", autocomplete: "off", autocapitalize: "off",
    spellcheck: "false", enterkeyhint: "search", role: "combobox", "aria-expanded": "false", "aria-controls": "place-list", "aria-autocomplete": "list",
    "aria-label": "İl veya ilçe ara",
  });
  const list = el("div", { class: "listbox", id: "place-list", role: "listbox", "aria-label": "Sonuçlar", hidden: true });
  const locate = el("button", { class: "btn btn-secondary btn-icon", type: "button", "aria-label": "Konumumu kullan", title: "Konumumu kullan", onclick: () => onLocate() }, icon("locate"));
  const root = el("div", { class: "card search" }, el("div", { class: "input-wrap" }, icon("search"), input), locate, list);

  let results = [];
  let active = -1;

  function close() {
    list.hidden = true;
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
    active = -1;
  }
  function highlight(i) {
    active = i;
    [...list.children].forEach((child, n) => child.setAttribute("aria-selected", String(n === i)));
    if (i >= 0) {
      input.setAttribute("aria-activedescendant", `place-opt-${i}`);
      list.children[i].scrollIntoView({ block: "nearest" });
    } else input.removeAttribute("aria-activedescendant");
  }
  function pick(entry) {
    close();
    input.value = "";
    input.blur();
    onPick(entry);
  }
  function render(query) {
    clear(list);
    if (!query.trim()) return close();
    results.forEach((entry, i) => {
      list.append(el("div", { class: "option", role: "option", id: `place-opt-${i}`, "aria-selected": "false", onpointerdown: (e) => { e.preventDefault(); pick(entry); } },
        el("span", {}, entry.name), el("small", {}, entry.sub)));
    });
    if (!results.length) {
      list.append(el("div", { class: "listbox-empty", role: "presentation" }, `"${query}" için sonuç yok. İl ya da ilçe adının bir kısmını deneyin, örneğin "adana".`));
    }
    list.hidden = false;
    input.setAttribute("aria-expanded", "true");
    highlight(results.length ? 0 : -1);
  }

  const update = debounce(async () => {
    const entries = await loadIndex();
    results = find(entries, input.value);
    render(input.value);
  }, 80);

  input.addEventListener("focus", () => { loadIndex().catch(() => {}); });
  input.addEventListener("input", update);
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" && !list.hidden && results.length) { e.preventDefault(); highlight((active + 1) % results.length); }
    else if (e.key === "ArrowUp" && !list.hidden && results.length) { e.preventDefault(); highlight((active - 1 + results.length) % results.length); }
    else if (e.key === "Enter" && active >= 0) { e.preventDefault(); pick(results[active]); }
    else if (e.key === "Escape") { close(); }
  });
  input.addEventListener("blur", () => setTimeout(close, 120));

  return { root, input };
}
