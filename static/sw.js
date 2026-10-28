// Service worker только ради push: офлайн-кэша здесь намеренно нет —
// страница живёт свежими данными из базы, закэшированная оболочка врала бы.

self.addEventListener('install', () => self.skipWaiting());

self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));

self.addEventListener('push', (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (err) {
    data = {};
  }

  const title = data.title || 'Новая капча';
  const options = {
    body: data.body || 'Появились картинки без ответа',
    icon: '/static/icons/icon-192.png',
    badge: '/static/icons/badge-96.png',
    // один тег — новые уведомления заменяют предыдущее, а не копятся пачкой
    tag: 'new-images',
    renotify: true,
    data: { url: '/' },
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || '/';

  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clients) => {
      // если вкладка уже открыта — поднимаем её, а не плодим новую
      for (const client of clients) {
        if (new URL(client.url).pathname === url && 'focus' in client) return client.focus();
      }
      return self.clients.openWindow(url);
    }),
  );
});
