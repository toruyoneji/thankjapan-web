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

const CACHE_NAME = 'thankjapan-v2';
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