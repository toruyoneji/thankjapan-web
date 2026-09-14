importScripts('https://www.gstatic.com/firebasejs/9.23.0/firebase-app-compat.js');
importScripts('https://www.gstatic.com/firebasejs/9.23.0/firebase-messaging-compat.js');

const firebaseConfig = {
  apiKey: "{{ FIREBASE_API_KEY }}",
  authDomain: "{{ FIREBASE_PROJECT_ID }}.firebaseapp.com",
  projectId: "{{ FIREBASE_PROJECT_ID }}",
  storageBucket: "{{ FIREBASE_PROJECT_ID }}.firebasestorage.app",
  messagingSenderId: "{{ FIREBASE_MESSAGING_SENDER_ID }}",
  appId: "{{ FIREBASE_APP_ID }}"
};

firebase.initializeApp(firebaseConfig);
const messaging = firebase.messaging();

const CACHE_NAME = 'thankjapan-v3';
const ASSETS_TO_CACHE = [
  '/',
  '/static/css/main.css'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(ASSETS_TO_CACHE);
    })
  );
  self.skipWaiting();
});

// Without this, a new SW version activates (skipWaiting above) but keeps
// deferring to whatever SW instance already controls each open tab until
// that tab is closed or hard-reloaded - so a page left open across a
// deploy can keep being served by stale cached fallback responses from the
// previous SW. clients.claim() makes the new SW take over every open tab
// as soon as it activates, and deleting old-named caches here stops
// previous deploys' cached entries from lingering indefinitely.
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((names) => {
      return Promise.all(
        names.filter((name) => name !== CACHE_NAME).map((name) => caches.delete(name))
      );
    }).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        return response;
      })
      .catch(() => {
        return caches.match(event.request).then((cachedResponse) => {
          if (cachedResponse) {
            return cachedResponse;
          }
          return new Response('Network error occurred and no cache available.', {
            status: 408,
            headers: { 'Content-Type': 'text/plain' }
          });
        });
      })
  );
});

messaging.onBackgroundMessage((payload) => {
  const notificationTitle = payload.notification.title;
  const notificationOptions = {
    body: payload.notification.body,
    icon: '/static/images/pwa-icon-192.png', 
    badge: '/static/images/pwa-icon-192.png', 
    tag: 'thankjapan-news', 
    data: {
        url: payload.data.url || '/' 
    }
  };
  self.registration.showNotification(notificationTitle, notificationOptions);
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  event.waitUntil(
    clients.openWindow(event.notification.data.url)
  );
});