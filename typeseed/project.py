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

    def grey(self, pid):
        """The page as a uint8 grey array (memory-mapped from the cache)."""
        path = os.path.join(self.root, "cache", f"page_{pid}.npy")
        if not os.path.isfile(path):
            with self._lock:
                if not os.path.isfile(path):
                    arr = _load_grey(self.page(pid))
                    np.save(path + ".tmp.npy", arr)
                    os.replace(path + ".tmp.npy", path)
        return np.load(path, mmap_mode="r")

    def preview(self, pid):
        path = os.path.join(self.root, "cache", f"preview_{pid}.jpg")
        if not os.path.isfile(path):
            g = self.grey(pid)
            im = Image.fromarray(np.asarray(g))
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
