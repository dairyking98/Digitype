// Type Seed labelling tool: pages on a zoomable canvas, boxes dragged
// around glyphs and labelled, then Regenerate turns them into a font.
const $ = (id) => document.getElementById(id);
const api = async (url, opts = {}) => {
  const r = await fetch(url, { headers: { "Content-Type": "application/json" }, ...opts,
    body: opts.body && typeof opts.body !== "string" ? JSON.stringify(opts.body) : opts.body });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
};

const S = { pages: [], boxes: [], settings: {}, page: null, img: null, imgScale: 1, hi: null,
  zoom: 1, panX: 0, panY: 0, sel: null, drag: null, draft: null, space: false, editing: null, jump: {} };
const cv = $("cv"), ctx = cv.getContext("2d");
const dpr = () => window.devicePixelRatio || 1;

// ------------------------------------------------------------------ data
async function load() {
  const p = await api("/api/project");
  Object.assign(S, { pages: p.pages, boxes: p.boxes, settings: p.settings });
  $("root").textContent = p.root;
  showSettings(p.estimated_pitch_mm);
  renderPages();
  renderGlyphs();
  const last = Number(localStorage.getItem("ts.page"));
  if (S.pages.length) openPage(S.pages.find((x) => x.id === last) ? last : S.pages[0].id);
}

function showSettings(est) {
  const s = S.settings;
  $("set-pitch").value = s.pitch_mm; $("set-name").value = s.font_name; $("set-up").value = s.upsample;
  $("set-weight").value = s.weight; $("set-add").value = s.add;
  $("pitch-est").textContent = est ? `From your text runs: about ${est.toFixed(3)} mm (${(25.4 / est).toFixed(1)} per inch)` : "";
}
for (const [id, key] of [["set-pitch", "pitch_mm"], ["set-name", "font_name"], ["set-up", "upsample"], ["set-weight", "weight"], ["set-add", "add"]]) {
  $(id).addEventListener("change", async (e) => { S.settings = await api("/api/settings", { method: "POST", body: { [key]: e.target.value } }); });
}

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
  img.src = `/api/page/${id}/preview`;
  await img.decode().catch(() => {});
  if (S.page.id !== id) return;
  S.img = img;
  S.imgScale = img.naturalWidth / S.page.w;
  $("log").textContent = "";
  fit();
}

function fit() {
  const r = cv.getBoundingClientRect();
  S.zoom = Math.min(r.width / S.page.w, r.height / S.page.h) * 0.96;
  S.panX = (r.width - S.page.w * S.zoom) / 2;
  S.panY = (r.height - S.page.h * S.zoom) / 2;
  viewChanged();
}

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
  const pid = S.page.id, key = [x, y, w, h].map(Math.round).join(",");
  const img = new Image();
  img.src = `/api/page/${pid}/region?x=${x}&y=${y}&w=${w}&h=${h}&out=${out}`;
  await img.decode().catch(() => {});
  if (S.page.id !== pid) return;
  S.hi = { img, x, y, w, h, key };
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
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(S.img, 0, 0, S.page.w, S.page.h);
  if (S.hi) ctx.drawImage(S.hi.img, S.hi.x, S.hi.y, S.hi.w, S.hi.h);
  const lw = 1.5 / z;
  ctx.font = `${13 / z}px ui-monospace, monospace`;
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
        if (c.baseline != null) {
          ctx.strokeStyle = c.borrowed ? "rgba(255,107,107,.55)" : "#ff6b6b"; ctx.lineWidth = lw;
          ctx.beginPath(); ctx.moveTo(c.x, c.baseline); ctx.lineTo(c.x + c.w, c.baseline); ctx.stroke();
        }
      }
    }
    if (on) {
      ctx.fillStyle = "#6c9cff";
      const s = 8 / z;
      for (const [hx, hy] of corners(b)) ctx.fillRect(hx - s / 2, hy - s / 2, s, s);
    }
  }
  if (S.draft) {
    ctx.strokeStyle = "#6c9cff"; ctx.lineWidth = lw; ctx.setLineDash([6 / z, 4 / z]);
    ctx.strokeRect(S.draft.x, S.draft.y, S.draft.w, S.draft.h); ctx.setLineDash([]);
  }
}
const corners = (b) => [[b.x, b.y], [b.x + b.w, b.y], [b.x, b.y + b.h], [b.x + b.w, b.y + b.h]];

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
  if (sel && sel.page === S.page.id) {
    const ci = corners(sel).findIndex(([hx, hy]) => Math.abs(px - hx) < tol && Math.abs(py - hy) < tol);
    if (ci >= 0) { S.drag = { kind: "resize", ci, box: sel, orig: { ...sel } }; return; }
    const cell = (sel.cells || []).find((c) => c.baseline != null && px >= c.x && px <= c.x + c.w && Math.abs(py - c.baseline) < tol);
    if (cell) { S.drag = { kind: "base", box: sel }; return; }
  }
  const hit = [...pageBoxes()].reverse().find((b) => px >= b.x && px <= b.x + b.w && py >= b.y && py <= b.y + b.h);
  if (hit) {
    S.sel = hit.id; showSel();
    S.drag = { kind: "move", box: hit, ox: px - hit.x, oy: py - hit.y, moved: false };
    draw();
    return;
  }
  S.sel = null; showSel();
  S.draft = { x: px, y: py, w: 0, h: 0 };
  S.drag = { kind: "new" };
});
window.addEventListener("mousemove", (e) => {
  if (!S.drag) return;
  const [px, py] = toPage(e);
  const d = S.drag;
  if (d.kind === "pan") { S.panX = d.px + e.clientX - d.sx; S.panY = d.py + e.clientY - d.sy; viewChanged(); return; }
  if (d.kind === "new") { S.draft.w = px - S.draft.x; S.draft.h = py - S.draft.y; draw(); return; }
  if (d.kind === "move") { d.box.x = px - d.ox; d.box.y = py - d.oy; d.moved = true; d.box.cells = []; draw(); return; }
  if (d.kind === "resize") {
    const o = d.orig, b = d.box;
    const left = d.ci % 2 === 0, top = d.ci < 2;
    const x0 = left ? px : o.x, x1 = left ? o.x + o.w : px, y0 = top ? py : o.y, y1 = top ? o.y + o.h : py;
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
    const saved = await api("/api/box", { method: "POST", body: { page: S.page.id, ...norm(box), text } });
    S.boxes.push(saved); S.draft = null; S.sel = saved.id;
    afterChange();
  }
});

async function saveBox(box) {
  const body = { id: box.id, page: box.page, x: box.x, y: box.y, w: box.w, h: box.h, text: box.text,
    baseline: box.baseline ?? null };
  const saved = await api("/api/box", { method: "POST", body });
  Object.assign(box, saved);
  afterChange();
}
async function afterChange() {
  draw(); showSel(); renderGlyphs(); renderPages();
  // a new baseline can lend itself to neighbours on the page: refresh them all
  try {
    S.boxes = await api("/api/boxes");
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
  const out = 240;
  $("sel-crop").innerHTML = `<img alt="" src="/api/page/${b.page}/region?x=${b.x - b.h * 0.2}&y=${b.y - b.h * 0.2}&w=${b.w + b.h * 0.4}&h=${b.h * 1.4}&out=${out}">`;
}
$("sel-text").addEventListener("change", async (e) => {
  const b = S.boxes.find((x) => x.id === S.sel);
  if (b) { b.text = e.target.value; await saveBox(b); }
});
$("sel-del").onclick = () => { const b = S.boxes.find((x) => x.id === S.sel); if (b) delBox(b); };
$("sel-base-clear").onclick = async () => {
  const b = S.boxes.find((x) => x.id === S.sel);
  if (b) { b.baseline = null; await saveBox(b); }
};

// ------------------------------------------------------------------ sources
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
  $("tab-label").classList.toggle("on", which === "label");
  $("tab-result").classList.toggle("on", which === "result");
  $("view-label").hidden = which !== "label";
  $("view-result").hidden = which !== "result";
  if (which === "label") resize();
}
$("tab-label").onclick = () => tab("label");
$("tab-result").onclick = async () => {
  tab("result");
  const st = await api("/api/regenerate");
  if (st.result) renderResult(st.result);
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

function svgOf(d, strokes, adv) {
  const lines = (strokes || []).map((p) => `<polyline points="${p.map(([x, y]) => `${x},${y}`).join(" ")}" fill="none" stroke="#e8590c" stroke-width="8"/>`).join("");
  return `<svg viewBox="-60 -850 ${adv + 120} 1200"><g transform="scale(1,-1)">
    <line x1="-60" x2="${adv + 60}" y1="0" y2="0" stroke="#bbb" stroke-width="3" stroke-dasharray="12 8"/>
    <rect x="0" y="-300" width="${adv}" height="1100" fill="none" stroke="#ddd" stroke-width="2"/>
    <path d="${d}" fill="#111" fill-rule="evenodd"/>${lines}</g></svg>`;
}
function renderResult(r) {
  const fonts = Object.entries(r.fonts || {}).map(([k, f]) => `<a class="dl" href="/api/out/${encodeURIComponent(f)}" download>${f}</a>`).join(" · ");
  const est = r.estimated_pitch_mm ? ` · your text runs suggest ${r.estimated_pitch_mm.toFixed(3)} mm` : "";
  $("result-head").innerHTML = `<b>Fonts:</b> ${fonts || "none"} <span class="muted">pitch ${r.pitch_mm} mm${est}</span>
    <label style="margin:0 0 0 auto;display:flex;gap:6px;align-items:center"><input type="checkbox" id="show-strokes" checked style="width:auto"> seed strokes</label>`;
  const grid = $("result-grid");
  grid.innerHTML = "";
  const show = () => $("show-strokes")?.checked;
  const paint = () => {
    grid.innerHTML = "";
    for (const [ch, g] of Object.entries(r.chars)) {
      const card = document.createElement("div");
      card.className = "rcard";
      if (!g.n) { card.innerHTML = `<div class="t"><b></b><span class="muted">${g.error || "no samples"}</span></div>`; card.querySelector("b").textContent = ch; grid.appendChild(card); continue; }
      card.innerHTML = `<div class="t"><b></b><span class="muted">${g.n} sample${g.n === 1 ? "" : "s"}${g.iou != null ? ` · match ${g.iou}` : ""}</span></div>
        <div class="imgs"><div><img alt="" src="${g.avg_png}"></div><div>${svgOf(g.avg_svg, null, r.advance)}</div><div>${svgOf(g.seed_svg, show() ? g.strokes : null, r.advance)}</div></div>
        <div class="cap"><span>averaged</span><span>traced</span><span>seed, regrown</span></div>`;
      card.querySelector("b").textContent = ch;
      card.title = "Click to visit this glyph's samples";
      card.onclick = () => { tab("label"); jumpTo(ch); };
      grid.appendChild(card);
    }
  };
  paint();
  $("show-strokes").onchange = paint;
}

resize();
load();
