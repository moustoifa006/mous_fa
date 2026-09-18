// Service worker minimal pour rendre "Nous Deux" installable comme une application.
const CACHE_NAME = 'nous-deux-v1';
const APP_SHELL = [
  '/static/style.css',
  '/static/script.js',
  '/static/manifest.json',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL)).catch(() => {})
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// Stratégie "réseau d'abord" : les données du couple doivent toujours être fraîches.
// En cas de coupure réseau, on retombe sur le cache de l'app shell si possible.
self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') return;
  const url = new URL(event.request.url);
  if (url.origin !== location.origin) return;

  event.respondWith(
    fetch(event.request)
      .then((res) => {
        if (res.ok && APP_SHELL.some((p) => url.pathname === p)) {
          const clone = res.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
        }
        return res;
      })
      .catch(() => caches.match(event.request))
  );
});

// Réception d'une notification push envoyée par le serveur.
self.addEventListener('push', (event) => {
  let data = { title: 'Nous Deux 💞', body: 'Il y a du nouveau !', url: '/', message_id: null };
  if (event.data) {
    try { data = { ...data, ...event.data.json() }; }
    catch (e) { data.body = event.data.text(); }
  }

  const openUrl = data.message_id
    ? `${data.url || '/decouvrir'}?reply=${encodeURIComponent(data.message_id)}`
    : (data.url || '/');

  event.waitUntil(
    self.registration.showNotification(data.title || 'Nous Deux 💞', {
      body: data.body || '',
      icon: '/static/icons/icon-192.png',
      badge: '/static/icons/icon-192.png',
      vibrate: [200, 100, 200],
      tag: data.message_id ? `message-${data.message_id}` : 'nous-deux',
      renotify: true,
      requireInteraction: false,
      actions: data.message_id ? [{ action: 'reply', title: '↩️ Répondre' }] : [],
      data: { url: openUrl, message_id: data.message_id || null },
    })
  );
});

// Clic sur la notification : ouvre directement la conversation et
// sélectionne le message concerné pour permettre une réponse précise.
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const data = event.notification.data || {};
  const url = data.url || '/';
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
      for (const client of clientList) {
        if (client.url.includes(location.origin) && 'focus' in client) {
          if ('navigate' in client) client.navigate(url);
          return client.focus();
        }
      }
      if (clients.openWindow) return clients.openWindow(url);
    })
  );
});
