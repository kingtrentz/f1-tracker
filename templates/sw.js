/* F1 Tracker service worker: makes the site installable, shows push alerts.
   Network first: pages and data.json always come fresh when there's signal; the last copy is only a fallback offline.
   Ghost laps, history and everything else aren't touched (straight to the network). */
const CACHE = "f1-shell-v2";
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil((async () => {
  for (const k of await caches.keys()) if (k !== CACHE) await caches.delete(k);
  await self.clients.claim();
})()));
self.addEventListener("fetch", e => {
  const r = e.request, u = new URL(r.url);
  if (r.method !== "GET" || u.origin !== location.origin) return;
  const page = r.mode === "navigate", data = u.pathname.endsWith("/data.json");
  if (!page && !data) return;
  const key = u.origin + u.pathname;
  e.respondWith((async () => {
    try {
      // GitHub Pages says max-age=600; "no-cache" makes the phone ask GitHub each time (cheap 304 when nothing changed)
      const res = await fetch(page ? u.href : r.url, {cache: "no-cache", credentials: "same-origin"});
      if (res.ok) { const c = res.clone(); caches.open(CACHE).then(cc => cc.put(key, c)).catch(() => {}); }
      return res;
    } catch (err) {
      const hit = await caches.match(key) || (page && await caches.match(new URL("index.html", self.registration.scope).href));
      if (hit) return hit;
      throw err;
    }
  })());
});
self.addEventListener("push", e => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (_) { d = {title: "F1 Tracker", body: e.data ? e.data.text() : ""}; }
  e.waitUntil(self.registration.showNotification(d.title || "F1 Tracker", {
    body: d.body || "", tag: d.tag || undefined, icon: "icon-192.png", badge: "icon-192.png",
    data: {url: new URL(d.url || "index.html", self.registration.scope).href}
  }));
});
self.addEventListener("notificationclick", e => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || self.registration.scope;
  e.waitUntil((async () => {
    const same = url.startsWith(self.registration.scope);
    if (same) for (const c of await self.clients.matchAll({type: "window", includeUncontrolled: true})) {
      try { await c.navigate(url); return c.focus(); } catch (_) {}
    }
    return self.clients.openWindow(url);
  })());
});
