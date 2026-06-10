// SUNNY REMOVER — Service Worker
// 코드(HTML/JS/manifest)는 network-first → 업데이트가 바로 반영.
// 무거운 자산(모델 .onnx · onnxruntime · 폰트 · 아이콘)은 cache-first → 두 번째부터 즉시·오프라인.
const CACHE = 'sunny-remover-v2';
const SHELL = ['./', './index.html', './manifest.webmanifest'];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE)
      .then(c => Promise.allSettled(SHELL.map(u => c.add(u))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

function isHeavyAsset(url){
  return url.includes('.onnx')
      || url.includes('onnxruntime-web')
      || url.includes('jsdelivr')          // ort + pretendard 폰트 CDN
      || /\.(png|jpg|jpeg|webp|woff2?|ttf)(\?|$)/i.test(url);
}

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = req.url;

  if (isHeavyAsset(url)) {
    // cache-first
    e.respondWith(
      caches.match(req).then(cached => cached || fetch(req).then(resp => {
        if (resp && (resp.ok || resp.type === 'opaque')) {
          const clone = resp.clone();
          caches.open(CACHE).then(c => c.put(req, clone)).catch(()=>{});
        }
        return resp;
      }))
    );
  } else {
    // network-first (HTML·코드 — 항상 최신, 오프라인이면 캐시)
    e.respondWith(
      fetch(req).then(resp => {
        if (resp && resp.ok) {
          const clone = resp.clone();
          caches.open(CACHE).then(c => c.put(req, clone)).catch(()=>{});
        }
        return resp;
      }).catch(() => caches.match(req))
    );
  }
});
