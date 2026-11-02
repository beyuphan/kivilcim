/* Kıvılcım service worker.
 *
 * It makes the app installable and lets the page shell open without a connection. It never caches
 * live data: fire detections, weather, risk and status always come from the network, so stale
 * numbers are never shown as if they were current. Offline, the page opens and says it has no data.
 */
const VERSION = "__VERSION__";
const SHELL_CACHE = "kivilcim-shell-" + VERSION;
const ASSET_CACHE = "kivilcim-assets-" + VERSION;

const SHELL = [
  "/",
  "/history",
  "/methodology",
  "/manifest.webmanifest",
  "/static/pwa.js",
  "/static/ilceler.json",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
];

// Paths that must always hit the network.
const LIVE = ["/fires", "/risk", "/weather", "/status", "/events", "/health", "/docs", "/openapi.json"];

// Third-party files that are safe to keep (the map library). Map tiles are not cached here.
const CACHEABLE_HOSTS = ["unpkg.com"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE)
      // One missing file must not stop the install, so each is added on its own.
      .then((cache) => Promise.all(SHELL.map((url) => cache.add(url).catch(() => undefined))))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((key) => ![SHELL_CACHE, ASSET_CACHE].includes(key)).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});

function isLive(url) {
  return url.origin === self.location.origin
    && LIVE.some((path) => url.pathname === path || url.pathname.startsWith(path + "/"));
}

// Pages: the network first (so updates show), the stored copy when offline. The query string is
// ignored so every /?lat=..&lon=.. link falls back to the same stored page.
async function page(request) {
  const url = new URL(request.url);
  try {
    const response = await fetch(request);
    if (response.ok) (await caches.open(SHELL_CACHE)).put(url.pathname, response.clone());
    return response;
  } catch (error) {
    return (await caches.match(url.pathname)) || (await caches.match("/")) || Response.error();
  }
}

// Files that rarely change: the stored copy at once, refreshed in the background.
async function asset(request) {
  const cache = await caches.open(ASSET_CACHE);
  const stored = (await cache.match(request)) || (await caches.match(request));
  const refresh = fetch(request)
    .then((response) => { if (response.ok) cache.put(request, response.clone()); return response; })
    .catch(() => undefined);
  return stored || (await refresh) || Response.error();
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (isLive(url)) return; // leave it to the browser: network only

  if (request.mode === "navigate") {
    event.respondWith(page(request));
  } else if (url.origin === self.location.origin || CACHEABLE_HOSTS.includes(url.hostname)) {
    event.respondWith(asset(request));
  }
});
