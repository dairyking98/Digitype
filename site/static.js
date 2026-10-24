// Digitype static site: boots Python in a worker, then answers the page's
// api/ requests (handed over by the service worker) with it, and only then
// loads the labelling tool itself (app.js), unchanged but for relative URLs.
const PDFJS = "https://cdn.jsdelivr.net/npm/pdfjs-dist@4.10.38/build/";
const PDF_DPI = 600;
const $ = (id) => document.getElementById(id);
const status = (t) => { $("boot-status").textContent = t; };

let worker, ready = false, nextId = 1;
const waiting = new Map();
const job = { running: false, log: [], result: null, error: null };

function ask(method, path, query = "", body = null) {
  const id = nextId++;
  return new Promise((resolve) => {
    waiting.set(id, resolve);
    worker.postMessage({ kind: "req", id, method, path, query, body }, body ? [body] : []);
  });
}
const json = (status, obj) => ({ status, type: "application/json", body: new TextEncoder().encode(JSON.stringify(obj)).buffer });

// what the service worker hands over: Regenerate's job state is kept here,
// so its log can be polled while Python is busy with it
async function answer(m) {
  const { method, path, query } = m;
  if (path === "/api/regenerate") {
    if (method === "POST") {
      if (job.running) return json(409, { detail: "already running" });
      const chars = m.body ? JSON.parse(new TextDecoder().decode(m.body) || "{}").chars : null;
      Object.assign(job, { running: true, log: [], result: null, error: null });
      worker.postMessage({ kind: "regen", chars: chars ?? null });
      return json(200, { started: true });
    }
    const since = Number(new URLSearchParams(query).get("since") || 0);
    return json(200, { running: job.running, log: job.log.slice(since), n: job.log.length,
      result: job.running ? null : job.result, error: job.error });
  }
  const name = new URLSearchParams(query).get("name") || "";
  if (path === "/api/upload" && /\.pdf$/i.test(name)) return uploadPdf(name, m.body);
  return ask(method, path, query, m.body);
}

// PDFs: each page drawn at 600 dpi here (PDF.js) and added as a grey PNG,
// since PyMuPDF doesn't run in the browser
async function uploadPdf(name, data) {
  const pdfjs = await import(PDFJS + "pdf.min.mjs");
  pdfjs.GlobalWorkerOptions.workerSrc = PDFJS + "pdf.worker.min.mjs";
  const doc = await pdfjs.getDocument({ data }).promise;
  const stem = name.replace(/\.pdf$/i, "");
  let last = null, added = 0;
  for (let i = 1; i <= doc.numPages; i++) {
    const page = await doc.getPage(i);
    const vp = page.getViewport({ scale: PDF_DPI / 72 });
    const cv = new OffscreenCanvas(Math.round(vp.width), Math.round(vp.height));
    const cx = cv.getContext("2d");
    cx.fillStyle = "#fff"; cx.fillRect(0, 0, cv.width, cv.height);
    await page.render({ canvasContext: cx, viewport: vp }).promise;
    const png = await (await cv.convertToBlob({ type: "image/png" })).arrayBuffer();
    const q = new URLSearchParams({ name: `${stem} p${i}.png`, dpi: PDF_DPI }).toString();
    last = await ask("POST", "/api/upload", q, png);
    if (last.status !== 200) return last;
    added += JSON.parse(new TextDecoder().decode(last.body)).added;
  }
  const j = JSON.parse(new TextDecoder().decode(last.body));
  return json(200, { ...j, added });
}

navigator.serviceWorker?.addEventListener("message", async (e) => {
  if (!e.data?.digitype) return;
  let r;
  try { r = ready ? await answer(e.data) : json(503, { detail: "Digitype is still loading in this tab" }); }
  catch (err) { r = json(500, { detail: String(err.message || err) }); }
  e.ports[0].postMessage(r, r.body ? [r.body] : []);
});

function startWorker(dir) {
  $("boot-pick").hidden = true;
  worker = new Worker("worker.js");
  worker.onmessage = (e) => {
    const m = e.data;
    if (m.kind === "status") status(m.text);
    else if (m.kind === "fail") status(`Couldn't start: ${m.text}`);
    else if (m.kind === "res") { waiting.get(m.id)?.(m); waiting.delete(m.id); }
    else if (m.kind === "log") job.log.push(m.line);
    else if (m.kind === "regen-done") Object.assign(job, { running: false, result: m.result, error: m.error });
    else if (m.kind === "ready") {
      ready = true;
      $("boot").hidden = true;
      $("static-where").textContent = m.where;
      $("static-open").onclick = (ev) => { ev.preventDefault(); $("static-zip").click(); };
      $("static-zip").onchange = async (ev) => {
        const f = ev.target.files[0];
        if (!f) return;
        $("upload-status").textContent = `Opening ${f.name}…`;
        const r = await ask("POST", "/api/import", "", await f.arrayBuffer());
        if (r.status === 200) location.reload();
        else $("upload-status").textContent = JSON.parse(new TextDecoder().decode(r.body)).detail;
      };
      const s = document.createElement("script");
      s.type = "module"; s.src = "app.js";
      document.body.append(s);
    }
  };
  worker.postMessage({ kind: "boot", dir });
}

async function main() {
  // service workers need https (or localhost)
  if (location.protocol === "http:" && !/^(localhost|127\.0\.0\.1|\[::1\])$/.test(location.hostname))
    return location.replace(location.href.replace(/^http:/, "https:"));
  if (!("serviceWorker" in navigator)) return status("This browser has no service workers - the static site can't run here.");
  await navigator.serviceWorker.register("sw.js");
  await navigator.serviceWorker.ready;
  if (!navigator.serviceWorker.controller) return location.reload();   // first visit: let the worker take this page
  navigator.storage?.persist?.();
  status("Where should the project live?");
  $("boot-pick").hidden = false;
  $("boot-browser").onclick = () => startWorker(null);
  if (window.showDirectoryPicker) {
    $("boot-folder").onclick = async () => {
      try { startWorker(await showDirectoryPicker({ id: "digitype", mode: "readwrite" })); }
      catch { /* cancelled */ }
    };
  } else {
    $("boot-folder").disabled = true;
    $("boot-folder").title = "Needs Chrome or Edge";
  }
}
main().catch((e) => status(String(e)));
