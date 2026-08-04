
importScripts('https://www.gstatic.com/firebasejs/9.23.0/firebase-app-compat.js');
importScripts('https://www.gstatic.com/firebasejs/9.23.0/firebase-messaging-compat.js');


const firebaseConfig = {
  apiKey: "AIzaSyDid3dPSXpNdyfDbDJ_b2vLEkRY5jUX9-E",
  authDomain: "thankjapan-v2.firebaseapp.com",
  projectId: "thankjapan-v2",
  storageBucket: "thankjapan-v2.firebasestorage.app",
  messagingSenderId: "643438917626",
  appId: "1:643438917626:web:07e51df5b4feeb7c98d643",
  measurementId: "G-ZF96G7SN85"
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
  console.log('[sw.js] Background message received: ', payload);

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