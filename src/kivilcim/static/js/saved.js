// "My places": a short list kept in this browser only. Nothing about it is sent to the server
// except the place that is looked up when a page is open.

const KEY = "kv-places";
const MAX = 12;

export const idOf = (lat, lon) => `${lat.toFixed(2)},${lon.toFixed(2)}`;

function read() {
  try {
    const list = JSON.parse(localStorage.getItem(KEY) || "[]");
    return Array.isArray(list) ? list.filter((p) => p && Number.isFinite(p.lat) && Number.isFinite(p.lon) && typeof p.name === "string") : [];
  } catch {
    return [];
  }
}

function write(list) {
  try { localStorage.setItem(KEY, JSON.stringify(list)); } catch { /* private mode: the list lasts until the page closes */ }
}

export const list = () => read();
export const has = (lat, lon) => read().some((p) => idOf(p.lat, p.lon) === idOf(lat, lon));

/** Add a place (newest first). Returns false when the list is full. */
export function add({ name, lat, lon, r = 25 }) {
  const items = read().filter((p) => idOf(p.lat, p.lon) !== idOf(lat, lon));
  if (items.length >= MAX) return false;
  items.unshift({ name: name.slice(0, 60), lat, lon, r });
  write(items);
  return true;
}

export function remove(lat, lon) {
  write(read().filter((p) => idOf(p.lat, p.lon) !== idOf(lat, lon)));
}

export const limit = MAX;
