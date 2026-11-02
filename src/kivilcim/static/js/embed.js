// The embeddable map: the active zones of the last 24 hours, for other sites to show in a frame.
// Everything opens on Kıvılcım itself, in a new tab.

import { api } from "./api.js";
import { createMap, levelColor, getCanvas, fit, TURKEY } from "./map.js";
import { fmt, agoFrom } from "./ui.js";

const config = JSON.parse(document.getElementById("kv-config")?.textContent || "{}");
const map = createMap(document.getElementById("map"), config);

api.zones(true).then((data) => {
  const bounds = [];
  for (const z of data.zones) {
    const marker = L.circleMarker([z.latitude, z.longitude], {
      renderer: getCanvas(), radius: 7 + Math.min(12, Math.sqrt(z.detections) * 2), weight: 2, color: "#fff", fillOpacity: 0.85, fillColor: levelColor(z.risk?.level),
    });
    marker.bindTooltip(`${z.district}, ${z.province} · ${fmt.num(z.detections)} tespit`, { direction: "top", className: "marker-label" });
    marker.on("click", () => window.open(`/?lat=${z.latitude.toFixed(4)}&lon=${z.longitude.toFixed(4)}&r=${Math.ceil(z.radius_km) + 5}&name=${encodeURIComponent(`${z.district}, ${z.province}`)}`, "_blank", "noopener"));
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
