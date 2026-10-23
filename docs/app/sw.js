// Digitype static site: every api/ request (fetches, <img src>, fonts,
// downloads) goes to the page that made it, which answers it with Python
// running in a worker. Nothing else is touched.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

const API = new URL("api/", self.registration.scope).pathname;

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (url.origin !== location.origin || !url.pathname.startsWith(API) || !e.clientId) return;
  e.respondWith(forward(e, url));
});

async function forward(e, url) {
  const client = await self.clients.get(e.clientId);
  if (!client) return new Response('{"detail":"no page"}', { status: 503 });
  const body = ["GET", "HEAD"].includes(e.request.method) ? null : await e.request.arrayBuffer();
  const ch = new MessageChannel();
  const reply = new Promise((resolve) => { ch.port1.onmessage = (m) => resolve(m.data); });
  client.postMessage({ digitype: true, method: e.request.method, path: "/api/" + url.pathname.slice(API.length),
    query: url.search.slice(1), body }, [ch.port2]);
  const r = await reply;
  const headers = { "Content-Type": r.type, "Cache-Control": "no-store" };
  if (r.filename) headers["Content-Disposition"] = `attachment; filename="${r.filename}"`;
  return new Response(r.body, { status: r.status, headers });
}
