// SUNNY REMOVER — Service Worker
// 코드(HTML/JS/manifest)는 network-first → 업데이트가 바로 반영.
// 무거운 자산(모델 .onnx · onnxruntime · 폰트 · 아이콘)은 cache-first → 두 번째부터 즉시·오프라인.
// ★ CACHE 상수를 올릴 때마다(예 v3→v4) 새 버전으로 인식 → 페이지가 "새 버전 있음" 배너 표시.
const CACHE = 'sunny-remover-v6';
const SHELL = ['./', './index.html', './manifest.webmanifest'];

// 설치 시 자동 활성화(skipWaiting)하지 않는다 — 사용자가 [업데이트]를 누를 때까지 대기.
// (작업 중인데 강제로 새로고침되는 사고 방지)
self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE)
      .then(c => Promise.allSettled(SHELL.map(u => c.add(u))))
  );
});

// 페이지가 [업데이트]를 누르면 보내는 메시지 → 대기 중 새 SW를 즉시 활성화.
self.addEventListener('message', e => {
  if (e.data && e.data.type === 'SKIP_WAITING') self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

function isHeavyAsset(url){
  // 큰 모델·CDN 라이브러리·폰트만 cache-first. 아이콘(작은 png)은 network-first로 항상 최신.
  return url.includes('.onnx')
      || url.includes('onnxruntime-web')
      || url.includes('jsdelivr')          // ort + pretendard 폰트 CDN
      || /\.(woff2?|ttf)(\?|$)/i.test(url);
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
