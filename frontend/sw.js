// Minimal service worker — exists to satisfy PWA installability requirements
// on Chrome / Edge (manifest alone isn't enough; the browser also needs a
// service worker that handles fetch events before it'll fire
// beforeinstallprompt).
//
// No caching strategy yet; fetches pass through to the network. Future
// iterations can add offline cache for /index.html / /manifest.json / static
// assets. Version bump the CACHE_NAME when that's added.
const CACHE_NAME = "livechord-v1";

self.addEventListener("install", (event) => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("fetch", (event) => {
  // Pass-through. Keeping an empty listener is enough to satisfy installability;
  // avoid responding explicitly so the browser's default network fetch runs.
});
