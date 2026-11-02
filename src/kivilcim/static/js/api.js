// Talking to the server. Every call can be aborted, and repeat calls within a short time are shared.

const memory = new Map();

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

async function getJSON(path, { signal, ttl = 0 } = {}) {
  const hit = memory.get(path);
  if (hit && Date.now() - hit.at < ttl) return hit.value;
  const response = await fetch(path, { signal, headers: { Accept: "application/json" } });
  if (!response.ok) throw new ApiError(response.status, `HTTP ${response.status}`);
  const value = await response.json();
  if (ttl) memory.set(path, { at: Date.now(), value });
  return value;
}

const q = (params) => new URLSearchParams(params).toString();

export const api = {
  /** Active zones. weather=false answers at once, without the weather index. */
  zones: (weather, signal) => getJSON(`/risk/zones?since_hours=24&weather=${weather}`, { signal, ttl: 20_000 }),
  fires: (lat, lon, radius, hours, signal) =>
    getJSON(`/fires?${q({ lat: lat.toFixed(5), lon: lon.toFixed(5), radius_km: radius, since_hours: hours })}`, { signal }),
  /** Weather for a place, or null when it is outside Turkey or the weather is unavailable. */
  async weather(lat, lon, signal) {
    try {
      return await getJSON(`/weather?${q({ lat: lat.toFixed(5), lon: lon.toFixed(5) })}`, { signal, ttl: 60_000 });
    } catch (error) {
      if (error.name === "AbortError") throw error;
      return null;
    }
  },
  status: (signal) => getJSON("/status", { signal, ttl: 20_000 }),
  events: (signal) => getJSON("/events?limit=100", { signal, ttl: 60_000 }),
  event: (id, signal) => getJSON(`/events/${encodeURIComponent(id)}?days_before=2&days_after=5&padding_deg=0.05`, { signal, ttl: 300_000 }),
  imagery: (id, signal) => getJSON(`/events/${encodeURIComponent(id)}/imagery`, { signal, ttl: 300_000 }),
  /** When the satellites last scanned a place and when they will next. Null when it cannot be told. */
  async passes(lat, lon, signal) {
    try {
      return await getJSON(`/passes?${q({ lat: lat.toFixed(3), lon: lon.toFixed(3) })}`, { signal, ttl: 120_000 });
    } catch (error) {
      if (error.name === "AbortError") throw error;
      return null;
    }
  },
  /** Whether the Telegram bot is set up, and its username. Never fails: no bot means no button. */
  async bot(signal) {
    try {
      return await getJSON("/alerts/bot", { signal, ttl: 600_000 });
    } catch (error) {
      if (error.name === "AbortError") throw error;
      return { enabled: false, username: null };
    }
  },
  /** The dates of the latest strong fire near a place, if any. */
  suggest: (lat, lon, signal) => getJSON(`/imagery/suggest?${q({ lat: lat.toFixed(3), lon: lon.toFixed(3) })}`, { signal, ttl: 120_000 }),
  /** Satellite images from before and after a fire at any place. */
  placeImagery: (lat, lon, start, end, signal) =>
    getJSON(`/imagery/place?${q({ lat: lat.toFixed(3), lon: lon.toFixed(3), start, end })}`, { signal, ttl: 300_000 }),
  places: (signal) => getJSON("/static/ilceler.json", { signal, ttl: Infinity }),
};
