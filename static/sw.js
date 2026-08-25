// 智能行程规划 - 离线外壳缓存(网络优先, 缓存兜底, 接口永不缓存)
const CACHE = 'route-app-v1';

self.addEventListener('install', () => {
  self.skipWaiting();
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;   // 高德/瓦片等外部资源不缓存
  if (url.pathname.startsWith('/api/')) return; // 接口不缓存, 保证数据最新

  e.respondWith(
    caches.open(CACHE).then(async (cache) => {
      try {
        const fresh = await fetch(req);
        if (fresh.ok) cache.put(req, fresh.clone());
        return fresh;
      } catch (err) {
        const cached = await cache.match(req, { ignoreSearch: true });
        return cached || Response.error();
      }
    })
  );
});
