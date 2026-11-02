// Is the wind carrying a fire toward a place? Plain geometry on what was measured now: where the
// fire is, which way the wind blows, how fast. It is not a forecast of where a fire will go: wind
// changes within hours, and terrain and fuel matter more than anything here.

const RAD = Math.PI / 180;

/** The compass bearing in degrees (0 = north) from the first point to the second. */
export function bearing(lat1, lon1, lat2, lon2) {
  const dLon = (lon2 - lon1) * RAD;
  const y = Math.sin(dLon) * Math.cos(lat2 * RAD);
  const x = Math.cos(lat1 * RAD) * Math.sin(lat2 * RAD) - Math.sin(lat1 * RAD) * Math.cos(lat2 * RAD) * Math.cos(dLon);
  return (Math.atan2(y, x) / RAD + 360) % 360;
}

/** The smallest angle between two bearings, 0 to 180. */
export const angleDiff = (a, b) => Math.abs(((a - b + 540) % 360) - 180);

const NAMES = ["kuzey", "kuzeydoğu", "doğu", "güneydoğu", "güney", "güneybatı", "batı", "kuzeybatı"];
export const compassName = (deg) => NAMES[Math.round((((deg % 360) + 360) % 360) / 45) % 8];

/** "güneybatıdan" for a direction something comes from, "kuzeydoğuya" for one it goes to. */
const FROM = { kuzey: "kuzeyden", kuzeydoğu: "kuzeydoğudan", doğu: "doğudan", güneydoğu: "güneydoğudan", güney: "güneyden", güneybatı: "güneybatıdan", batı: "batıdan", kuzeybatı: "kuzeybatıdan" };
export const fromName = (deg) => FROM[compassName(deg)];

export const CALM_KMH = 8; // below this the wind carries nothing in a particular direction

/**
 * How the wind relates to a fire and a place.
 * `windFrom` is the direction the wind blows from (as weather reports give it), in degrees.
 * Returns { relation: "toward" | "across" | "away" | "calm", fireBearing, windTo }:
 * "toward" means the wind blows from the fire to the place (within 45 degrees).
 */
export function windRelation(fire, place, windFrom, speedKmh) {
  const fireBearing = bearing(place.lat, place.lon, fire.lat, fire.lon); // from the place to the fire
  if (windFrom == null || speedKmh == null || speedKmh < CALM_KMH) return { relation: "calm", fireBearing, windTo: null };
  // The wind blows from `windFrom`. It carries things from the fire to the place when it comes
  // from the fire's side of the place, that is, when windFrom is close to the bearing to the fire.
  const diff = angleDiff(windFrom, fireBearing);
  const relation = diff <= 45 ? "toward" : diff <= 100 ? "across" : "away";
  return { relation, fireBearing, windTo: (windFrom + 180) % 360 };
}

/** One sentence in Turkish and a danger level for the pill, for a fire `km` away. */
export function describe(relation, km) {
  switch (relation) {
    case "toward":
      return { level: km <= 15 ? "very_high" : "high", short: "Rüzgar yangından size doğru", text: "Rüzgar şu an yangın tarafından size doğru esiyor." };
    case "across":
      return { level: "moderate", short: "Rüzgar yan yönde", text: "Rüzgar yangına göre yan yönde esiyor; doğrudan size taşımıyor." };
    case "away":
      return { level: "low", short: "Rüzgar yangından uzağa", text: "Rüzgar şu an yangından sizden uzağa doğru esiyor." };
    default:
      return { level: "unknown", short: "Rüzgar zayıf", text: "Rüzgar zayıf; belirli bir yöne taşımıyor." };
  }
}
