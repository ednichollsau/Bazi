// Bump this version whenever you deploy a change — the SW will auto-update and reload all clients.
const CACHE = 'ena-v3';

// ── Install: pre-cache the dashboard shell ──────────────
self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE)
      .then(c => c.add('/dashboard?token=earseed2026'))
      .catch(() => {}) // don't fail install if pre-cache fails
      .then(() => self.skipWaiting()) // activate immediately, don't wait for old SW to die
  );
});

// ── Activate: delete old caches, claim all clients ──────
self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(
        keys.filter(k => k !== CACHE).map(k => caches.delete(k))
      ))
      .then(() => self.clients.claim()) // take control of all open tabs immediately
  );
});

// ── Fetch ───────────────────────────────────────────────
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);

  // 1. API calls: always network-only, never cache
  if (url.pathname.startsWith('/api/')) {
    e.respondWith(
      fetch(e.request).catch(() =>
        new Response('{"error":"offline"}', {
          headers: { 'Content-Type': 'application/json' }
        })
      )
    );
    return;
  }

  // 2. Dashboard HTML: network-first so deployments show immediately,
  //    fall back to cache only when genuinely offline.
  if (url.pathname === '/dashboard') {
    e.respondWith(
      fetch(e.request)
        .then(res => {
          if (res.ok) {
            // Update cache with fresh response
            const clone = res.clone();
            caches.open(CACHE).then(c => c.put(e.request, clone));
          }
          return res;
        })
        .catch(() => caches.match(e.request)) // offline fallback
    );
    return;
  }

  // 3. Static assets (icons, manifest, fonts): cache-first for performance
  e.respondWith(
    caches.match(e.request).then(cached => cached ||
      fetch(e.request).then(res => {
        if (res.ok) {
          const clone = res.clone();
          caches.open(CACHE).then(c => c.put(e.request, clone));
        }
        return res;
      })
    )
  );
});
