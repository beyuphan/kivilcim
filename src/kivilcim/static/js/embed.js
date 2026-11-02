// The embeddable map: the active zones of the last 24 hours, for other sites to show in a frame.
// Everything opens on Kıvılcım itself, in a new tab.

import { api } from "./api.js";
import { createMap, pin, heatColor, fit, TURKEY } from "./map.js";
import { el, fmt, agoFrom } from "./ui.js";

const config = JSON.parse(document.getElementById("kv-config")?.textContent || "{}");
const map = createMap(document.getElementById("map"), config);

api.zones(true).then((data) => {
  const bounds = [];
  for (const z of [...data.zones].sort((x, y) => x.total_frp_mw - y.total_frp_mw)) {
    const size = 28 + Math.min(22, Math.sqrt(z.detections) * 3);
    const marker = pin([z.latitude, z.longitude], {
      color: heatColor(z.total_frp_mw), label: String(z.detections), size, title: `${z.district}, ${z.province}: ${z.detections} tespit`,
      content: el("div", { class: "pop" }, el("b", {}, `${z.district}, ${z.province}`), el("span", { class: "pop-meta" }, `${fmt.num(z.detections)} tespit · ${fmt.dec(z.total_frp_mw)} MW`),
        el("a", { class: "btn btn-primary btn-small", href: `/?lat=${z.latitude.toFixed(4)}&lon=${z.longitude.toFixed(4)}&r=${Math.ceil(z.radius_km) + 5}&name=${encodeURIComponent(`${z.district}, ${z.province}`)}`, target: "_blank", rel: "noopener" }, "Kıvılcım'da aç")),
    });
    marker.addTo(map);
    bounds.push([z.latitude, z.longitude]);
  }
  const latest = data.data_as_of.latest_observation_at;
  document.getElementById("embed-sub").textContent = `${fmt.num(data.count)} bölge${latest ? ` · son gözlem ${agoFrom(latest)}` : ""}. Resmi bir uyarı sistemi değildir.`;
  if (bounds.length) fit(bounds, { maxZoom: 8 });
  else fit(TURKEY, { animate: false });
}).catch(() => {
  document.getElementById("embed-sub").textContent = "Veriler yüklenemedi.";
});
