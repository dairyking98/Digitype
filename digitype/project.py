"""A labelling project: the sample files (pages), the boxes drawn on them
and what glyph each holds, and a cache of every page as 8-bit grey.

project dir/
  project.json   sources, pages (file, frame or PDF page, size, dpi), font sets
                 (each typeface its own set, with its own settings: pitch, name...)
  labels.json    boxes: set, page, x y w h (page pixels), text, baseline override
  cache/         page_<n>.npy (grey, full resolution), preview_<n>.jpg
  out/<set>/     what Regenerate writes for each set (fonts, seeds, results)
"""
import json
import os
import threading
import time

import numpy as np
from PIL import Image

Image.MAX_IMAGE_PIXELS = None
IMAGE_EXT = {".tif", ".tiff", ".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"}
PDF_DPI = 600
PREVIEW_MAX = 2400
DEFAULT_SETTINGS = {"pitch_mm": 2.54, "upem": 1000, "advance": 600, "font_name": "", "upsample": 4,
                    "weight": 1.0, "add": 0.0, "expand_left": 0.5, "expand_right": 0.5,
                    "baseline_mode": "adaptive", "tilt_correction": True, "placement": "typed",
                    "soft_edges": True, "smooth": 1.0}


class Project:
    def __init__(self, root, sources=None):
        self.root = os.path.abspath(root)
        os.makedirs(os.path.join(self.root, "cache"), exist_ok=True)
        os.makedirs(os.path.join(self.root, "out"), exist_ok=True)
        self._lock = threading.Lock()
        self.meta = self._read("project.json", {"sources": [], "pages": []})
        self.labels = self._read("labels.json", {"boxes": [], "next_id": 1})
        # font sets: one per typeface, so two fonts on the same pages never
        # get averaged together. A project from before sets becomes set 1.
        if not self.meta.get("sets"):
            old = {**DEFAULT_SETTINGS, **self.meta.pop("settings", {})}
            name = old.get("font_name") or os.path.basename(self.root.rstrip("/\\")) or "Font 1"
            self.meta["sets"] = [{"id": 1, "name": name, "settings": old}]
            self.meta["active_set"] = 1
            for b in self.labels["boxes"]:
                b.setdefault("set", 1)
        for st in self.meta["sets"]:
            st["settings"] = {**DEFAULT_SETTINGS, **st.get("settings", {})}
        if self.meta.get("active_set") not in [s["id"] for s in self.meta["sets"]]:
            self.meta["active_set"] = self.meta["sets"][0]["id"]
        # results written before font sets (loose in out/) belong to set 1
        out = os.path.join(self.root, "out")
        loose = [n for n in os.listdir(out) if n == "result.json" or n == "seeds" or n.endswith(".ttf")]
        if loose:
            dest = self.out_dir(self.meta["sets"][0]["id"])
            for n in loose:
                if not os.path.exists(os.path.join(dest, n)):
                    os.replace(os.path.join(out, n), os.path.join(dest, n))
            p = os.path.join(dest, "result.json")
            if os.path.isfile(p):
                with open(p, encoding="utf-8") as f:
                    r = json.load(f)
                r.setdefault("out", os.path.basename(dest))
                r.setdefault("set", self.meta["sets"][0]["id"])
                r.setdefault("set_name", self.meta["sets"][0]["name"])
                with open(p, "w", encoding="utf-8") as f:
                    json.dump(r, f, ensure_ascii=False)
        if sources:
            self.add_sources(sources)
        else:
            self.save()

    # ------------------------------------------------------------ files
    def _read(self, name, default):
        p = os.path.join(self.root, name)
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        return default

    def _write(self, name, data):
        p = os.path.join(self.root, name)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1, ensure_ascii=False)
        os.replace(tmp, p)

    def save(self):
        with self._lock:
            self._write("project.json", self.meta)
            self._write("labels.json", self.labels)

    # ------------------------------------------------------------ pages
    def add_sources(self, sources):
        """Each image file is a page (each frame of a multi-page TIFF), each
        PDF page a page. A folder adds every sample file in it, sorted."""
        files = []
        for s in sources:
            s = os.path.abspath(s)
            if os.path.isdir(s):
                for name in sorted(os.listdir(s)):
                    p = os.path.join(s, name)
                    if os.path.isfile(p) and os.path.splitext(name)[1].lower() in IMAGE_EXT | {".pdf"}:
                        files.append(p)
            elif os.path.isfile(s):
                files.append(s)
        known = {(p["file"], p.get("frame", 0), p.get("pdf_page")) for p in self.meta["pages"]}
        for f in files:
            if f not in self.meta["sources"]:
                self.meta["sources"].append(f)
            for page in _pages_of(f):
                key = (page["file"], page.get("frame", 0), page.get("pdf_page"))
                if key in known:
                    continue
                page["id"] = max([p["id"] for p in self.meta["pages"]], default=0) + 1
                self.meta["pages"].append(page)
                known.add(key)
        self.save()

    def add_upload(self, name, data):
        """A file uploaded through the page: kept in the project's uploads/
        folder (never overwriting one there), then added like any source."""
        d = os.path.join(self.root, "uploads")
        os.makedirs(d, exist_ok=True)
        base = "".join(c if c.isalnum() or c in "-_. ()" else "_" for c in os.path.basename(name)) or "upload"
        stem, ext = os.path.splitext(base)
        if ext.lower() not in IMAGE_EXT | {".pdf"}:
            raise ValueError(f"not a sample file ({ext or 'no extension'}) - PDF or an image")
        path, k = os.path.join(d, base), 1
        while os.path.exists(path):
            path = os.path.join(d, f"{stem} ({k}){ext}")
            k += 1
        with open(path, "wb") as f:
            f.write(data)
        before = len(self.meta["pages"])
        self.add_sources([path])
        return path, len(self.meta["pages"]) - before

    def page(self, pid):
        for p in self.meta["pages"]:
            if p["id"] == pid:
                return p
        raise KeyError(pid)

    @staticmethod
    def _orient_key(page):
        q, r = int(page.get("quarter", 0)) % 4, float(page.get("rotation", 0.0))
        return "" if not q and not r else f"_q{q}_r{r:+.2f}"

    def grey(self, pid):
        """The page as a uint8 grey array (memory-mapped from the cache),
        turned the way the page's quarter turns and rotation say."""
        page = self.page(pid)
        key = self._orient_key(page)
        path = os.path.join(self.root, "cache", f"page_{pid}{key}.npy")
        if not os.path.isfile(path):
            base = None if not key else self._base_grey(pid)       # turn the cached, unturned page
            with self._lock:
                if not os.path.isfile(path):
                    arr = _orient(np.asarray(base) if base is not None else _load_grey(page), page)
                    np.save(path + ".tmp.npy", arr)
                    os.replace(path + ".tmp.npy", path)
        return np.load(path, mmap_mode="r")

    def _base_grey(self, pid):
        """The page as scanned, unturned (kept cached: every orientation is
        made from it)."""
        path = os.path.join(self.root, "cache", f"page_{pid}.npy")
        if not os.path.isfile(path):
            with self._lock:
                if not os.path.isfile(path):
                    arr = _load_grey(self.page(pid))
                    np.save(path + ".tmp.npy", arr)
                    os.replace(path + ".tmp.npy", path)
        return np.load(path, mmap_mode="r")

    def set_page(self, pid, dpi=None, rotation=None, quarter=None):
        """A page's dpi (as the user knows it, when the file's is missing or
        wrong) and orientation: quarter turns clockwise and a fine rotation
        in degrees (clockwise +). The page's boxes move with it."""
        page = self.page(pid)
        if dpi is not None:
            page["dpi"] = float(dpi) if dpi else None
        if rotation is not None or quarter is not None:
            old = (int(page.get("quarter", 0)) % 4, float(page.get("rotation", 0.0)))
            new = (old[0] if quarter is None else int(quarter) % 4,
                   old[1] if rotation is None else max(-45.0, min(45.0, float(rotation))))
            if new != old:
                w0, h0 = page.get("w0", page["w"] if old[0] % 2 == 0 else page["h"]), page.get("h0", page["h"] if old[0] % 2 == 0 else page["w"])
                page["w0"], page["h0"] = w0, h0
                with self._lock:
                    for b in self.labels["boxes"]:
                        if b["page"] != pid:
                            continue
                        cx, cy = b["x"] + b["w"] / 2, b["y"] + b["h"] / 2
                        ox, oy = _to_original(cx, cy, w0, h0, *old)
                        nx, ny = _from_original(ox, oy, w0, h0, *new)
                        bw, bh = (b["h"], b["w"]) if (new[0] - old[0]) % 2 else (b["w"], b["h"])
                        b["x"], b["y"], b["w"], b["h"] = nx - bw / 2, ny - bh / 2, bw, bh
                        if b.get("baseline") is not None:   # a hand-set baseline can't follow a turn reliably
                            b["baseline"] = None
                page["quarter"], page["rotation"] = new
                page["w"], page["h"] = (w0, h0) if new[0] % 2 == 0 else (h0, w0)
                key = self._orient_key(page)
                for f in os.listdir(os.path.join(self.root, "cache")):     # drop this page's other turned copies
                    if f.startswith((f"page_{pid}_", f"preview_{pid}_")) and (not key or key not in f):
                        try:                                               # (the unturned page stays: all are made from it)
                            os.remove(os.path.join(self.root, "cache", f))
                        except OSError:
                            pass
        self.save()
        return page

    def preview(self, pid):
        page = self.page(pid)
        key = self._orient_key(page)
        path = os.path.join(self.root, "cache", f"preview_{pid}{key}.jpg")
        if not os.path.isfile(path):
            if key:
                # a turned page's preview: turn the small unturned preview -
                # instant, where turning a 100-megapixel scan takes seconds
                # (that happens only when the full page is needed)
                im = Image.open(self.preview_base(pid)).convert("L")
                im = Image.fromarray(_orient(np.asarray(im), page))
            else:
                im = Image.fromarray(np.asarray(self._base_grey(pid)))
                im.thumbnail((PREVIEW_MAX, PREVIEW_MAX), Image.LANCZOS)
            im.save(path + ".tmp.jpg", quality=85)
            os.replace(path + ".tmp.jpg", path)
        return path

    def preview_base(self, pid):
        path = os.path.join(self.root, "cache", f"preview_{pid}.jpg")
        if not os.path.isfile(path):
            im = Image.fromarray(np.asarray(self._base_grey(pid)))
            im.thumbnail((PREVIEW_MAX, PREVIEW_MAX), Image.LANCZOS)
            im.save(path + ".tmp.jpg", quality=85)
            os.replace(path + ".tmp.jpg", path)
        return path

    def region(self, pid, x, y, w, h, out_w):
        g = self.grey(pid)
        H, W = g.shape
        x0, y0 = max(0, int(x)), max(0, int(y))
        x1, y1 = min(W, int(x + w)), min(H, int(y + h))
        if x1 <= x0 or y1 <= y0:
            return Image.new("L", (1, 1), 255)
        im = Image.fromarray(np.asarray(g[y0:y1, x0:x1]))
        if out_w and im.width > out_w:
            im = im.resize((int(out_w), max(1, int(im.height * out_w / im.width))), Image.LANCZOS)
        return im

    # ------------------------------------------------------------ font sets
    def font_set(self, sid=None):
        sid = self.meta["active_set"] if sid is None else sid
        for s in self.meta["sets"]:
            if s["id"] == sid:
                return s
        return self.meta["sets"][0]

    def settings(self, sid=None):
        return self.font_set(sid)["settings"]

    def new_set(self, name):
        """A new, empty font set (settings copied from the current one, as a
        starting point) - and make it the one being labelled."""
        with self._lock:
            sid = max(s["id"] for s in self.meta["sets"]) + 1
            st = dict(self.settings())
            st["font_name"] = ""
            self.meta["sets"].append({"id": sid, "name": name or f"Font {sid}", "settings": st})
            self.meta["active_set"] = sid
        self.save()
        return self.font_set(sid)

    def rename_set(self, sid, name):
        self.font_set(sid)["name"] = name
        self.save()

    def activate_set(self, sid):
        self.meta["active_set"] = self.font_set(sid)["id"]
        self.save()

    def delete_set(self, sid):
        """Only an empty set can go (its boxes would be lost)."""
        if any(b.get("set", 1) == sid for b in self.labels["boxes"]):
            raise ValueError("this font set still has boxes - delete them first")
        if self.font_set(sid).get("images"):
            raise ValueError("this font set still has glyph images - delete them first")
        if len(self.meta["sets"]) == 1:
            raise ValueError("a project needs at least one font set")
        with self._lock:
            self.meta["sets"] = [s for s in self.meta["sets"] if s["id"] != sid]
            if self.meta["active_set"] == sid:
                self.meta["active_set"] = self.meta["sets"][0]["id"]
        self.save()

    def out_dir(self, sid=None):
        st = self.font_set(sid)
        safe = "".join(c if c.isalnum() or c in "-_ " else "_" for c in st["name"]).strip() or f"set{st['id']}"
        d = os.path.join(self.root, "out", f"{st['id']:02d} {safe}")
        os.makedirs(d, exist_ok=True)
        return d

    # ------------------------------------------------------------ boxes
    def box_list(self, sid=None):
        """A snapshot (boxes can be added or deleted by another request while
        this one is still reading) - of one font set when sid is given."""
        with self._lock:
            return [dict(b) for b in self.labels["boxes"] if sid is None or b.get("set", 1) == sid]

    def upsert_box(self, box):
        with self._lock:
            if not box.get("id"):
                box.setdefault("set", self.meta["active_set"])
                box["id"] = self.labels["next_id"]
                self.labels["next_id"] += 1
                box["created"] = time.time()
                self.labels["boxes"].append(box)
            else:
                for i, b in enumerate(self.labels["boxes"]):
                    if b["id"] == box["id"]:
                        self.labels["boxes"][i] = {**b, **box}
                        box = self.labels["boxes"][i]
                        break
        self.save()
        return box

    def delete_box(self, bid):
        with self._lock:
            self.labels["boxes"] = [b for b in self.labels["boxes"] if b["id"] != bid]
        self.save()


    # ------------------------------------------------------------ glyph images
    # A clean picture of a character (cleaned up or drawn, by hand or with an
    # AI image tool) that stands in for the scans' average: kept in
    # glyph_images/, listed per font set with its character and placement.
    def image_list(self, sid=None):
        with self._lock:
            return [dict(g) for g in self.font_set(sid).setdefault("images", [])]

    def image(self, iid):
        for st in self.meta["sets"]:
            for g in st.get("images", []):
                if g["id"] == iid:
                    return g
        raise KeyError(iid)

    def image_path(self, g):
        return os.path.join(self.root, "glyph_images", g["file"])

    def add_image(self, name, data, char=""):
        d = os.path.join(self.root, "glyph_images")
        os.makedirs(d, exist_ok=True)
        base = "".join(c if c.isalnum() or c in "-_. ()" else "_" for c in os.path.basename(name)) or "glyph.png"
        stem, ext = os.path.splitext(base)
        if ext.lower() not in IMAGE_EXT:
            raise ValueError(f"not an image ({ext or 'no extension'}) - PNG, JPG, BMP, TIFF, WebP")
        path, k = os.path.join(d, base), 1
        while os.path.exists(path):
            path = os.path.join(d, f"{stem} ({k}){ext}")
            k += 1
        with open(path, "wb") as f:
            f.write(data)
        try:
            with Image.open(path) as im:
                size = im.size
        except Exception as e:
            os.remove(path)
            raise ValueError(f"couldn't read the image: {e}")
        with self._lock:
            iid = self.meta.get("next_image_id", 1)
            self.meta["next_image_id"] = iid + 1
            g = {"id": iid, "file": os.path.basename(path), "name": name, "char": char, "w": size[0], "h": size[1],
                 "place": None, "enabled": True, "created": time.time()}
            self.font_set().setdefault("images", []).append(g)
        self.save()
        return g

    def update_image(self, iid, fields):
        with self._lock:
            g = self.image(iid)
            for k in ("char", "place", "enabled", "how"):
                if k in fields:
                    g[k] = fields[k]
        self.save()
        return dict(g)

    def delete_image(self, iid):
        with self._lock:
            g = self.image(iid)
            for st in self.meta["sets"]:
                st["images"] = [x for x in st.get("images", []) if x["id"] != iid]
        try:
            os.remove(self.image_path(g))
        except OSError:
            pass
        self.save()


def _pages_of(path):
    ext = os.path.splitext(path)[1].lower()
    name = os.path.basename(path)
    if ext == ".pdf":
        import pymupdf
        doc = pymupdf.open(path)
        out = []
        for i, pg in enumerate(doc):
            w = int(round(pg.rect.width / 72 * PDF_DPI))
            h = int(round(pg.rect.height / 72 * PDF_DPI))
            out.append({"file": path, "pdf_page": i, "name": f"{name} p{i + 1}", "w": w, "h": h, "dpi": PDF_DPI})
        return out
    im = Image.open(path)
    n = getattr(im, "n_frames", 1)
    out = []
    for fr in range(n):
        im.seek(fr)
        dpi = im.info.get("dpi", (0, 0))[0] or 0
        out.append({"file": path, "frame": fr, "name": name if n == 1 else f"{name} #{fr + 1}",
                    "w": im.width, "h": im.height, "dpi": float(dpi) if dpi and dpi > 72 else None})
    return out


def _orient(arr, page):
    """Quarter turns clockwise, then a fine rotation (degrees, clockwise +)
    about the centre, the page keeping its size; uncovered corners white."""
    q = int(page.get("quarter", 0)) % 4
    if q:
        arr = np.ascontiguousarray(np.rot90(arr, k=-q))
    r = float(page.get("rotation", 0.0))
    if r:   # PIL: positive = counter-clockwise; ours is clockwise +
        arr = np.asarray(Image.fromarray(arr).rotate(-r, resample=Image.BILINEAR, fillcolor=255))
    return arr


def _quarter_fwd(x, y, w, h, q):
    """A point after q clockwise quarter turns of a w x h image."""
    for _ in range(q % 4):
        x, y, w, h = h - y, x, h, w
    return x, y, w, h


def _from_original(x, y, w0, h0, q, r):
    x, y, w, h = _quarter_fwd(x, y, w0, h0, q)
    if r:
        t = np.radians(r)                       # clockwise on screen (y down)
        cx, cy = w / 2, h / 2
        dx, dy = x - cx, y - cy
        x, y = cx + dx * np.cos(t) - dy * np.sin(t), cy + dx * np.sin(t) + dy * np.cos(t)
    return float(x), float(y)


def _to_original(x, y, w0, h0, q, r):
    w, h = (w0, h0) if q % 2 == 0 else (h0, w0)
    if r:
        t = np.radians(-r)
        cx, cy = w / 2, h / 2
        dx, dy = x - cx, y - cy
        x, y = cx + dx * np.cos(t) - dy * np.sin(t), cy + dx * np.sin(t) + dy * np.cos(t)
    for _ in range(q % 4):                      # undo one clockwise turn: (x, y) in w x h -> (y, w - x)
        x, y, w, h = y, w - x, h, w
    return float(x), float(y)


def _load_grey(page):
    if page.get("pdf_page") is not None:
        import pymupdf
        doc = pymupdf.open(page["file"])
        pix = doc[page["pdf_page"]].get_pixmap(dpi=PDF_DPI, colorspace=pymupdf.csGRAY)
        return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width).copy()
    im = Image.open(page["file"])
    im.seek(page.get("frame", 0))
    if im.mode in ("I;16", "I;16B", "I;16L", "I"):
        arr = np.asarray(im, dtype=np.float32)
        arr = (arr - arr.min()) / max(1.0, float(arr.max() - arr.min())) * 255
        return arr.astype(np.uint8)
    return np.asarray(im.convert("L"), dtype=np.uint8).copy()
