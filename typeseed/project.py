"""A labelling project: the sample files (pages), the boxes drawn on them
and what glyph each holds, and a cache of every page as 8-bit grey.

project dir/
  project.json   sources, pages (file, frame or PDF page, size, dpi), settings
  labels.json    boxes: page, x y w h (page pixels), text, baseline override
  cache/         page_<n>.npy (grey, full resolution), preview_<n>.jpg
  out/           what Regenerate writes (font, seeds, averaged samples)
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
                    "weight": 1.0, "add": 0.0}


class Project:
    def __init__(self, root, sources=None):
        self.root = os.path.abspath(root)
        os.makedirs(os.path.join(self.root, "cache"), exist_ok=True)
        os.makedirs(os.path.join(self.root, "out"), exist_ok=True)
        self._lock = threading.Lock()
        self.meta = self._read("project.json", {"sources": [], "pages": [], "settings": dict(DEFAULT_SETTINGS)})
        self.meta["settings"] = {**DEFAULT_SETTINGS, **self.meta.get("settings", {})}
        self.labels = self._read("labels.json", {"boxes": [], "next_id": 1})
        if sources:
            self.add_sources(sources)

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

    # ------------------------------------------------------------ boxes
    def box_list(self):
        return self.labels["boxes"]

    def upsert_box(self, box):
        with self._lock:
            if not box.get("id"):
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
