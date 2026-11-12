// Quirio Alerta: funciona sin internet con lo último que vio.
// Avisos en el celular (OneSignal). Si no carga (sin internet o bloqueado), la app sigue funcionando igual.
try { importScripts("https://cdn.onesignal.com/sdks/web/v16/OneSignalSDK.sw.js"); } catch (e) {}
const CACHE = "quirio-v15";
const TESELAS = "quirio-teselas-v1";       // mapa de calles/satélite que la persona ya miró
const MAX_TESELAS = 1500;                   // ~30 MB como máximo
const BASE = ["./", "index.html", "manifest.webmanifest", "iconos/icono-v2-192.png", "iconos/icono-v2-512.png", "iconos/icono-v2-maskable.png",
  "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.js"];
self.addEventListener("install", e => { e.waitUntil(caches.open(CACHE).then(c => c.addAll(BASE)).then(() => self.skipWaiting())); });
self.addEventListener("activate", e => { e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE && k !== TESELAS).map(k => caches.delete(k)))).then(() => self.clients.claim())); });
async function recortar() {
  const c = await caches.open(TESELAS); const ks = await c.keys();
  for (let i = 0; i < ks.length - MAX_TESELAS; i++) await c.delete(ks[i]);
}
self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (url.pathname.endsWith("estado.json")) {          // estado: primero internet, si no hay, el último guardado
    e.respondWith(fetch(e.request).then(r => { const c = r.clone(); caches.open(CACHE).then(k => k.put("estado.json", c)); return r; })
      .catch(() => caches.match("estado.json")));
    return;
  }
  if (url.hostname === "tile.openstreetmap.org" || url.hostname === "server.arcgisonline.com" || url.pathname.includes("/orto/")) {
    // Solo se guardan las teselas que la persona ya vio (no se descargan en masa: regla de OpenStreetMap).
    e.respondWith(caches.open(TESELAS).then(c => c.match(e.request).then(guardada => {
      const red = fetch(e.request).then(r => { if (r.ok || r.type === "opaque") { c.put(e.request, r.clone()); recortar(); } return r; })
        .catch(() => guardada);
      return guardada || red;
    })));
    return;
  }
  e.respondWith(caches.match(e.request).then(r => r || fetch(e.request)));
});
