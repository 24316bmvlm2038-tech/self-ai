// Lets the installed home-screen app open even when Self-AI isn't running: pages and assets
// come from the network when possible and from this cache otherwise. API calls are never cached.
const CACHE = 'selfai-shell-v1';
const SHELL = [
  '/',
  '/static/app.js',
  '/static/style.css',
  '/static/manifest.webmanifest',
  '/static/icon-192.png',
  '/static/vendor/marked.min.js',
  '/static/vendor/purify.min.js',
  '/static/vendor/highlight.min.js',
  '/static/vendor/hljs-dark.min.css',
  '/static/vendor/hljs-light.min.css',
];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== 'GET' || url.origin !== self.location.origin || url.pathname.startsWith('/api/')) return;
  event.respondWith(
    fetch(request)
      .then((response) => {
        if (response.ok) {
          const copy = response.clone();
          caches.open(CACHE).then((cache) => cache.put(request, copy));
        }
        return response;
      })
      .catch(async () => (await caches.match(request)) || (request.mode === 'navigate' && (await caches.match('/'))) || Response.error()),
  );
});
