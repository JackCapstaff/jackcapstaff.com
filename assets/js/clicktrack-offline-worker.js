'use strict';

const shell = new URL('./', self.location).href;
const cacheName = 'clicktrack-offline-shell-v1';

self.addEventListener('install', (event) => event.waitUntil(self.skipWaiting()));
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));
self.addEventListener('fetch', (event) => {
  if (event.request.mode !== 'navigate' || event.request.url !== shell) return;
  event.respondWith(caches.open(cacheName).then(async (cache) => (
    await cache.match(shell) || fetch(event.request)
  )));
});
