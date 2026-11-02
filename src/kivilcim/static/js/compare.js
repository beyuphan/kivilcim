// Before and after satellite images on the same map, split by a draggable line. Both are ordinary
// tile layers, so panning and zooming move them together; each is clipped to its side of the line.

import { el, icon, fmt } from "./ui.js";

const QUALITY = { clear: "açık hava", partly_cloudy: "az bulutlu", cloudy: "bulutlu", partial: "kısmi görüntü" };

/** One side of the comparison as a short description, e.g. "Sentinel-2 · 30 m · açık hava". */
export function describe(image) {
  return `${image.source} · ${image.resolution_m} m · ${QUALITY[image.quality] || image.quality}`;
}

export function createCompare(map, mapwrap, imagery, { onReady = () => {} } = {}) {
  let ratio = 0.5;
  let pending = 0;

  const layerFor = (image) => L.tileLayer(image.tile_url, {
    maxNativeZoom: image.max_native_zoom,
    maxZoom: 18,
    minZoom: 5,
    updateWhenIdle: true,
    keepBuffer: 1,
    attribution: imagery.attribution,
  });

  let before = imagery.before ? layerFor(imagery.before) : null;
  let after = imagery.after ? layerFor(imagery.after) : null;

  const wait = el("div", { class: "compare-wait", role: "status" }, "Uydu görüntüleri yükleniyor…");
  const handle = el("button", {
    class: "compare-handle", type: "button", role: "slider", "aria-label": "Öncesi ve sonrası görüntü ayırıcı",
    "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": "50", "aria-orientation": "horizontal",
  }, el("span", { class: "compare-knob" }, icon("arrows-h", 22)));
  const labels = [
    imagery.before && label("before", imagery.before),
    imagery.after && label("after", imagery.after),
  ];
  const overlay = el("div", { class: "compare" }, labels, handle, wait);

  function label(side, image) {
    return el("div", { class: "compare-label", dataset: { side } }, el("b", {}, `${image.label} · ${fmt.shortDay(image.date)}`), describe(image));
  }

  function clip() {
    const size = map.getSize();
    const nw = map.containerPointToLayerPoint([0, 0]);
    const se = map.containerPointToLayerPoint(size);
    const x = nw.x + (se.x - nw.x) * (before && after ? ratio : before ? 1 : 0);
    if (before) before.getContainer().style.clip = `rect(${nw.y}px, ${after ? x : se.x}px, ${se.y}px, ${nw.x}px)`;
    if (after) after.getContainer().style.clip = `rect(${nw.y}px, ${se.x}px, ${se.y}px, ${before ? x : nw.x}px)`;
    handle.style.left = `${ratio * 100}%`;
    handle.setAttribute("aria-valuenow", String(Math.round(ratio * 100)));
    handle.hidden = !(before && after);
  }

  // The notice shows while any of the layers is still fetching tiles.
  function track(layer) {
    layer.on("loading", () => { pending += 1; wait.hidden = false; });
    layer.on("load", () => {
      pending = Math.max(0, pending - 1);
      if (pending === 0) { wait.hidden = true; onReady(); }
    });
  }

  [before, after].forEach((layer) => { if (layer) { layer.addTo(map); track(layer); } });
  mapwrap.append(overlay);
  map.on("move zoom resize viewreset", clip);
  clip();
  // The layers' containers exist only after adding; clip once more when the first tiles arrive.
  [before, after].forEach((layer) => layer?.once("tileloadstart", clip));

  // ---- dragging and keys -----------------------------------------------------------------------
  const setRatio = (value) => { ratio = Math.min(Math.max(value, 0.02), 0.98); clip(); };
  const fromPointer = (e) => {
    const box = mapwrap.getBoundingClientRect();
    setRatio((e.clientX - box.left) / box.width);
  };
  handle.addEventListener("pointerdown", (e) => {
    handle.setPointerCapture(e.pointerId);
    map.dragging.disable();
    fromPointer(e);
  });
  handle.addEventListener("pointermove", (e) => { if (handle.hasPointerCapture(e.pointerId)) fromPointer(e); });
  const release = (e) => {
    if (handle.hasPointerCapture(e.pointerId)) handle.releasePointerCapture(e.pointerId);
    map.dragging.enable();
  };
  handle.addEventListener("pointerup", release);
  handle.addEventListener("pointercancel", release);
  handle.addEventListener("keydown", (e) => {
    const step = e.shiftKey ? 0.2 : 0.05;
    if (e.key === "ArrowLeft") { e.preventDefault(); setRatio(ratio - step); }
    else if (e.key === "ArrowRight") { e.preventDefault(); setRatio(ratio + step); }
    else if (e.key === "Home") { e.preventDefault(); setRatio(0.02); }
    else if (e.key === "End") { e.preventDefault(); setRatio(0.98); }
  });

  wait.hidden = pending === 0;

  /** Show another day on one side. */
  function swap(side, image) {
    const old = side === "before" ? before : after;
    const next = layerFor(image);
    if (side === "before") before = next;
    else after = next;
    next.addTo(map);
    track(next);
    next.once("tileloadstart", clip);
    if (old) map.removeLayer(old);
    const node = overlay.querySelector(`.compare-label[data-side="${side}"]`);
    if (node) node.replaceWith(label(side, image));
    clip();
  }

  return {
    setRatio,
    swap,
    destroy() {
      map.off("move zoom resize viewreset", clip);
      map.dragging.enable();
      [before, after].forEach((layer) => layer && map.removeLayer(layer));
      overlay.remove();
    },
  };
}

/** A small still of one satellite tile, for cards. Returns an <img> or a note when there is none. */
export function thumbnail(image, lat, lon, tilemath, zoom = 11) {
  const z = Math.min(zoom, image.max_native_zoom);
  const { x, y } = tilemath.tileFor(lat, lon, z);
  return el("img", {
    src: tilemath.tileUrl(image.tile_url, z, x, y),
    alt: "", loading: "lazy", decoding: "async", width: 256, height: 256,
  });
}
