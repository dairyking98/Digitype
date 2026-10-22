"""From labelled boxes to a font.

1. Cells: a box holding one glyph is one cell; a box labelled with several
   characters is a run of fixed-pitch cells, the split nudged so its
   boundaries fall in the gaps between letters.
2. Baselines: a glyph that sits on the line (most letters, figures, . ! ?)
   gives the baseline as the bottom of its ink; the rest (g j p q y , ; ( )
   _ ' " ...) take it from their line, or from boxes beside them on the
   page. A baseline dragged by hand always wins.
3. Averaging: every sample of a character is thresholded, lined up on its
   cell centre and baseline, nudged into register with the others, and
   averaged - the dirt is different on every impression, the letter isn't.
4. Seed: the average (upsampled for smooth edges) sinks to a seed and is
   grown back (island.py, seed.py, grow.py).
5. Font: both the regrown letters and the plain traced averages are
   written as fixed-pitch TrueType fonts, every glyph sitting on its
   baseline in a cell of the sample's pitch.
"""
import base64
import io
import json
import os
from functools import reduce

import numpy as np
from PIL import Image
from scipy import ndimage
from shapely.geometry import Polygon
from shapely.geometry.polygon import orient
from skimage import filters, measure, morphology
from skimage.registration import phase_cross_correlation

from . import grow as G
from . import island
from .raster import Grid, clean
from .seed import from_island

ON_BASELINE = set("ABCDEFGHIKLMNOPRSTUVWXYZabcdefhiklmnorstuvwxz0123456789.!?:%&#ÄÖÜäöüßÆæØøÅåÉéÈèÊêËëÀàÂâÎîÏïÔôÛûÙùÇ")


# ------------------------------------------------------------------ cells
def _threshold(gray):
    g = np.asarray(gray, dtype=np.float32)
    if g.size == 0 or g.max() - g.min() < 20:
        return None
    return float(filters.threshold_otsu(g))


def _ink(gray, thr):
    ink = np.asarray(gray) < thr
    return morphology.remove_small_objects(ink, max_size=max(3, ink.size // 4000))


def box_cells(project, box):
    """[{char, x, y, w, h, baseline, auto}] in page pixels for one box."""
    text = box.get("text") or ""
    g = project.grey(box["page"])
    x, y, w, h = (int(round(box[k])) for k in ("x", "y", "w", "h"))
    crop = np.asarray(g[max(0, y):y + h, max(0, x):x + w])
    thr = _threshold(crop)
    if thr is None or not text:
        return []
    ink = _ink(crop, thr)
    chars = list(text)
    n = len(chars)
    cells = []
    warn = ""
    if n == 1:
        cells.append((chars[0], 0.0, float(w)))
    else:
        # the typewriter's pitch is fixed: split at the project's pitch when
        # the box agrees with it, and say so when the count can't fit
        page = project.page(box["page"])
        p_set = pitch_px(project, page, project.meta["settings"])
        own = w / n
        if abs(own / p_set - 1) < 0.08:
            cw, spread = p_set, 0.02
        else:
            cw, spread = own, 0.06
            warn = (f"{n} characters in this box make a pitch of {own / (page.get('dpi') or 600) * 25.4:.2f} mm, "
                    f"but the project's pitch is {project.meta['settings']['pitch_mm']} mm - check the text "
                    f"(a missing or extra space?) or the pitch setting")
        # where do the cell boundaries go? Between letters (little ink on
        # the boundary columns), with the text's spaces on blank paper -
        # the spaces are what tell "glyph in this cell" from "glyph in the
        # next one" when a table has a space after every letter
        prof = ndimage.uniform_filter1d(ink.sum(axis=0).astype(float), 3)
        cum = np.concatenate([[0], np.cumsum(prof)])
        spaces = [i for i, c in enumerate(chars) if c.isspace()]
        letters = [i for i, c in enumerate(chars) if not c.isspace()]

        def inside(i, o, p):
            a = int(np.clip(o + (i + 0.2) * p, 0, len(prof)))
            b = int(np.clip(o + (i + 0.8) * p, 0, len(prof)))
            return cum[b] - cum[a]

        best = (float("inf"), 0.0, cw)
        for p in np.linspace(cw * (1 - spread), cw * (1 + spread), 21):
            for o in np.linspace(-0.6 * p, 0.6 * p, 49):
                xs = (o + p * np.arange(n + 1)).round().astype(int)
                xs = xs[(xs >= 0) & (xs < len(prof))]
                score = prof[xs].sum() if len(xs) else 0.0
                score += sum(inside(i, o, p) for i in spaces)                       # ink where a space should be
                score += sum(prof.max() * 3 for i in letters if inside(i, o, p) == 0 and chars[i] not in ".,'`´·")
                if score < best[0]:
                    best = (score, o, p)
        _, o, p = best
        cells = [(c, o + i * p, p) for i, c in enumerate(chars)]
        # the ink, first inked column to last, spans the trimmed text: between
        # (n - 1) cells and n cells (the last glyph is narrower than a cell)
        cols = np.nonzero(ink.any(axis=0))[0]
        n_trim = len(text.strip())
        if not warn and len(cols) and n_trim > 1:
            span = (cols.max() - cols.min() + 1) / p
            if span > n_trim + 0.15 or span < n_trim - 1.05:
                warn = (f"the ink spans about {span:.1f} cells but the text has {n_trim} characters "
                        f"(not counting spaces at the ends) - check for a missing or extra space")
        # spaces should land on blank paper and letters on ink
        if not warn:
            colink = ink.sum(axis=0)
            full = np.median([colink[int(max(0, o + i * p)):int(min(w, o + (i + 1) * p))].sum()
                              for i, c in enumerate(chars) if not c.isspace()] or [1])
            bad = []
            for i, c in enumerate(chars):
                a, b = int(max(0, o + i * p + 0.2 * p)), int(min(w, o + (i + 1) * p - 0.2 * p))
                amount = colink[a:b].sum() if b > a else 0
                if c.isspace() and amount > 0.25 * full or (not c.isspace() and c not in ".,'`´·" and amount == 0):
                    bad.append(i)
            if len(bad) >= 2:
                i = bad[0]
                warn = (f"{len(bad)} cells don't match the text (spaces over ink, or letters over blank paper), "
                        f"starting near \"{text[max(0, i - 6):i + 6]}\" - a missing or extra character before there?")
    out = []
    for c, cx, cw in cells:
        if c.isspace():
            continue
        a, b = int(max(0, cx)), int(min(w, cx + cw))
        sub = ink[:, a:b]
        rows = np.nonzero(sub.any(axis=1))[0]
        base = None
        if len(rows) and c in ON_BASELINE:
            base = y + rows.max() + 1.0
        out.append({"char": c, "x": x + cx, "y": y, "w": cw, "h": h, "baseline": base,
                    "has_ink": bool(len(rows)), "thr": thr, "warn": warn})
    known = [c["baseline"] for c in out if c["baseline"] is not None]
    line = float(np.median(known)) if known else None
    for c in out:
        c["auto"] = True
        if box.get("baseline") is not None:
            c["baseline"], c["auto"] = float(box["baseline"]), False
        elif c["baseline"] is None or (line is not None and n > 1):
            c["baseline"] = line          # a run of text: one line, one baseline
    return out


def all_cells(project):
    """Cells of every box; a cell still without a baseline borrows the
    median baseline of boxes on its page that overlap it vertically."""
    per_box = {b["id"]: box_cells(project, b) for b in project.box_list()}
    boxes = {b["id"]: b for b in project.box_list()}
    for bid, cells in per_box.items():
        b = boxes[bid]
        for c in cells:
            if c["baseline"] is not None:
                continue
            near = []
            for oid, oc in per_box.items():
                o = boxes[oid]
                if oid == bid or o["page"] != b["page"]:
                    continue
                ov = min(b["y"] + b["h"], o["y"] + o["h"]) - max(b["y"], o["y"])
                if ov > 0.5 * min(b["h"], o["h"]):
                    near += [x["baseline"] for x in oc if x["baseline"] is not None and not x.get("borrowed")]
            c["baseline"] = float(np.median(near)) if near else None
            c["borrowed"] = True
    return per_box


# ------------------------------------------------------------------ pitch
def pitch_px(project, page, settings):
    dpi = page.get("dpi") or 600.0
    return settings["pitch_mm"] / 25.4 * dpi


def estimate_pitch_mm(project):
    """From runs of text: box width / characters, in mm at each page's dpi."""
    vals = []
    for b in project.box_list():
        t = b.get("text") or ""
        if len(t) >= 4:
            dpi = project.page(b["page"]).get("dpi") or 600.0
            vals.append(b["w"] / len(t) / dpi * 25.4)
    return float(np.median(vals)) if vals else None


# ------------------------------------------------------------------ averaging
def _sample(project, cell, page, settings):
    """One cell's ink, in a frame 2 pitches wide centred on the cell, with
    the baseline at a known row. -> (mask float, base_row, cx_col) | None"""
    if not cell["has_ink"] or cell["baseline"] is None:
        return None
    g = project.grey(page["id"])
    p = pitch_px(project, page, settings)
    cx = cell["x"] + cell["w"] / 2
    x0, x1 = int(cx - p), int(cx + p)
    y0, y1 = int(cell["y"]), int(cell["y"] + cell["h"])
    crop = np.asarray(g[max(0, y0):y1, max(0, x0):x1]).astype(np.float32)
    if crop.size == 0:
        return None
    ink = _ink(crop, cell["thr"])
    # keep only ink that belongs to this cell: components reaching its core
    lab, n = ndimage.label(ink, structure=np.ones((3, 3)))
    core_a = int(cell["x"] - max(0, x0) + 0.15 * cell["w"])
    core_b = int(cell["x"] - max(0, x0) + 0.85 * cell["w"])
    keep = set(np.unique(lab[:, max(0, core_a):max(core_a + 1, core_b)])) - {0}
    mask = np.isin(lab, list(keep))
    if not mask.any():
        return None
    return mask.astype(np.float32), cell["baseline"] - max(0, y0), cx - max(0, x0)


def average(samples, pitch, max_shift_frac=0.12):
    """Line samples up (cell centre, baseline), register each to the median
    with sub-pixel phase correlation, average. -> (prob map, base_row, cx)"""
    above = max(s[1] for s in samples)
    below = max(s[0].shape[0] - s[1] for s in samples)
    H = int(np.ceil(above + below)) + 4
    W = int(np.ceil(2 * pitch)) + 4
    base, cx = above + 2, W / 2
    stack = []
    for m, br, scx in samples:
        canvas = np.zeros((H, W), np.float32)
        dy, dx = base - br, cx - scx
        iy, ix = int(np.floor(dy)), int(np.floor(dx))
        ys, xs = max(0, iy), max(0, ix)
        ye, xe = min(H, iy + m.shape[0]), min(W, ix + m.shape[1])
        canvas[ys:ye, xs:xe] = m[ys - iy:ye - iy, xs - ix:xe - ix]
        canvas = ndimage.shift(canvas, (dy - iy, dx - ix), order=1)
        stack.append(canvas)
    stack = np.array(stack)
    if len(stack) >= 3:
        ref = np.median(stack, axis=0)
        lim = max_shift_frac * pitch
        for i in range(len(stack)):
            shift, _, _ = phase_cross_correlation(ref, stack[i], upsample_factor=8, normalization=None)
            if np.hypot(*shift) <= lim:
                stack[i] = ndimage.shift(stack[i], shift, order=1)
    return stack.mean(axis=0), base, cx, len(stack)


# ------------------------------------------------------------------ geometry
def _trace(prob, level=0.5):
    """The 0.5 contour of a probability map as one shape (px, y down)."""
    cs = measure.find_contours(np.pad(prob, 1), level)
    polys = [Polygon(np.column_stack([c[:, 1] - 1, c[:, 0] - 1])).buffer(0) for c in cs if len(c) >= 4]
    polys = [p for p in polys if not p.is_empty and p.area > 1]
    if not polys:
        return None
    return reduce(lambda a, b: a.symmetric_difference(b), polys)


def _to_units(geom, k, cx, base, advance):
    from shapely.affinity import affine_transform
    # x_u = (x - cx) * k + advance/2 ; y_u = (base - y) * k
    return affine_transform(geom, [k, 0, 0, -k, advance / 2 - cx * k, base * k])


def regenerate(project, chars=None, log=print):
    st = project.meta["settings"]
    per_box = all_cells(project)
    by_char = {}
    for bid, cells in per_box.items():
        page = project.page(next(b for b in project.box_list() if b["id"] == bid)["page"])
        for c in cells:
            by_char.setdefault(c["char"], []).append((c, page))
    out_dir = os.path.join(project.root, "out")
    os.makedirs(os.path.join(out_dir, "seeds"), exist_ok=True)
    results, seed_glyphs, avg_glyphs = {}, {}, {}
    adv, upem, up = st["advance"], st["upem"], int(st["upsample"])
    todo = sorted(by_char) if not chars else [c for c in chars if c in by_char]
    for n_done, ch in enumerate(todo, 1):
        items = by_char[ch]
        page0 = items[0][1]
        p = pitch_px(project, page0, st)
        samples = [s for s in (_sample(project, c, pg, st) for c, pg in items) if s is not None]
        skipped = len(items) - len(samples)
        log(f"[{n_done}/{len(todo)}] {ch!r}: {len(samples)} samples" + (f" ({skipped} without ink or baseline)" if skipped else ""))
        if not samples:
            results[ch] = {"n": 0, "error": "no usable samples"}
            continue
        prob, base, cx, n = average(samples, p)
        hi = np.clip(ndimage.zoom(prob, up, order=1), 0, 1)
        k = adv / (p * up)                                   # font units per upsampled pixel
        ink = clean(hi > 0.5)
        avg_shape = _trace(hi)
        grown = None
        seed = None
        if ink.any():
            isl = island.sink(ink)
            island.prune(isl)
            grid = Grid(adv / 2 - cx * up * k, base * up * k, 1.0 / k, ink.shape)
            seed = from_island(isl, grid, ch, adv, upem)
            seed.meta = {"samples": n, "pitch_mm": st["pitch_mm"], "dpi": page0.get("dpi")}
            grown = G.grow(seed, weight=st["weight"], add=st["add"])
            with open(os.path.join(out_dir, "seeds", f"seed_{ord(ch):04x}.json"), "w", encoding="utf-8") as f:
                f.write(seed.to_json())
        avg_u = _to_units(avg_shape, k, cx * up, base * up, adv) if avg_shape is not None else None
        if grown is not None:
            seed_glyphs[ch] = grown.simplify(1.5)
        if avg_u is not None:
            avg_glyphs[ch] = avg_u.simplify(1.0)
        buf = io.BytesIO()
        Image.fromarray(((1 - prob) * 255).astype(np.uint8)).save(buf, "PNG")
        results[ch] = {
            "n": n, "skipped": skipped,
            "avg_png": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode(),
            "seed_svg": _svg(seed_glyphs.get(ch)), "avg_svg": _svg(avg_glyphs.get(ch)),
            "strokes": [st_.points for st_ in seed.strokes] if seed else [],
            "iou": round(G.iou(seed_glyphs.get(ch), avg_glyphs.get(ch)), 3) if ch in seed_glyphs and ch in avg_glyphs else None,
        }
    name = st.get("font_name") or os.path.basename(project.root.rstrip("/\\")) or "Type Seed"
    fonts = {}
    for label, glyphs in (("Seed", seed_glyphs), ("Average", avg_glyphs)):
        if glyphs:
            path = os.path.join(out_dir, f"{_safe(name)}-{label}.ttf")
            write_font(path, f"{name} {label}", glyphs, upem, adv)
            fonts[label.lower()] = os.path.basename(path)
            log(f"wrote {path}")
    summary = {"chars": results, "fonts": fonts, "advance": adv, "upem": upem,
               "pitch_mm": st["pitch_mm"], "estimated_pitch_mm": estimate_pitch_mm(project)}
    with open(os.path.join(out_dir, "result.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False)
    return summary


def _safe(s):
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in s).strip("_") or "TypeSeed"


def _svg(geom):
    if geom is None or geom.is_empty:
        return ""
    d = []
    for poly in getattr(geom, "geoms", [geom]):
        for ring in [poly.exterior, *poly.interiors]:
            pts = list(ring.coords)[:-1]
            if len(pts) >= 3:
                d.append("M" + "L".join(f"{x:.1f} {y:.1f}" for x, y in pts) + "Z")
    return "".join(d)


# ------------------------------------------------------------------ font
def write_font(path, family, glyphs, upem, advance):
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    order = [".notdef", "space"] + [f"uni{ord(c):04X}" for c in sorted(glyphs)]
    fb = FontBuilder(upem, isTTF=True)
    fb.setupGlyphOrder(order)
    cmap = {32: "space", **{ord(c): f"uni{ord(c):04X}" for c in glyphs}}
    fb.setupCharacterMap(cmap)
    drawn, metrics = {}, {}
    ymin, ymax = 0, 0
    for name in order:
        pen = TTGlyphPen(None)
        ch = chr(int(name[3:], 16)) if name.startswith("uni") else None
        geom = glyphs.get(ch) if ch else None
        if geom is not None and not geom.is_empty:
            for poly in getattr(geom, "geoms", [geom]):
                poly = orient(poly, -1.0)                 # TrueType: outer contours clockwise
                for ring in [poly.exterior, *poly.interiors]:
                    pts = [(int(round(x)), int(round(y))) for x, y in list(ring.coords)[:-1]]
                    pts = [pt for i, pt in enumerate(pts) if pt != pts[i - 1]]
                    if len(pts) < 3:
                        continue
                    pen.moveTo(pts[0])
                    for pt in pts[1:]:
                        pen.lineTo(pt)
                    pen.closePath()
            minx, miny, maxx, maxy = geom.bounds
            ymin, ymax = min(ymin, miny), max(ymax, maxy)
            lsb = int(round(minx))
        else:
            lsb = 0
        drawn[name] = pen.glyph()
        metrics[name] = (advance, lsb)
    fb.setupGlyf(drawn)
    fb.setupHorizontalMetrics(metrics)
    asc, desc = int(max(ymax, upem * 0.75)), int(min(ymin, -upem * 0.25))
    fb.setupHorizontalHeader(ascent=asc, descent=desc)
    fb.setupNameTable({"familyName": family, "styleName": "Regular"})
    fb.setupOS2(sTypoAscender=asc, sTypoDescender=desc, usWinAscent=asc, usWinDescent=-desc, xAvgCharWidth=advance)
    fb.setupPost(isFixedPitch=1)
    fb.save(path)
