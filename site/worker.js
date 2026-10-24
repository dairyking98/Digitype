// Digitype static site: Python (Pyodide) with the digitype package, and the
// project kept in the browser (IndexedDB) or in a folder picked on disk.
const PYODIDE = "https://cdn.jsdelivr.net/pyodide/v0.27.7/full/";
importScripts(PYODIDE + "pyodide.js");

let py, api, sync = async () => {}, timer = null;
let queue = Promise.resolve();
const say = (m) => postMessage(m);

// one request at a time: Python is single-threaded, and a request must not
// start while another is part-way through
const serial = (fn) => (queue = queue.then(fn, fn));

function saveSoon() {
  clearTimeout(timer);
  timer = setTimeout(() => serial(() => sync().catch((e) => say({ kind: "status", text: `Saving failed: ${e}` }))), 400);
}

async function boot({ dir }) {
  say({ kind: "status", text: "Loading Python…" });
  py = await loadPyodide({ indexURL: PYODIDE });
  say({ kind: "status", text: "Loading numpy, scipy, scikit-image, shapely, fontTools, Pillow, matplotlib…" });
  await py.loadPackage(["numpy", "scipy", "scikit-image", "shapely", "fonttools", "pillow", "matplotlib"]);
  say({ kind: "status", text: "Loading Digitype…" });
  const zip = await (await fetch("digitype.zip", { cache: "no-cache" })).arrayBuffer();
  py.unpackArchive(zip, "zip", { extractDir: "/lib/digitype" });

  say({ kind: "status", text: "Opening the project…" });
  let root;
  if (dir) {                       // a real folder on disk (Chrome/Edge)
    const fs = await py.mountNativeFS("/folder", dir);
    sync = () => fs.syncfs();
    root = "/folder";
  } else {                         // browser storage
    py.FS.mkdirTree("/projects");
    py.FS.mount(py.FS.filesystems.IDBFS, {}, "/projects");
    const idb = (populate) => new Promise((ok, no) => py.FS.syncfs(populate, (e) => (e ? no(e) : ok())));
    await idb(true);
    sync = () => idb(false);
    root = "/projects/default";
  }
  py.runPython(`
import sys
sys.path.insert(0, "/lib/digitype")
from digitype.webapi import Api
`);
  api = py.globals.get("Api")(root);
  await sync();
  say({ kind: "ready", where: dir ? dir.name : "this browser" });
}

async function request({ id, method, path, query, body }) {
  try {
    const r = api.handle(method, path, query, body ? new Uint8Array(body) : null);
    const [status, type, out, filename] = r.toJs();
    r.destroy();
    const buf = out.buffer.slice(out.byteOffset, out.byteOffset + out.byteLength);
    if (path === "/api/import") await sync();      // the page reloads straight after
    say({ kind: "res", id, status, type, body: buf, filename });
  } catch (e) {
    say({ kind: "res", id, status: 500, type: "application/json",
      body: new TextEncoder().encode(JSON.stringify({ detail: String(e.message || e).split("\n").slice(-3).join(" ") })).buffer });
  }
  saveSoon();
}

function regenerate({ chars }) {
  let result = null, error = null;
  const log = (line) => say({ kind: "log", line: String(line) });
  try {
    const r = api.regenerate(chars ?? null, log);
    result = JSON.parse(py.globals.get("__import__")("json").dumps(r));
    r?.destroy?.();
  } catch (e) {
    error = String(e.message || e).split("\n").slice(-2).join(" ");
    log(String(e.message || e));
  }
  say({ kind: "regen-done", result, error });
  saveSoon();
}

onmessage = (e) => {
  const m = e.data;
  if (m.kind === "boot") serial(() => boot(m).catch((err) => say({ kind: "fail", text: String(err.message || err) })));
  else if (m.kind === "req") serial(() => request(m));
  else if (m.kind === "regen") serial(() => regenerate(m));
};
