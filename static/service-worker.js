/* Scandroid PWA service worker — v7 */
const CACHE_VERSION = 'v8'; // Change this on every deploy
const CACHE_NAME = `scandroid-cache-${CACHE_VERSION}`;

const PRECACHE_URLS = [
  "/", "/fsp-login", "/fsp-admin", "/scan", "/offline",
  "/beneficiary-offline", "/success-offline",
  "/static/scandroid.png", "/static/scandroid_banner.png",
  "/static/ns1.png", "/static/ns2.png",
  "https://cdn.jsdelivr.net/npm/jsqr@1.4.0/dist/jsQR.js"
];

console.log(`[ServiceWorker] Installing version: ${CACHE_NAME}`);

self.addEventListener("install", (event) => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(PRECACHE_URLS))
  );
});

self.addEventListener("activate", (event) => {
  self.clients.claim();
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys
          .filter(k => k !== CACHE_NAME)
          .map(k => caches.delete(k))
      )
    )
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);

  // Handle /ping separately
  if (url.pathname === "/ping") {
    event.respondWith(fetch(req, { cache: "no-store" }).catch(() =>
      new Response("", { status: 503 })
    ));
    return;
  }

  // Special handling for Kobo sync ZIP
  if (url.pathname === "/api/offline/latest.zip") {
    event.respondWith(networkThenCache(req));
    return;
  }

  // Handle navigation (e.g. page load) with network-first strategy
  const isNavigation = req.mode === "navigate" ||
                       (req.headers.get("accept") || "").includes("text/html");
  if (isNavigation) {
    event.respondWith(networkFirstNavigation(req));
    return;
  }

  // Handle static assets (scripts, images, styles)
  if (isStatic(req)) {
    event.respondWith(cacheFirst(req));
  }
});

/* ---------- helpers ---------- */

function isStatic(req) {
  const url = new URL(req.url);
  return (
    (url.origin === location.origin &&
      (/\/static\//.test(url.pathname) ||
        /\.(js|css|png|jpg|jpeg|webp|svg|ico)$/i.test(url.pathname))) ||
    /cdn.jsdelivr.net$/.test(url.host)
  );
}

async function networkFirstNavigation(req) {
  try {
    const fresh = await fetch(req, { cache: "no-store" });
    const cache = await caches.open(CACHE_NAME);
    cache.put(req, fresh.clone());
    return fresh;
  } catch (e) {
    const cache = await caches.open(CACHE_NAME);
    const url = new URL(req.url);

      let cached = await cache.match(req, { ignoreSearch: true });
      if (cached) return cached;

      let path = url.pathname;

      // Try raw path
      cached = await cache.match(path);
      if (cached) return cached;

      // Try with trailing slash
      cached = await cache.match(path + '/');
      if (cached) return cached;

      // Try .html fallback
      cached = await cache.match(path + '.html');
      if (cached) return cached;

    const offline = await cache.match("/offline");
    return offline || new Response("Offline", { status: 503 });
  }
}

async function cacheFirst(req) {
  const cache = await caches.open(CACHE_NAME);
  const cached = await cache.match(req, { ignoreSearch: true });
  if (cached) return cached;

  const fresh = await fetch(req);
  cache.put(req, fresh.clone());
  return fresh;
}

async function networkThenCache(req) {
  try {
    const res = await fetch(req, { cache: "no-store" });
    const cache = await caches.open(CACHE_NAME);
    cache.put(req, res.clone());
    return res;
  } catch (e) {
    const cache = await caches.open(CACHE_NAME);
    const cached = await cache.match(req, { ignoreSearch: true });
    if (cached) return cached;
    throw e;
  }
}
