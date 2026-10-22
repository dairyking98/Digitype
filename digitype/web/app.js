// Digitype labelling tool: pages on a zoomable canvas, boxes dragged
// around glyphs and labelled, then Regenerate turns them into a font.
const $ = (id) => document.getElementById(id);
const api = async (url, opts = {}) => {
  const r = await fetch(url, { headers: { "Content-Type": "application/json" }, ...opts,
    body: opts.body && typeof opts.body !== "string" ? JSON.stringify(opts.body) : opts.body });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
};

const S = { rotPreview: 0, guides: false, mouseY: null, pages: [], boxes: [], allBoxes: [], sets: [], activeSet: 1, settings: {}, page: null, img: null, imgScale: 1, hi: null,
  zoom: 1, panX: 0, panY: 0, sel: null, drag: null, draft: null, space: false, editing: null, jump: {} };
const cv = $("cv"), ctx = cv.getContext("2d");
const dpr = () => window.devicePixelRatio || 1;

// ------------------------------------------------------------------ data
function applyState(p) {
  Object.assign(S, { pages: p.pages, settings: p.settings, sets: p.sets, activeSet: p.active_set, images: p.images || [] });
  setBoxes(p.boxes);
  $("root").textContent = p.root;
  showSettings(p.estimated_pitch_mm);
  renderSets(); renderPages(); renderGlyphs(); draw();
  if (!$("view-images").hidden) renderImages();
}
// S.boxes: the font set being labelled; S.allBoxes: every set's (others drawn dimmed)
function setBoxes(all) {
  S.allBoxes = all;
  S.boxes = all.filter((b) => (b.set ?? 1) === S.activeSet);
  if (S.sel && !S.boxes.some((b) => b.id === S.sel)) { S.sel = null; showSel(); }
}

async function load() {
  const p = await api("/api/project");
  applyState(p);
  const last = Number(localStorage.getItem("ts.page"));
  if (S.pages.length) openPage(S.pages.find((x) => x.id === last) ? last : S.pages[0].id);
}

function showSettings(est) {
  const s = S.settings;
  $("set-pitch").value = s.pitch_mm; $("set-name").value = s.font_name; $("set-up").value = s.upsample;
  $("set-weight").value = s.weight; $("set-add").value = s.add;
  $("set-el").value = s.expand_left; $("set-er").value = s.expand_right;
  $("set-place").value = s.placement || "typed";
  $("set-soft").checked = s.soft_edges !== false; $("set-smooth").value = s.smooth ?? 1;
  $("set-bmode").value = s.baseline_mode || "adaptive"; $("set-tilt").checked = s.tilt_correction !== false;
  const pe = $("pitch-est");
  pe.innerHTML = "";
  if (est) {
    pe.append(`From your text runs: about ${est.toFixed(3)} mm (${(25.4 / est).toFixed(1)} per inch) `);
    if (Math.abs(est - s.pitch_mm) / s.pitch_mm > 0.03) {
      const b = document.createElement("button");
      b.className = "small"; b.textContent = `Use ${est.toFixed(2)}`;
      b.title = "Set this font's pitch to the measured value";
      b.onclick = async () => {
        S.settings = await api("/api/settings", { method: "POST", body: { pitch_mm: Number(est.toFixed(3)) } });
        $("set-pitch").value = S.settings.pitch_mm;
        afterChange();
        showSettings(est);
      };
      pe.append(b);
    }
  }
}
$("set-bmode").addEventListener("change", async (e) => { S.settings = await api("/api/settings", { method: "POST", body: { baseline_mode: e.target.value } }); afterChange(); });
$("set-soft").addEventListener("change", async (e) => { S.settings = await api("/api/settings", { method: "POST", body: { soft_edges: e.target.checked } }); });
$("set-place").addEventListener("change", async (e) => {
  S.settings = await api("/api/settings", { method: "POST", body: { placement: e.target.value } });
  $("log").textContent = "Placement changed - press Regenerate to apply it to the fonts.";
});
$("set-tilt").addEventListener("change", async (e) => { S.settings = await api("/api/settings", { method: "POST", body: { tilt_correction: e.target.checked } }); });
for (const [id, key] of [["set-smooth", "smooth"], ["set-el", "expand_left"], ["set-er", "expand_right"], ["set-pitch", "pitch_mm"], ["set-name", "font_name"], ["set-up", "upsample"], ["set-weight", "weight"], ["set-add", "add"]]) {
  $(id).addEventListener("change", async (e) => { S.settings = await api("/api/settings", { method: "POST", body: { [key]: e.target.value } }); });
}

function renderSets() {
  const sel = $("set-pick");
  sel.innerHTML = "";
  for (const st of S.sets) {
    const n = S.allBoxes.filter((b) => (b.set ?? 1) === st.id).length;
    const o = document.createElement("option");
    o.value = st.id; o.textContent = `${st.name} (${n} box${n === 1 ? "" : "es"})`;
    o.selected = st.id === S.activeSet;
    sel.appendChild(o);
  }
  $("set-del").disabled = S.sets.length < 2 || S.boxes.length > 0;
}
$("set-pick").onchange = async (e) => {
  applyState(await api(`/api/sets/${e.target.value}`, { method: "POST", body: { activate: true } }));
  $("result-grid").innerHTML = ""; $("result-head").innerHTML = "";
};
let nameMode = null;
function askSetName(mode) {
  nameMode = mode;
  const inp = $("set-name-input");
  inp.hidden = false;
  inp.value = mode === "rename" ? (S.sets.find((s) => s.id === S.activeSet)?.name || "") : "";
  inp.placeholder = mode === "rename" ? "New name, Enter" : "Name of the new font, Enter";
  inp.focus(); inp.select();
}
$("set-new").onclick = () => askSetName("new");
$("set-rename").onclick = () => askSetName("rename");
$("set-name-input").addEventListener("keydown", async (e) => {
  e.stopPropagation();
  if (e.key === "Escape") { e.target.hidden = true; return; }
  if (e.key !== "Enter") return;
  const name = e.target.value.trim();
  e.target.hidden = true;
  if (nameMode === "new") applyState(await api("/api/sets", { method: "POST", body: { name } }));
  else if (name) applyState(await api(`/api/sets/${S.activeSet}`, { method: "POST", body: { name } }));
  $("result-grid").innerHTML = ""; $("result-head").innerHTML = "";
  $("log").textContent = nameMode === "new" ? `New font set "${name || "untitled"}" - the boxes you draw now belong to it. Set its pitch below.` : "";
});
$("set-del").onclick = async () => {
  try { applyState(await api(`/api/sets/${S.activeSet}`, { method: "DELETE" })); }
  catch (e) { $("log").textContent = e.message; }
};

function renderPages() {
  const el = $("pages");
  el.innerHTML = "";
  for (const p of S.pages) {
    const n = S.boxes.filter((b) => b.page === p.id).length;
    const d = document.createElement("div");
    d.className = "page-item" + (S.page && S.page.id === p.id ? " on" : "");
    d.innerHTML = `<span></span><span class="n">${n || ""}</span>`;
    d.firstChild.textContent = p.name;
    d.title = `${p.file}\n${p.w} × ${p.h} px` + (p.dpi ? ` · ${p.dpi} dpi` : " · dpi unknown (600 assumed)");
    d.onclick = () => openPage(p.id);
    el.appendChild(d);
  }
  if (!S.pages.length) el.innerHTML = '<p class="muted">No pages yet - add a file or folder below.</p>';
  showPageTools();
}

function renderGlyphs() {
  const count = {};
  for (const b of S.boxes) for (const c of b.cells || []) count[c.char] = (count[c.char] || 0) + 1;
  const keys = Object.keys(count).sort();
  $("glyph-count").textContent = keys.length ? `${keys.length} · ${Object.values(count).reduce((a, b) => a + b, 0)} samples` : "";
  const el = $("glyphs");
  el.innerHTML = "";
  for (const k of keys) {
    const d = document.createElement("div");
    d.className = "gchip";
    d.innerHTML = `<b></b><small>${count[k]}</small>`;
    d.firstChild.textContent = k;
    d.title = `${count[k]} sample${count[k] === 1 ? "" : "s"} of “${k}” - click to visit them in turn`;
    d.onclick = () => jumpTo(k);
    el.appendChild(d);
  }
  if (!keys.length) el.innerHTML = '<p class="muted">None yet.</p>';
}

function jumpToBox(id) {
  const b = S.boxes.find((x) => x.id === id);
  if (!b) return;
  const go = () => {
    S.sel = b.id;
    const r = cv.getBoundingClientRect();
    S.zoom = Math.min(r.width / (b.w * 1.6), r.height / (b.h * 4), 6);
    S.panX = r.width / 2 - (b.x + b.w / 2) * S.zoom;
    S.panY = r.height / 2 - (b.y + b.h / 2) * S.zoom;
    viewChanged(); showSel();
  };
  if (!S.page || S.page.id !== b.page) openPage(b.page).then(go); else go();
}

function jumpTo(ch) {
  const list = S.boxes.filter((b) => (b.cells || []).some((c) => c.char === ch));
  if (!list.length) return;
  S.jump[ch] = ((S.jump[ch] ?? -1) + 1) % list.length;
  const b = list[S.jump[ch]];
  const go = () => {
    S.sel = b.id;
    const r = cv.getBoundingClientRect();
    S.zoom = Math.min(r.width / (b.w * 3), r.height / (b.h * 4), 4);
    S.panX = r.width / 2 - (b.x + b.w / 2) * S.zoom;
    S.panY = r.height / 2 - (b.y + b.h / 2) * S.zoom;
    viewChanged(); showSel();
  };
  if (!S.page || S.page.id !== b.page) openPage(b.page).then(go); else go();
}

// ------------------------------------------------------------------ page
async function openPage(id) {
  S.page = S.pages.find((p) => p.id === id);
  localStorage.setItem("ts.page", id);
  S.hi = null; S.sel = null; showSel();
  renderPages();
  $("log").textContent = `Loading ${S.page.name}… (a big scan takes a moment the first time)`;
  const img = new Image();
  img.src = `/api/page/${id}/preview?o=${S.page.quarter || 0}_${S.page.rotation || 0}`;   // a new address per orientation
  await img.decode().catch(() => {});
  if (S.page.id !== id) return;
  S.img = img;
  S.imgScale = img.naturalWidth / S.page.w;
  $("log").textContent = "";
  showPageTools();
  fit();
}

// ------------------------------------------------------------------ this page: dpi, rotation, guides
function showPageTools() {
  const p = S.page;
  $("page-tools").hidden = !p;
  if (!p) return;
  $("pg-info").textContent = `${p.w} × ${p.h} px` + (p.quarter ? ` · turned ${p.quarter * 90}°` : "");
  $("pg-dpi").value = p.dpi || "";
  const r = p.rotation || 0;
  $("pg-rot").value = Math.max(-10, Math.min(10, r)); $("pg-rot-n").value = r;
}
async function reloadPage(refit) {
  const id = S.page.id;
  const img = new Image();
  img.src = `/api/page/${id}/preview?v=${Date.now()}`;
  await img.decode().catch(() => {});
  if (S.page.id !== id) return null;
  return img;
}
let pageBusy = null, pageNext = null;
async function setPage(body, refit) {
  // one change at a time; a newer one waiting replaces any older one
  if (pageBusy) { pageNext = { body, refit }; return; }
  pageBusy = body;
  const turning = "rotation" in body || "quarter" in body;
  if (turning) $("log").textContent = "Turning the page…";
  try {
    const r = await api(`/api/page/${S.page.id}`, { method: "POST", body });
    const img = turning ? await reloadPage(refit) : null;
    // swap everything at once: until the new image is here the page stays
    // turned on screen (the live preview), so it never snaps back
    Object.assign(S.page, r.page);
    const i = S.pages.findIndex((p) => p.id === S.page.id); if (i >= 0) S.pages[i] = S.page;
    setBoxes(r.boxes);
    if (img) { S.img = img; S.imgScale = img.naturalWidth / S.page.w; S.hi = null; S.rotPreview = 0; }
    renderGlyphs(); renderSets(); showPageTools();
    if (turning) { if (refit) fit(); else viewChanged(); }
    $("log").textContent = turning ? `Page turned to ${S.page.rotation || 0}°${S.page.quarter ? ` + ${S.page.quarter * 90}°` : ""} - saved.` : "";
  } catch (e) { $("log").textContent = `Couldn't change the page: ${e.message}`; S.rotPreview = 0; showPageTools(); draw(); }
  pageBusy = null;
  if (pageNext) { const n = pageNext; pageNext = null; setPage(n.body, n.refit); }
}
$("pg-dpi").addEventListener("change", (e) => setPage({ dpi: e.target.value ? Number(e.target.value) : null }));
$("pg-rot").addEventListener("input", (e) => {          // live: turn what's on screen, guides on
  if (pageBusy) return;
  S.rotPreview = Number(e.target.value) - (S.page.rotation || 0);
  $("pg-rot-n").value = e.target.value;
  draw();
});
$("pg-rot").addEventListener("change", (e) => setPage({ rotation: Number(e.target.value) }));
$("pg-rot-n").addEventListener("change", (e) => setPage({ rotation: Number(e.target.value) || 0 }));
$("pg-rot0").onclick = () => setPage({ rotation: 0 });
$("pg-ql").onclick = () => setPage({ quarter: ((S.page.quarter || 0) + 3) % 4 }, true);
$("pg-qr").onclick = () => setPage({ quarter: ((S.page.quarter || 0) + 1) % 4 }, true);
$("pg-guides").addEventListener("change", (e) => { S.guides = e.target.checked; draw(); });

function fit() {
  const r = cv.getBoundingClientRect();
  S.zoom = Math.min(r.width / S.page.w, r.height / S.page.h) * 0.96;
  S.panX = (r.width - S.page.w * S.zoom) / 2;
  S.panY = (r.height - S.page.h * S.zoom) / 2;
  viewChanged();
}

const orientKey = (p) => `${(p && p.quarter) || 0}_${(p && p.rotation) || 0}`;
let hiTimer = null;
function viewChanged() {
  draw();
  clearTimeout(hiTimer);
  hiTimer = setTimeout(loadHi, 220);
}

async function loadHi() {
  if (!S.page || S.zoom <= S.imgScale * 1.05) { S.hi = null; return draw(); }
  const r = cv.getBoundingClientRect();
  let x = -S.panX / S.zoom, y = -S.panY / S.zoom, w = r.width / S.zoom, h = r.height / S.zoom;
  x = Math.max(0, x); y = Math.max(0, y);
  w = Math.min(S.page.w - x, w); h = Math.min(S.page.h - y, h);
  if (w <= 0 || h <= 0) return;
  const out = Math.round(Math.min(4000, w * S.zoom * dpr()));
  const pid = S.page.id, key = [x, y, w, h].map(Math.round).join(","), orient = orientKey(S.page);
  const img = new Image();
  img.src = `/api/page/${pid}/region?x=${x}&y=${y}&w=${w}&h=${h}&out=${out}&o=${orient}`;
  await img.decode().catch(() => {});
  // a tile asked for before the page was turned must not land on the turned page
  if (S.page.id !== pid || orientKey(S.page) !== orient) return;
  S.hi = { img, x, y, w, h, key, orient };
  draw();
}

// ------------------------------------------------------------------ drawing
function resize() {
  const r = cv.getBoundingClientRect();
  const w = Math.round(r.width * dpr()), h = Math.round(r.height * dpr());
  if (!w || !h) return;
  const first = !cv.width || cv.width === 300;   // the canvas default, before any real size
  if (cv.width !== w || cv.height !== h) { cv.width = w; cv.height = h; }
  if (first && S.page && S.img) fit(); else draw();
}
new ResizeObserver(() => resize()).observe($("stage"));

function pageBoxes() { return S.page ? S.boxes.filter((b) => b.page === S.page.id) : []; }

function draw() {
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, cv.width, cv.height);
  if (!S.page || !S.img) return;
  const d = dpr(), z = S.zoom;
  ctx.setTransform(d * z, 0, 0, d * z, d * S.panX, d * S.panY);
  if (S.rotPreview) {                            // dragging the rotation: turn the page about its centre
    ctx.translate(S.page.w / 2, S.page.h / 2);
    ctx.rotate(S.rotPreview * Math.PI / 180);
    ctx.translate(-S.page.w / 2, -S.page.h / 2);
  }
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(S.img, 0, 0, S.page.w, S.page.h);
  if (S.hi && S.hi.orient === orientKey(S.page)) ctx.drawImage(S.hi.img, S.hi.x, S.hi.y, S.hi.w, S.hi.h);
  const lw = 1.5 / z;
  ctx.font = `${13 / z}px ui-monospace, monospace`;
  ctx.setLineDash([4 / z, 4 / z]);
  for (const b of S.allBoxes) {                  // other font sets: shown, not editable
    if (b.page !== S.page.id || (b.set ?? 1) === S.activeSet) continue;
    ctx.strokeStyle = "rgba(160,160,160,.55)"; ctx.lineWidth = lw;
    ctx.strokeRect(b.x, b.y, b.w, b.h);
  }
  ctx.setLineDash([]);
  for (const b of pageBoxes()) {
    const on = b.id === S.sel;
    const warn = (b.cells || []).find((c) => c.warn);
    ctx.strokeStyle = warn ? "#ff6b6b" : on ? "#6c9cff" : "#ffb84d";
    ctx.lineWidth = on ? lw * 1.6 : lw;
    ctx.strokeRect(b.x, b.y, b.w, b.h);
    ctx.fillStyle = on ? "#6c9cff" : "#ffb84d";
    ctx.fillText(b.text || "?", b.x, b.y - 4 / z);
    if (on || z > S.imgScale * 2) {
      for (const c of b.cells || []) {
        ctx.strokeStyle = "rgba(108,156,255,.5)"; ctx.lineWidth = lw * 0.6;
        if ((b.cells || []).length > 1) {
          ctx.strokeRect(c.x, b.y, c.w, b.h);
          ctx.fillStyle = "rgba(108,156,255,.95)";
          ctx.font = `${Math.min(c.w * 0.7, 14 / z)}px ui-monospace, monospace`;
          ctx.fillText(c.char, c.x + c.w * 0.15, b.y + b.h + Math.min(c.w * 0.7, 14 / z));
          ctx.font = `${13 / z}px ui-monospace, monospace`;
        }
        if (c.baseline != null) {               // drawn at its tilt
          const dy = Math.tan(c.tilt || 0) * c.w / 2;
          ctx.strokeStyle = c.borrowed ? "rgba(255,107,107,.55)" : "#ff6b6b"; ctx.lineWidth = lw;
          ctx.beginPath(); ctx.moveTo(c.x, c.baseline - dy); ctx.lineTo(c.x + c.w, c.baseline + dy); ctx.stroke();
        }
      }
    }
    if (on) {
      ctx.fillStyle = "#6c9cff";
      const s = 8 / z;
      for (const h of handles(b)) ctx.fillRect(h.x - s / 2, h.y - s / 2, s, s);
    }
  }
  if (S.draft) {
    ctx.strokeStyle = "#6c9cff"; ctx.lineWidth = lw; ctx.setLineDash([6 / z, 4 / z]);
    ctx.strokeRect(S.draft.x, S.draft.y, S.draft.w, S.draft.h); ctx.setLineDash([]);
  }
  if (S.guides || S.rotPreview) drawGuides();
}

// guide rule: level lines fixed to the screen, to line the text up against
// while straightening a page; the one under the pointer is stronger
function drawGuides() {
  const d = dpr();
  ctx.setTransform(d, 0, 0, d, 0, 0);
  const r = cv.getBoundingClientRect(), step = 32;
  ctx.lineWidth = 1;
  ctx.strokeStyle = "rgba(0, 170, 255, .35)";
  ctx.beginPath();
  for (let y = step / 2; y < r.height; y += step) { ctx.moveTo(0, Math.round(y) + 0.5); ctx.lineTo(r.width, Math.round(y) + 0.5); }
  ctx.stroke();
  ctx.strokeStyle = "rgba(0, 170, 255, .15)";
  ctx.beginPath();
  for (let x = step / 2; x < r.width; x += step * 4) { ctx.moveTo(Math.round(x) + 0.5, 0); ctx.lineTo(Math.round(x) + 0.5, r.height); }
  ctx.stroke();
  if (S.mouseY != null) {
    ctx.strokeStyle = "rgba(255, 60, 60, .8)";
    ctx.beginPath(); ctx.moveTo(0, S.mouseY + 0.5); ctx.lineTo(r.width, S.mouseY + 0.5); ctx.stroke();
  }
}
// resize handles of the selected box: corners and edge middles; l/r/t/b
// say which sides each one moves
const handles = (b) => [
  { x: b.x, y: b.y, l: 1, t: 1, cur: "nwse-resize" }, { x: b.x + b.w / 2, y: b.y, t: 1, cur: "ns-resize" },
  { x: b.x + b.w, y: b.y, r: 1, t: 1, cur: "nesw-resize" }, { x: b.x + b.w, y: b.y + b.h / 2, r: 1, cur: "ew-resize" },
  { x: b.x + b.w, y: b.y + b.h, r: 1, b: 1, cur: "nwse-resize" }, { x: b.x + b.w / 2, y: b.y + b.h, b: 1, cur: "ns-resize" },
  { x: b.x, y: b.y + b.h, l: 1, b: 1, cur: "nesw-resize" }, { x: b.x, y: b.y + b.h / 2, l: 1, cur: "ew-resize" },
];
function handleAt(px, py) {
  const sel = S.boxes.find((b) => b.id === S.sel);
  if (!sel || !S.page || sel.page !== S.page.id) return null;
  const tol = 8 / S.zoom;
  return handles(sel).find((h) => Math.abs(px - h.x) < tol && Math.abs(py - h.y) < tol) || null;
}

// ------------------------------------------------------------------ mouse
const toPage = (e) => {
  const r = cv.getBoundingClientRect();
  return [(e.clientX - r.left - S.panX) / S.zoom, (e.clientY - r.top - S.panY) / S.zoom];
};
const norm = (b) => ({ ...b, x: Math.min(b.x, b.x + b.w), y: Math.min(b.y, b.y + b.h), w: Math.abs(b.w), h: Math.abs(b.h) });

cv.addEventListener("contextmenu", (e) => e.preventDefault());
cv.addEventListener("mousedown", (e) => {
  if (!S.page) return;
  hideInput(true);
  const [px, py] = toPage(e);
  if (e.button === 1 || e.button === 2 || S.space) {
    S.drag = { kind: "pan", sx: e.clientX, sy: e.clientY, px: S.panX, py: S.panY };
    return;
  }
  const tol = 7 / S.zoom;
  const sel = S.boxes.find((b) => b.id === S.sel);
  // the selected box's handles resize it directly; moving the whole box and
  // dragging its baseline need Shift held, so a click that wobbles never
  // shifts the input data
  const h = handleAt(px, py);
  if (h) { S.drag = { kind: "resize", h, box: sel, orig: { ...sel } }; return; }
  if (sel && sel.page === S.page.id && e.shiftKey) {
    const cell = (sel.cells || []).find((c) => c.baseline != null && px >= c.x && px <= c.x + c.w && Math.abs(py - c.baseline) < tol);
    if (cell) { S.drag = { kind: "base", box: sel }; return; }
  }
  const hit = [...pageBoxes()].reverse().find((b) => px >= b.x && px <= b.x + b.w && py >= b.y && py <= b.y + b.h);
  if (hit) {
    S.sel = hit.id; showSel();
    if (e.shiftKey) S.drag = { kind: "move", box: hit, ox: px - hit.x, oy: py - hit.y, moved: false, sx: e.clientX, sy: e.clientY };
    draw();
    return;
  }
  S.sel = null; showSel();
  S.draft = { x: px, y: py, w: 0, h: 0 };
  S.drag = { kind: "new" };
});
cv.addEventListener("mousemove", (e) => {
  S.mouseY = e.clientY - cv.getBoundingClientRect().top;
  if (S.guides || S.rotPreview) draw();
  if (S.drag || S.space) return;
  const [px, py] = toPage(e);
  const h = handleAt(px, py);
  cv.style.cursor = h ? h.cur : "crosshair";
});
window.addEventListener("mousemove", (e) => {
  if (!S.drag) return;
  const [px, py] = toPage(e);
  const d = S.drag;
  if (d.kind === "pan") { S.panX = d.px + e.clientX - d.sx; S.panY = d.py + e.clientY - d.sy; viewChanged(); return; }
  if (d.kind === "new") { S.draft.w = px - S.draft.x; S.draft.h = py - S.draft.y; draw(); return; }
  if (d.kind === "move") {
    if (!d.moved && Math.hypot(e.clientX - d.sx, e.clientY - d.sy) < 4) return;   // a click, not a drag
    d.box.x = px - d.ox; d.box.y = py - d.oy; d.moved = true; d.box.cells = []; draw(); return;
  }
  if (d.kind === "resize") {
    const o = d.orig, b = d.box, h = d.h;
    const x0 = h.l ? px : o.x, x1 = h.r ? px : o.x + o.w, y0 = h.t ? py : o.y, y1 = h.b ? py : o.y + o.h;
    Object.assign(b, norm({ x: x0, y: y0, w: x1 - x0, h: y1 - y0 }));
    b.cells = []; draw(); return;
  }
  if (d.kind === "base") {
    d.box.baseline = py;
    for (const c of d.box.cells || []) { c.baseline = py; c.borrowed = false; }
    draw();
  }
});
window.addEventListener("mouseup", async () => {
  const d = S.drag;
  S.drag = null;
  if (!d) return;
  if (d.kind === "new") {
    const b = norm(S.draft);
    if (b.w * S.zoom < 4 || b.h * S.zoom < 4) { S.draft = null; draw(); return; }
    S.draft = b;
    askGlyph(b, "");
  } else if (d.kind === "move" && d.moved || d.kind === "resize" || d.kind === "base") {
    await saveBox(d.box);
  }
});
cv.addEventListener("dblclick", (e) => {          // double-click a box to edit its text
  if (!S.page) return;
  const [px, py] = toPage(e);
  const hit = [...pageBoxes()].reverse().find((b) => px >= b.x && px <= b.x + b.w && py >= b.y && py <= b.y + b.h);
  if (!hit) return;
  S.drag = null;
  S.sel = hit.id; showSel(); draw();
  askGlyph(hit, hit.text || "");
});
cv.addEventListener("wheel", (e) => {
  e.preventDefault();
  const r = cv.getBoundingClientRect();
  const mx = e.clientX - r.left, my = e.clientY - r.top;
  const f = Math.exp(-e.deltaY * 0.0015);
  const nz = Math.min(40, Math.max(0.02, S.zoom * f));
  S.panX = mx - (mx - S.panX) * (nz / S.zoom);
  S.panY = my - (my - S.panY) * (nz / S.zoom);
  S.zoom = nz;
  viewChanged();
}, { passive: false });

// ------------------------------------------------------------------ glyph entry
const input = $("glyph-input");
function askGlyph(box, value) {
  S.editing = box;
  const r = cv.getBoundingClientRect(), st = $("stage").getBoundingClientRect();
  const sx = box.x * S.zoom + S.panX + (r.left - st.left), sy = (box.y + box.h) * S.zoom + S.panY + (r.top - st.top) + 6;
  input.style.left = `${Math.max(4, Math.min(st.width - 190, sx))}px`;
  input.style.top = `${Math.max(4, Math.min(st.height - 40, sy))}px`;
  input.value = value;
  input.placeholder = "glyph(s), Enter";
  input.hidden = false;
  input.focus(); input.select();
}
function hideInput(cancel) {
  if (input.hidden) return;
  input.hidden = true;
  if (cancel && S.editing && !S.editing.id) { S.draft = null; draw(); }
  S.editing = null;
}
input.addEventListener("keydown", async (e) => {
  e.stopPropagation();
  if (e.key === "Escape") { hideInput(true); return; }
  if (e.key !== "Enter") return;
  const text = input.value;
  const box = S.editing;
  hideInput(false);
  if (!text) { S.draft = null; draw(); return; }
  if (box.id) { box.text = text; await saveBox(box); }
  else {
    const saved = await api("/api/box", { method: "POST", body: { page: S.page.id, set: S.activeSet, ...norm(box), text } });
    S.boxes.push(saved); S.allBoxes.push(saved); S.draft = null; S.sel = saved.id;
    afterChange();
  }
});

async function saveBox(box) {
  const body = { id: box.id, page: box.page, set: box.set ?? S.activeSet, x: box.x, y: box.y, w: box.w, h: box.h, text: box.text,
    baseline: box.baseline ?? null };
  const saved = await api("/api/box", { method: "POST", body });
  Object.assign(box, saved);
  afterChange();
}
async function afterChange() {
  draw(); showSel(); renderGlyphs(); renderPages();
  // a new baseline can lend itself to neighbours on the page: refresh them all
  try {
    setBoxes(await api("/api/boxes"));
    renderSets();
  } catch (e) { $("log").textContent = `Couldn't refresh the boxes (${e.message}) - your change is saved; reload if anything looks off.`; }
  draw(); renderGlyphs(); showSel();
}

// ------------------------------------------------------------------ keyboard
window.addEventListener("keydown", async (e) => {
  if (e.target.tagName === "INPUT") return;
  if (e.code === "Space") { S.space = true; cv.style.cursor = "grab"; e.preventDefault(); return; }
  const b = S.boxes.find((x) => x.id === S.sel);
  if (e.key === "f") { if (S.page) fit(); return; }
  if (!b) return;
  if (e.key === "Delete" || e.key === "Backspace") { await delBox(b); e.preventDefault(); }
  else if (e.key === "Enter") { askGlyph(b, b.text || ""); e.preventDefault(); }
  else if (e.key.startsWith("Arrow")) {
    const s = e.shiftKey ? 10 : 1;
    if (e.key === "ArrowLeft") b.x -= s; if (e.key === "ArrowRight") b.x += s;
    if (e.key === "ArrowUp") b.y -= s; if (e.key === "ArrowDown") b.y += s;
    e.preventDefault(); draw();
    clearTimeout(b._t); b._t = setTimeout(() => saveBox(b), 300);
  }
});
window.addEventListener("keyup", (e) => { if (e.code === "Space") { S.space = false; cv.style.cursor = "crosshair"; } });

async function delBox(b) {
  await api(`/api/box/${b.id}`, { method: "DELETE" });
  S.boxes = S.boxes.filter((x) => x.id !== b.id);
  S.allBoxes = S.allBoxes.filter((x) => x.id !== b.id);
  S.sel = null;
  afterChange();
}

// ------------------------------------------------------------------ selection panel
function showSel() {
  const b = S.boxes.find((x) => x.id === S.sel);
  $("sel").hidden = !b;
  if (!b) return;
  if (document.activeElement !== $("sel-text")) $("sel-text").value = b.text || "";
  const cells = b.cells || [];
  const manual = b.baseline != null;
  const borrowed = cells.some((c) => c.borrowed);
  const noBase = cells.filter((c) => c.baseline == null).length;
  $("sel-info").textContent = `${cells.length} cell${cells.length === 1 ? "" : "s"} · baseline ${manual ? "set by hand" : borrowed ? "borrowed from the line" : "automatic"}`
    + (noBase ? ` · ${noBase} without a baseline - drag the red line or label a letter beside it` : "")
    + (cells.find((c) => c.warn) ? `\n⚠ ${cells.find((c) => c.warn).warn}` : "");
  $("sel-set").innerHTML = S.sets.map((st) => `<option value="${st.id}"${st.id === (b.set ?? 1) ? " selected" : ""}></option>`).join("");
  [...$("sel-set").options].forEach((o, i) => { o.textContent = S.sets[i].name; });
  const out = 240;
  $("sel-crop").innerHTML = `<img alt="" src="/api/page/${b.page}/region?x=${b.x - b.h * 0.2}&y=${b.y - b.h * 0.2}&w=${b.w + b.h * 0.4}&h=${b.h * 1.4}&out=${out}">`;
}
$("sel-text").addEventListener("change", async (e) => {
  const b = S.boxes.find((x) => x.id === S.sel);
  if (b) { b.text = e.target.value; await saveBox(b); }
});
$("sel-set").addEventListener("change", async (e) => {
  const b = S.boxes.find((x) => x.id === S.sel);
  if (!b) return;
  const to = Number(e.target.value), name = S.sets.find((s) => s.id === to)?.name;
  b.set = to;
  await saveBox(b);
  $("log").textContent = `Box ${b.id} moved to the font "${name}".`;
});
$("sel-del").onclick = () => { const b = S.boxes.find((x) => x.id === S.sel); if (b) delBox(b); };
$("sel-base-clear").onclick = async () => {
  const b = S.boxes.find((x) => x.id === S.sel);
  if (b) { b.baseline = null; await saveBox(b); }
};

// ------------------------------------------------------------------ sources
$("upload-btn").onclick = () => $("upload-input").click();
$("upload-input").addEventListener("change", async (e) => {
  const files = [...e.target.files];
  e.target.value = "";
  let added = 0;
  for (const [i, f] of files.entries()) {
    $("upload-status").textContent = `Uploading ${f.name} (${i + 1}/${files.length}, ${(f.size / 1e6).toFixed(1)} MB)…`;
    try {
      const r = await fetch(`/api/upload?name=${encodeURIComponent(f.name)}`, { method: "POST", body: f });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
      const j = await r.json();
      S.pages = j.pages; added += j.added;
    } catch (err) { $("upload-status").textContent = `${f.name}: ${err.message}`; renderPages(); return; }
  }
  renderPages();
  $("upload-status").textContent = `Added ${added} page${added === 1 ? "" : "s"} from ${files.length} file${files.length === 1 ? "" : "s"}.`;
  const last = S.pages[S.pages.length - added];
  if (last) openPage(last.id);
});
$("add-btn").onclick = async () => {
  const p = $("add-path").value.trim().replace(/^"|"$/g, "");
  if (!p) return;
  try {
    const r = await api("/api/sources", { method: "POST", body: { paths: [p] } });
    S.pages = r.pages; renderPages();
    $("add-path").value = "";
  } catch (e) { $("log").textContent = e.message; }
};

// ------------------------------------------------------------------ regenerate + results
function tab(which) {
  for (const k of ["label", "images", "result"]) {
    $(`tab-${k}`).classList.toggle("on", which === k);
    $(`view-${k}`).hidden = which !== k;
  }
  if (which === "label") resize();
}
$("tab-label").onclick = () => tab("label");
$("tab-images").onclick = () => showImages();
$("tab-result").onclick = async () => {
  tab("result");
  const res = await api("/api/result");
  if (res) renderResult(res);
  else if (!$("result-grid").children.length) $("result-head").innerHTML = '<p class="muted">Nothing regenerated yet - label some glyphs, then press Regenerate.</p>';
};
$("regen").onclick = async () => {
  try { await api("/api/regenerate", { method: "POST", body: {} }); }
  catch (e) { $("log").textContent = e.message; return; }
  $("regen").disabled = true; $("regen").textContent = "Regenerating…";
  $("log").textContent = "";
  let n = 0;
  const poll = async () => {
    const st = await api(`/api/regenerate?since=${n}`);
    n = st.n;
    if (st.log.length) { $("log").textContent += st.log.join("\n") + "\n"; $("log").scrollTop = 1e9; }
    if (st.running) return setTimeout(poll, 600);
    $("regen").disabled = false; $("regen").textContent = "Regenerate";
    if (st.error) { $("log").textContent += "ERROR: " + st.error; return; }
    if (st.result) { renderResult(st.result); tab("result"); }
  };
  poll();
};

// Every glyph is drawn in font units inside one frame shared by the whole
// font (r.extent), so sizes compare - including the averaged scan, placed by
// its baseline and pitch rather than by its pixel count.
function frameOf(r) {
  const e = r.extent || [0, -0.3 * (r.upem || 1000), r.advance, 0.85 * (r.upem || 1000)];
  const pad = 0.06 * (e[3] - e[1]);
  return { x: e[0] - pad, y: -(e[3] + pad), w: e[2] - e[0] + 2 * pad, h: e[3] - e[1] + 2 * pad };
}
function panel(kind, g, r, strokes) {
  const f = frameOf(r), adv = r.advance, sw = f.h / 300;
  let body = "";
  if (kind === "avg") {
    body = g.avg_img
      ? `<image href="${g.avg_png}" x="${g.avg_img[0]}" y="${g.avg_img[1]}" width="${g.avg_img[2]}" height="${g.avg_img[3]}" preserveAspectRatio="none" style="image-rendering:pixelated"/>`
      : `<text x="${f.x + f.w / 2}" y="${f.y + f.h / 2}" font-size="${f.h / 14}" text-anchor="middle" fill="#999">regenerate to see</text>`;
  } else {
    const d = kind === "seed" ? g.seed_svg : g.avg_svg;
    const lines = (strokes || []).map((p) => `<polyline points="${p.map(([x, y]) => `${x},${y}`).join(" ")}" fill="none" stroke="#e8590c" stroke-width="${sw * 2.5}"/>`).join("");
    body = `<g transform="scale(1,-1)"><path d="${d || ""}" fill="#111" fill-rule="evenodd"/>${lines}</g>`;
  }
  return `<svg viewBox="${f.x} ${f.y} ${f.w} ${f.h}" preserveAspectRatio="xMidYMid meet">
    <rect x="0" y="${f.y}" width="${adv}" height="${f.h}" fill="none" stroke="#e3e3e3" stroke-width="${sw}"/>
    <line x1="${f.x}" x2="${f.x + f.w}" y1="0" y2="0" stroke="#bbb" stroke-width="${sw}" stroke-dasharray="${sw * 6} ${sw * 4}"/>
    ${body}</svg>`;
}
const SORTS = {
  char: { label: "character", fn: (a, b) => a[0].localeCompare(b[0]) },
  most: { label: "most samples", fn: (a, b) => (b[1].n || 0) - (a[1].n || 0) || a[0].localeCompare(b[0]) },
  fewest: { label: "fewest samples", fn: (a, b) => (a[1].n || 0) - (b[1].n || 0) || a[0].localeCompare(b[0]) },
  match: { label: "worst match first", fn: (a, b) => (a[1].iou ?? 2) - (b[1].iou ?? 2) },
};
// Test type: the two fonts just written, loaded straight into the page
let ttStamp = 0;
async function loadTestFonts(r) {
  const stamp = ++ttStamp;
  const out = {};
  for (const [kind, file] of Object.entries(r.fonts || {})) {
    const fam = `ts-${kind}-${stamp}`;
    try {
      const ff = new FontFace(fam, `url("/api/out/${encodeURIComponent(r.out)}/${encodeURIComponent(file)}?v=${Date.now()}")`);
      await ff.load();
      document.fonts.add(ff);
      out[kind] = fam;
    } catch { out[kind] = null; }
  }
  if (stamp !== ttStamp) return;
  const set = (id, fam) => { const el = $(id); el.style.fontFamily = fam ? `"${fam}", monospace` : "monospace"; el.style.opacity = fam ? 1 : 0.4; };
  set("tt-avg", out.average); set("tt-seed", out.seed);
  paintTestType(r);
}
function paintTestType(r) {
  const t = $("tt-text").value;
  const size = $("tt-size").value + "px";
  for (const id of ["tt-avg", "tt-seed"]) { $(id).textContent = t; $(id).style.fontSize = size; }
  const have = new Set(Object.keys(r.chars || {}).filter((c) => r.chars[c].avg_svg));
  const missing = [...new Set([...t])].filter((c) => !/\s/.test(c) && !have.has(c));
  $("tt-missing").textContent = missing.length ? `Not in this font yet (shown in a stand-in): ${missing.join(" ")}` : "";
}
(() => {
  const saved = (() => { try { return localStorage.getItem("ts.tt") || ""; } catch { return ""; } })();
  $("tt-text").value = saved || "The quick brown fox jumps over the lazy dog 0123456789";
  $("tt-text").addEventListener("input", () => { try { localStorage.setItem("ts.tt", $("tt-text").value); } catch {} if (lastResult) paintTestType(lastResult); });
  $("tt-size").addEventListener("input", () => { if (lastResult) paintTestType(lastResult); });
})();

let lastResult = null;
function renderResult(r) {
  lastResult = r;
  loadTestFonts(r);
  const fonts = Object.entries(r.fonts || {}).map(([k, f]) => `<a class="dl" href="/api/out/${encodeURIComponent(r.out)}/${encodeURIComponent(f)}" download>${f}</a>`).join(" · ");
  const est = r.estimated_pitch_mm ? ` · your text runs suggest ${r.estimated_pitch_mm.toFixed(3)} mm` : "";
  const sort = localStorage.getItem("ts.sort") || "char";
  $("result-head").innerHTML = `<b>${r.set_name || ""}</b> · <b>Fonts:</b> ${fonts || "none"} <span class="muted">pitch ${r.pitch_mm} mm${est}</span>
    <span class="spacer"></span>
    <label class="inline">Sort <select id="res-sort">${Object.entries(SORTS).map(([k, v]) => `<option value="${k}"${k === sort ? " selected" : ""}>${v.label}</option>`).join("")}</select></label>
    <label class="inline"><input type="checkbox" id="show-strokes" ${localStorage.getItem("ts.strokes") === "0" ? "" : "checked"}> seed strokes</label>
    ${r.extent ? "" : '<span class="muted">(regenerate once more for one common scale)</span>'}`;
  const f = frameOf(r);
  const paint = () => {
    const grid = $("result-grid");
    grid.innerHTML = "";
    grid.style.setProperty("--ar", `${f.w} / ${f.h}`);
    const show = $("show-strokes").checked;
    const entries = Object.entries(r.chars).sort(SORTS[$("res-sort").value].fn);
    for (const [ch, g] of entries) {
      const card = document.createElement("div");
      card.className = "rcard";
      const title = `<div class="t"><b></b><span class="muted">${g.image ? "glyph image" + (g.n ? " · " : "") : ""}${g.n ? `${g.n} sample${g.n === 1 ? "" : "s"}${g.image ? " not used" : ""}` : ""}${g.iou != null ? ` · match ${g.iou}` : ""}${g.error ? ` · <span style="color:var(--bad)">${g.error}</span>` : ""}${g.warning ? ` · <span style="color:var(--bad)">${g.warning}</span>` : ""}${g.odd && g.odd.length ? ` · <span style="color:var(--warn,#f0a24d)">${g.odd.length} odd left out</span>` : ""}</span></div>`;
      if (!g.n && !g.image || g.error && !g.avg_svg) { card.innerHTML = title; card.querySelector("b").textContent = ch; grid.appendChild(card); continue; }
      card.innerHTML = `${title}
        <div class="imgs"><div>${panel("avg", g, r)}</div><div>${panel("trace", g, r)}</div><div>${panel("seed", g, r, show ? g.strokes : null)}</div></div>
        <div class="cap"><span>${g.image ? "picture" : "averaged"}</span><span>traced</span><span>seed, regrown</span></div>`;
      card.querySelector("b").textContent = ch;
      card.title = "Click to enlarge";
      card.onclick = () => enlarge(ch, g, r);
      grid.appendChild(card);
    }
  };
  paint();
  $("res-sort").onchange = (e) => { localStorage.setItem("ts.sort", e.target.value); paint(); };
  $("show-strokes").onchange = (e) => { localStorage.setItem("ts.strokes", e.target.checked ? "1" : "0"); paint(); };
}

function enlarge(ch, g, r) {
  const ov = document.createElement("div");
  ov.className = "overlay";
  const show = localStorage.getItem("ts.strokes") !== "0";
  ov.innerHTML = `<div class="ov-box" role="dialog" aria-label="Glyph ${ch}">
    <div class="ov-head"><b class="ov-ch"></b><span class="muted">${g.image ? "glyph image" + (g.n ? `, in place of ${g.n} sample${g.n === 1 ? "" : "s"}` : "") : `${g.n} sample${g.n === 1 ? "" : "s"}`}${g.skipped ? ` (${g.skipped} unusable)` : ""}${g.iou != null ? ` · seed vs trace match ${g.iou}` : ""}</span>
      <span class="spacer"></span>
      <button class="ov-save" title="Save the averaged scan as a big clean PNG, named after its character - clean it up (by hand or with an AI image tool) and add it back under Glyph images">Save picture for clean-up</button>
      ${g.image ? '<button class="ov-img">Edit glyph image</button>' : ""}
      ${g.n ? '<button class="ov-visit">Visit its samples</button>' : ""}<button class="ov-close" title="Close (Esc)">✕</button></div>
    ${g.odd && g.odd.length ? `<div class="ov-odd">Left out of the average - unlike the other samples (match ${g.odd.map((o) => o.match).join(", ")} vs a typical ${g.odd[0].typical}). Usually a label mistake (an ä labelled a, a letter one cell off):
      ${g.odd.map((o) => `<button class="small" data-box="${o.box}">Visit box ${o.box}</button>`).join(" ")}</div>` : ""}
    <div class="ov-imgs" style="--ar:${frameOf(r).w} / ${frameOf(r).h}">
      <figure><div>${panel("avg", g, r)}</div><figcaption>${g.image ? "glyph image" : `averaged (${g.n} impression${g.n === 1 ? "" : "s"})`}</figcaption></figure>
      <figure><div>${panel("trace", g, r)}</div><figcaption>traced - the Average font</figcaption></figure>
      <figure><div>${panel("seed", g, r, show ? g.strokes : null)}</div><figcaption>seed, regrown - the Seed font</figcaption></figure>
    </div></div>`;
  ov.querySelector(".ov-ch").textContent = ch;
  const close = () => { ov.remove(); window.removeEventListener("keydown", esc, true); };
  const esc = (e) => { if (e.key === "Escape") { e.stopPropagation(); close(); } };
  ov.onclick = (e) => { if (e.target === ov) close(); };
  ov.querySelector(".ov-close").onclick = close;
  if (ov.querySelector(".ov-visit")) ov.querySelector(".ov-visit").onclick = () => { close(); tab("label"); jumpTo(ch); };
  if (ov.querySelector(".ov-img")) ov.querySelector(".ov-img").onclick = () => { close(); showImages(g.image.id); };
  ov.querySelector(".ov-save").onclick = () => saveForCleanup(ch, g.scans || g);
  ov.querySelectorAll("[data-box]").forEach((b) => { b.onclick = () => { close(); tab("label"); jumpToBox(Number(b.dataset.box)); }; });
  window.addEventListener("keydown", esc, true);
  document.body.appendChild(ov);
}

// ------------------------------------------------------------------ glyph images
// A clean picture of a character, placed in the font: picture pixel
// (col, row) -> font units X = x + col*s, Y = y - row*s (y up, baseline 0).
// Drawn here in the same frame as Results (SVG, y down), so a picture
// lines up with the glyphs around it.
const uniName = (ch) => `uni${[...ch].map((c) => c.codePointAt(0).toString(16).toUpperCase().padStart(4, "0")).join("")}`;

// the averaged scan of a glyph, enlarged, as a white-backed PNG to clean up
function saveForCleanup(ch, g) {
  if (!g.avg_png) return;
  const im = new Image();
  im.onload = () => {
    const k = Math.max(1, Math.round(1600 / Math.max(im.width, im.height)));
    const c = document.createElement("canvas");
    c.width = im.width * k; c.height = im.height * k;
    const x = c.getContext("2d");
    x.fillStyle = "#fff"; x.fillRect(0, 0, c.width, c.height);
    x.imageSmoothingEnabled = true; x.imageSmoothingQuality = "high";
    x.drawImage(im, 0, 0, c.width, c.height);
    const a = document.createElement("a");
    a.href = c.toDataURL("image/png");
    a.download = `${uniName(ch)}.png`;
    a.click();
  };
  im.src = g.avg_png;
}

let imgResult = null;
async function showImages(focus) {
  tab("images");
  try { imgResult = await api("/api/result"); } catch { imgResult = null; }
  S.images = await api("/api/images");
  renderImages(focus);
}
// cap and x-height of this face (from the last Regenerate) for guide lines
function faceGuides(r) {
  const tops = (cs) => {
    const v = [...cs].map((c) => r?.chars?.[c]?.avg_bounds?.[3] ?? (r?.chars?.[c]?.image ? null : r?.chars?.[c]?.bounds?.[3])).filter((v) => v != null).sort((a, b) => a - b);
    return v.length ? v[v.length >> 1] : null;
  };
  return { cap: tops("HEFIKLMNTZ"), x: tops("xzvwnmru") };
}
function imageFrame(r) {
  const upem = r?.upem || 1000, adv = r?.advance || S.settings.advance || 600;
  const e = r?.extent || [0, -0.3 * upem, adv, 0.85 * upem];
  const x0 = Math.min(e[0], -0.15 * adv), x1 = Math.max(e[2], 1.15 * adv);
  const y0 = Math.min(e[1], -0.3 * upem), y1 = Math.max(e[3], 0.85 * upem);
  const pad = 0.04 * (y1 - y0);
  return { x: x0 - pad, y: -(y1 + pad), w: x1 - x0 + 2 * pad, h: y1 - y0 + 2 * pad, adv, upem };
}
function renderImages(focus) {
  const grid = $("img-grid");
  grid.innerHTML = "";
  const r = imgResult, f = imageFrame(r), gd = faceGuides(r);
  const refCh = $("img-ref").value;
  $("img-status").textContent = S.images.length
    ? `${S.images.length} picture${S.images.length === 1 ? "" : "s"} in ${S.sets.find((x) => x.id === S.activeSet)?.name || "this font"} · press Regenerate to put them in the fonts`
    : "No glyph images in this font yet.";
  if (!r) $("img-status").textContent += " · Regenerate once so pictures can be sized to this face";
  const used = {};
  for (const g of S.images) if (g.enabled !== false && g.char) used[g.char] = g.id;   // the last one of a character wins
  for (const g of S.images) {
    const card = document.createElement("div");
    card.className = "icard" + (g.enabled === false ? " off" : "");
    const dupe = g.char && used[g.char] !== g.id && g.enabled !== false;
    const scans = r?.chars?.[g.char]?.scans || (r?.chars?.[g.char] && !r.chars[g.char].image ? r.chars[g.char] : null);
    const ref = refCh && refCh !== g.char ? r?.chars?.[refCh] : null;
    const sw = f.h / 300;
    const guide = (y, cls) => y == null ? "" : `<line x1="${f.x}" x2="${f.x + f.w}" y1="${-y}" y2="${-y}" class="${cls}" stroke-width="${sw}"/>`;
    card.innerHTML = `
      <div class="ic-head"><input class="ic-ch" maxlength="2" spellcheck="false" autocomplete="off" placeholder="?" title="Which character this picture is">
        <span class="muted small ic-name"></span><span class="spacer"></span>
        <label class="inline small" title="Use this picture when regenerating"><input type="checkbox" class="ic-on" ${g.enabled === false ? "" : "checked"}> use</label></div>
      <div class="ic-ed"><svg viewBox="${f.x} ${f.y} ${f.w} ${f.h}" preserveAspectRatio="xMidYMid meet" tabindex="0">
        <rect x="0" y="${f.y}" width="${f.adv}" height="${f.h}" class="cell" stroke-width="${sw}"/>
        ${guide(0, "base")}${guide(gd.cap, "gl")}${guide(gd.x, "gl")}
        ${ref?.avg_svg ? `<path d="${ref.avg_svg}" transform="scale(1,-1)" class="ghost-ref" fill-rule="evenodd"/>` : ""}
        ${scans?.avg_img ? `<image href="${scans.avg_png}" x="${scans.avg_img[0]}" y="${scans.avg_img[1]}" width="${scans.avg_img[2]}" height="${scans.avg_img[3]}" preserveAspectRatio="none" class="ghost-scan"/>` : ""}
        <image class="pic" href="/api/images/${g.id}/file" preserveAspectRatio="none"/>
      </svg></div>
      <div class="ic-foot"><span class="muted small ic-how"></span><span class="spacer"></span>
        <button class="small ic-auto" title="Place it again automatically">Auto place</button>
        <button class="small danger ic-del" title="Delete this picture">Delete</button></div>`;
    card.querySelector(".ic-ch").value = g.char || "";
    card.querySelector(".ic-name").textContent = g.name || g.file;
    card.querySelector(".ic-name").title = g.file;
    const svg = card.querySelector("svg"), pic = card.querySelector(".pic");
    const how = () => {
      const el = card.querySelector(".ic-how");
      el.textContent = !g.char ? "type its character" : dupe ? `another picture of ${g.char} is used instead` :
        { average: "fitted onto its scans", reference: "placed like a standard font", hand: "placed by hand", empty: "no ink found" }[g.how] || "";
      el.style.color = !g.char || dupe ? "var(--warn,#f0a24d)" : "";
    };
    const put = () => {
      const p = g.place; if (!p) return;
      pic.setAttribute("x", p.x); pic.setAttribute("y", -p.y);
      pic.setAttribute("width", (g.w || 1000) * p.s); pic.setAttribute("height", (g.h || 1000) * p.s);
    };
    put(); how();
    let saveT = null;
    const save = () => {
      clearTimeout(saveT);
      saveT = setTimeout(async () => {
        try { Object.assign(g, await api(`/api/images/${g.id}`, { method: "POST", body: { place: g.place } })); how(); }
        catch (e) { $("img-status").textContent = e.message; }
      }, 250);
    };
    const toFont = (e) => { const pt = new DOMPoint(e.clientX, e.clientY).matrixTransform(svg.getScreenCTM().inverse()); return [pt.x, -pt.y]; };
    let drag = null;
    svg.addEventListener("pointerdown", (e) => { if (!g.place) return; svg.setPointerCapture(e.pointerId); svg.focus(); drag = { at: toFont(e), x: g.place.x, y: g.place.y }; });
    svg.addEventListener("pointermove", (e) => {
      if (!drag) return;
      const [X, Y] = toFont(e);
      g.place = { ...g.place, x: drag.x + X - drag.at[0], y: drag.y + Y - drag.at[1] };
      put();
    });
    svg.addEventListener("pointerup", () => { if (drag) { drag = null; g.how = "hand"; how(); save(); } });
    svg.addEventListener("wheel", (e) => {
      if (!g.place) return;
      e.preventDefault();
      const k = Math.exp(-e.deltaY * (e.shiftKey ? 0.0002 : 0.0015));
      const [X, Y] = toFont(e), p = g.place;         // scale about the pointer
      g.place = { x: X + (p.x - X) * k, y: Y + (p.y - Y) * k, s: p.s * k };
      put(); g.how = "hand"; how(); save();
    }, { passive: false });
    svg.addEventListener("keydown", (e) => {
      const d = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, 1], ArrowDown: [0, -1] }[e.key];
      if (!d || !g.place) return;
      e.preventDefault();
      const n = (e.shiftKey ? 20 : 2);
      g.place = { ...g.place, x: g.place.x + d[0] * n, y: g.place.y + d[1] * n };
      put(); g.how = "hand"; how(); save();
    });
    card.querySelector(".ic-ch").addEventListener("change", async (e) => {
      try { Object.assign(g, await api(`/api/images/${g.id}`, { method: "POST", body: { char: e.target.value.trim() } })); }
      catch (err) { $("img-status").textContent = err.message; }
      renderImages(g.id);
    });
    card.querySelector(".ic-on").addEventListener("change", async (e) => {
      Object.assign(g, await api(`/api/images/${g.id}`, { method: "POST", body: { enabled: e.target.checked } }));
      renderImages(g.id);
    });
    card.querySelector(".ic-auto").onclick = async () => {
      Object.assign(g, await api(`/api/images/${g.id}`, { method: "POST", body: { auto: true } }));
      put(); how();
    };
    card.querySelector(".ic-del").onclick = async () => {
      await api(`/api/images/${g.id}`, { method: "DELETE" });
      S.images = S.images.filter((x) => x.id !== g.id);
      renderImages();
    };
    grid.appendChild(card);
    if (focus === g.id) { card.scrollIntoView({ block: "center" }); card.classList.add("focus"); }
  }
}
$("img-add").onclick = () => $("img-input").click();
$("img-input").addEventListener("change", async (e) => {
  const files = [...e.target.files];
  e.target.value = "";
  let last = null;
  for (const [i, f] of files.entries()) {
    $("img-status").textContent = `Adding ${f.name} (${i + 1}/${files.length})…`;
    try {
      const r = await fetch(`/api/images?name=${encodeURIComponent(f.name)}`, { method: "POST", body: f });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
      last = (await r.json()).id;
    } catch (err) { $("img-status").textContent = `${f.name}: ${err.message}`; }
  }
  S.images = await api("/api/images");
  renderImages(last);
});
(() => {
  const saved = (() => { try { return localStorage.getItem("ts.imgref"); } catch { return null; } })();
  $("img-ref").value = saved ?? "H";
  $("img-ref").addEventListener("input", () => { try { localStorage.setItem("ts.imgref", $("img-ref").value); } catch {} renderImages(); });
})();

resize();
load();
