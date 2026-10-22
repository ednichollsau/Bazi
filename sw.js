// No clinical responses, tokens or HTML are cached on this shared origin.
self.addEventListener('install', event => event.waitUntil(self.skipWaiting()));
self.addEventListener('activate', event => event.waitUntil(
  caches.keys().then(keys => Promise.all(keys.filter(key => key.startsWith('ena-')).map(key => caches.delete(key))))
    .then(() => self.clients.claim())
));
// Requests use the network normally; offline errors remain visible to callers.
