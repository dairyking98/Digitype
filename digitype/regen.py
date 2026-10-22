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
from . import images, island
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


_CELL_CACHE = {}


def box_cells(project, box):
    """[{char, x, y, w, h, baseline, auto}] in page pixels for one box -
    remembered until the box, its text or the pitch changes."""
    page = project.page(box["page"])
    key = (project.root, box.get("id"), box["page"], round(box["x"], 2), round(box["y"], 2), round(box["w"], 2),
           round(box["h"], 2), box.get("text"), box.get("baseline"), project.settings(box.get("set", 1))["pitch_mm"], page.get("dpi"),
           project.settings(box.get("set", 1)).get("baseline_mode", "adaptive"))
    hit = _CELL_CACHE.get(key)
    if hit is None:
        hit = _CELL_CACHE[key] = _box_cells(project, box)
        if len(_CELL_CACHE) > 20000:
            _CELL_CACHE.clear()
    return [dict(c) for c in hit]


def _box_cells(project, box):
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
        settings = project.settings(box.get("set", 1))
        p_set = pitch_px(project, page, settings)
        own = w / n
        pitch_note = ""
        if abs(own / p_set - 1) < 0.08:
            cw, spread = p_set, 0.02
        else:
            cw, spread = own, 0.06
            if page.get("dpi"):            # a page without a dpi has no millimetres to compare
                pitch_note = (f"{n} characters in this box make a pitch of {own / page['dpi'] * 25.4:.2f} mm, "
                              f"but this font's pitch is {settings['pitch_mm']} mm - check the text "
                              f"(a missing or extra space?) or the pitch setting")
        # where do the cell boundaries go? Between letters (little ink on
        # the boundary columns), with the text's spaces on blank paper -
        # the spaces are what tell "glyph in this cell" from "glyph in the
        # next one" when a table has a space after every letter
        prof = ndimage.uniform_filter1d(ink.sum(axis=0).astype(float), 3)
        # the row's own rhythm: glyph columns repeat every pitch, so the
        # autocorrelation of the column profile peaks there - accurate even
        # when letters touch and there are no spaces to go by, where a small
        # pitch error would add up to a whole cell by the end of the line
        rp = _rhythm_pitch(prof, cw)
        if rp is not None:
            cw, spread = rp, 0.012
        cum = np.concatenate([[0], np.cumsum(prof)])
        spaces = [i for i, c in enumerate(chars) if c.isspace()]
        letters = [i for i, c in enumerate(chars) if not c.isspace()]

        def inside(i, o, p):
            a = int(np.clip(o + (i + 0.2) * p, 0, len(prof)))
            b = int(np.clip(o + (i + 0.8) * p, 0, len(prof)))
            return cum[b] - cum[a]

        best = (float("inf"), 0.0, cw)
        best_b = (float("inf"), 0.0, cw)          # boundaries in the gaps only - for the text fit below
        for p in np.linspace(cw * (1 - spread), cw * (1 + spread), 21):
            for o in np.linspace(-0.6 * p, 0.6 * p, 49):
                xs = (o + p * np.arange(n + 1)).round().astype(int)
                xs = xs[(xs >= 0) & (xs < len(prof))]
                score = prof[xs].sum() if len(xs) else 0.0
                gx = (o + p * np.arange(int((w - o) // p) + 2)).round().astype(int)
                gx = gx[(gx >= 0) & (gx < len(prof))]
                gscore = prof[gx].mean() if len(gx) else 0.0
                if gscore < best_b[0]:
                    best_b = (gscore, o, p)
                score += sum(inside(i, o, p) for i in spaces)                       # ink where a space should be
                score += sum(prof.max() * 3 for i in letters if inside(i, o, p) == 0 and chars[i] not in ".,'`´·")
                if score < best[0]:
                    best = (score, o, p)
        _, o, p = best
        cells = [(c, o + i * p, p) for i, c in enumerate(chars)]
        # fit the text to the glyphs actually on the grid: each character in
        # order onto a cell, letters onto ink, allowing an extra blank cell
        # where the typist left a gap or one fewer where the label has an
        # extra space - one missing space would otherwise put every cell
        # after it one letter off
        _, ob, pb = best_b
        # glyphs first: find each glyph from the gaps in the ink and match
        # the text to them in order - follows rows whose spacing changes
        # along the line (set or pasted rather than typed in one go); the
        # even grid is the fallback when the glyphs can't be told apart
        fitted, fixes = _fit_text_to_glyphs(chars, prof, pb, w)
        if fitted is None:
            fitted, fixes = _fit_text_to_grid(chars, prof, ob, pb, w)
        if fitted is not None:
            o, p = ob, pb
        fit_note = ""
        if fitted is not None:
            cells = fitted
            if fixes:
                fit_note = ("spacing adjusted to match the ink: " + "; ".join(fixes[:4]) + ("…" if len(fixes) > 4 else "")
                        + " - fix the text if it's wrong")
        # the ink, first inked column to last, spans the trimmed text: between
        # (n - 1) cells and n cells (the last glyph is narrower than a cell)
        cols = np.nonzero(ink.any(axis=0))[0]
        n_trim = len(text.strip())
        if fitted is None and len(cols) and n_trim > 1:
            span = (cols.max() - cols.min() + 1) / p
            if span > n_trim + 0.15 or span < n_trim - 1.05:
                warn = (f"the ink spans about {span:.1f} cells but the text has {n_trim} characters "
                        f"(not counting spaces at the ends) - check for a missing or extra space")
        # spaces should land on blank paper and letters on ink - the most
        # useful check, as it says where (the text fit above already did it)
        if fitted is None:
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
        if not warn:
            warn = pitch_note
        if fitted is not None:                 # say what the fit had to change; nothing else to report
            warn = fit_note
    # a cell that clearly missed its glyph (a chart whose glyphs are grouped,
    # not evenly spaced) snaps onto the nearest glyph; small offsets stay -
    # they are the face's own placement in its cell
    blobs, core_ink, core_full = [], {}, 0.0
    if n > 1:
        colink = ink.sum(axis=0).astype(float)
        colany = colink > 0
        cw0 = cells[0][2]
        gap = max(1, int(0.08 * cw0))
        run = None
        for i, v in enumerate(np.concatenate([colany, [False] * (gap + 1)])):
            if v:
                run = [i, i] if run is None else [run[0], i]
            elif run is not None and i - run[1] > gap:
                if run[1] - run[0] + 1 <= 1.3 * cw0:          # one glyph, not letters run together
                    blobs.append((run[0] + run[1] + 1) / 2)
                run = None
        for c, cx, cw in cells:                                # ink in the middle 40% of each cell
            a, b = int(max(0, cx + 0.3 * cw)), int(min(w, cx + 0.7 * cw))
            core_ink[cx] = colink[a:b].sum() if b > a else 0.0
        letters_core = [core_ink[cx] for c, cx, cw in cells if not c.isspace()]
        core_full = float(np.median(letters_core)) if letters_core else 0.0
    out = []
    for c, cx, cw in cells:
        if c.isspace():
            continue
        snapped = False
        if blobs and core_ink.get(cx, 0) < 0.12 * core_full and c not in ".,'`´·:;-_":   # the cell missed its glyph
            cc = cx + cw / 2
            near = min(blobs, key=lambda bc: abs(bc - cc))
            # ...and the glyph is nearer this cell than any other (in a
            # dense row it belongs to the neighbour)
            closest = min((ox + ow / 2 for oc, ox, ow in cells if not oc.isspace()), key=lambda m: abs(m - near))
            if 0.25 * cw < abs(near - cc) < 0.75 * cw and abs(closest - cc) < 1e-6:
                cx += near - cc
                snapped = True
        a, b = int(max(0, cx)), int(min(w, cx + cw))
        sub = ink[:, a:b]
        rows = np.nonzero(sub.any(axis=1))[0]
        base = None
        if len(rows) and c in ON_BASELINE:
            base = y + rows.max() + 1.0
        out.append({"char": c, "box": box.get("id"), "x": x + cx, "y": y, "w": cw, "h": h, "baseline": base,
                    "has_ink": bool(len(rows)), "thr": thr, "warn": warn, "snapped": snapped,
                    "unit": float(cw) if n > 1 else None})     # px per cell: the scale this sample was typed at
    mode = project.settings(box.get("set", 1)).get("baseline_mode", "adaptive")
    pts = [(c["x"] + c["w"] / 2, c["baseline"]) for c in out if c["baseline"] is not None]
    for c in out:
        c["auto"] = True
        c["tilt"] = 0.0
        if box.get("baseline") is not None:
            c["baseline"], c["auto"] = float(box["baseline"]), False
        elif pts and (c["baseline"] is None or n > 1):
            # a run of text: the baseline follows the line - one straight
            # (possibly tilted) line, or adaptive: a local fit over the
            # letters nearby, for a line that waves or drifts
            span = 8 * (c["w"] or 1) if mode == "adaptive" else None
            c["baseline"], c["tilt"] = baseline_at(pts, c["x"] + c["w"] / 2, span)
    return out


LIGHT_CHARS = set(".,'`´·:;-_\"")


def _glyph_blobs(prof, p):
    """Where the glyphs are along a row: runs of inked columns, with runs a
    hair apart joined (the two ticks of a quote) and runs clearly wider
    than a letter split at their thinnest columns. -> [(centre, weight)]"""
    if prof.max() <= 0:
        return []
    on = prof > 0.04 * prof.max()
    runs, a = [], None
    for i, v in enumerate(np.concatenate([on, [False]])):
        if v and a is None:
            a = i
        elif not v and a is not None:
            runs.append([a, i])
            a = None
    merged = []
    for r in runs:
        if merged and r[0] - merged[-1][1] < 0.12 * p and r[1] - merged[-1][0] <= 1.15 * p:
            merged[-1][1] = r[1]
        else:
            merged.append(list(r))
    blobs = []
    for a, b in merged:
        wdt = b - a
        k = int(round(wdt / p)) if wdt > 1.4 * p else 1
        cuts = [a]
        for i in range(1, k):
            guess = a + i * wdt / k
            lo, hi = int(guess - 0.25 * p), int(guess + 0.25 * p)
            lo, hi = max(lo, cuts[-1] + 1), min(hi, b - 1)
            cuts.append(lo + int(np.argmin(prof[lo:hi])) if hi > lo else int(guess))
        cuts.append(b)
        for x0, x1 in zip(cuts[:-1], cuts[1:]):
            if x1 - x0 >= 2:
                blobs.append(((x0 + x1) / 2, float(prof[x0:x1].sum())))
    return blobs


def _fit_text_to_glyphs(chars, prof, p, w, c_gap=1.0, c_skip=1.5, c_miss=3.0):
    """Match the text's characters, in order, to the glyphs found along the
    row (dynamic programming): each letter takes one glyph, centred on it;
    a glyph no letter takes costs c_skip (by its weight), a letter with no
    glyph c_miss, and spacing unlike the text's c_gap per cell of
    difference. -> (cells, notes) or (None, []) when it doesn't fit."""
    blobs = _glyph_blobs(prof, p)
    letters = [i for i, c in enumerate(chars) if not c.isspace()]
    L, B = len(letters), len(blobs)
    if not L or B < 0.7 * L or B > 1.5 * L + 2:
        return None, []
    wmed = np.median([b[1] for b in blobs]) or 1.0
    skip = [min(1.0, b[1] / wmed) * c_skip for b in blobs]
    INF = float("inf")
    # state: letter k matched to glyph j (or j = -1 = no glyph, position unknown)
    cost = np.full((L, B), INF)
    back = np.full((L, B), -1)
    pre = np.concatenate([[0], np.cumsum(skip)])
    for j in range(B):
        cost[0, j] = pre[j]
    for k in range(1, L):
        want = letters[k] - letters[k - 1]             # cells between the two letters, as the text says
        for j in range(k, B):
            best, arg = INF, -1
            for jp in range(max(k - 1, j - want - 3), j):
                if cost[k - 1, jp] == INF:
                    continue
                d = (blobs[j][0] - blobs[jp][0]) / p
                v = cost[k - 1, jp] + (pre[j] - pre[jp + 1]) + c_gap * min(3.0, abs(d - want))
                if v < best:
                    best, arg = v, jp
            cost[k, j] = best
            back[k, j] = arg
    end = [cost[L - 1, j] + (pre[B] - pre[j + 1]) for j in range(B)]
    j = int(np.argmin(end))
    if end[j] == INF or end[j] > c_miss * L * 0.35:
        return None, []
    at = [0] * L
    for k in range(L - 1, -1, -1):
        at[k] = j
        j = back[k, j]
    fixes, cells = [], []
    for k, i in enumerate(letters):
        cells.append((chars[i], blobs[at[k]][0] - p / 2, p))
        if k + 1 < L:
            d = round((blobs[at[k + 1]][0] - blobs[at[k]][0]) / p) - (letters[k + 1] - i)
            if d:
                ctx = "".join(chars[max(0, i - 3):i + 1]).strip()
                fixes.append(f"{'+' if d > 0 else ''}{d} space{'s' if abs(d) > 1 else ''} after \"{ctx}\"")
            for sidx in range(1, letters[k + 1] - i):    # the text's own spaces, spread between
                x = blobs[at[k]][0] + (blobs[at[k + 1]][0] - blobs[at[k]][0]) * sidx / (letters[k + 1] - i)
                cells.append((chars[i + sidx], x - p / 2, p))
    return cells, fixes


def _fit_text_to_grid(chars, prof, o, p, w, a_blank=3.0, b_skip=3.0, c_gap=0.6):
    """Place the text's characters on the cells of the grid (offset o,
    pitch p): dynamic programming over (character, cell), in order. Costs:
    a letter on blank paper (a_blank), an inked cell no character takes
    (b_skip), and each cell more or fewer between two characters than the
    text's spaces say (c_gap). -> ([(char, x, width)] for every character,
    spaces placed between, [notes on adjusted spacing]) or (None, [])."""
    ncell = int((w - o) // p) + 1
    if ncell < 2:
        return None, []
    cum = np.concatenate([[0], np.cumsum(prof)])
    def core(j):
        a = int(np.clip(o + (j + 0.25) * p, 0, len(prof)))
        b = int(np.clip(o + (j + 0.75) * p, 0, len(prof)))
        return cum[b] - cum[a]
    ink = np.array([core(j) for j in range(ncell)], float)
    full = np.median(ink[ink > 0.2 * ink.max()]) if (ink > 0).any() else 1.0
    ink = np.clip(ink / (full or 1.0), 0, 1)
    letters = [i for i, c in enumerate(chars) if not c.isspace()]
    if not letters or len(letters) > ncell:
        return None, []
    gaps = [letters[k + 1] - letters[k] - 1 for k in range(len(letters) - 1)]     # spaces the text puts between
    L, INF = len(letters), float("inf")
    place = lambda k, j: (a_blank * (0.3 if chars[letters[k]] in LIGHT_CHARS else 1.0)) * (1 - ink[j])
    skipc = np.concatenate([[0], np.cumsum(ink * b_skip)])                         # cost of leaving cells i..j-1 empty
    cost = np.full((L, ncell), INF)
    back = np.zeros((L, ncell), int)
    for j in range(ncell):
        cost[0, j] = skipc[j] + place(0, j)
    for k in range(1, L):
        g = gaps[k - 1]
        for j in range(k, ncell):
            best, arg = INF, -1
            for jp in range(max(k - 1, j - g - 4), j):
                if cost[k - 1, jp] == INF:
                    continue
                v = cost[k - 1, jp] + (skipc[j] - skipc[jp + 1]) + c_gap * abs((j - jp - 1) - g)
                if v < best:
                    best, arg = v, jp
            if arg >= 0:
                cost[k, j] = best + place(k, j)
                back[k, j] = arg
    end = [cost[L - 1, j] + (skipc[ncell] - skipc[j + 1]) for j in range(ncell)]
    j = int(np.argmin(end))
    if end[j] == INF:
        return None, []
    at = [0] * L
    for k in range(L - 1, -1, -1):
        at[k] = j
        j = back[k, j]
    fixes = []
    for k in range(L - 1):
        d = (at[k + 1] - at[k] - 1) - gaps[k]
        if d:
            ctx = "".join(chars[max(0, letters[k] - 3):letters[k] + 1]).strip()
            fixes.append(f"{'+' if d > 0 else ''}{d} space{'s' if abs(d) > 1 else ''} after \"{ctx}\"")
    out = []
    for k, i in enumerate(letters):
        out.append((chars[i], o + at[k] * p, p))
        if k + 1 < L:                                  # the text's spaces between, for completeness
            for sidx in range(1, letters[k + 1] - i):
                out.append((chars[i + sidx], o + (at[k] + min(sidx, max(1, at[k + 1] - at[k] - 1))) * p, p))
    return out, fixes


def _rhythm_pitch(prof, guess, rng=0.12):
    """The pitch at which the column profile repeats, near `guess` (+-12%),
    from its autocorrelation, refined between samples; None when the row
    shows no clear rhythm."""
    x = prof - prof.mean()
    n = len(x)
    lo, hi = int(guess * (1 - rng)), int(np.ceil(guess * (1 + rng)))
    if hi + 2 >= n // 2 or lo < 3:
        return None
    f = np.fft.rfft(x, 2 * n)
    ac = np.fft.irfft(f * np.conj(f))[:n]
    if ac[0] <= 0:
        return None
    seg = ac[lo:hi + 1]
    k = int(np.argmax(seg)) + lo
    if ac[k] / ac[0] < 0.15 or k in (lo, hi):
        return None
    a, b, c = ac[k - 1], ac[k], ac[k + 1]
    den = a - 2 * b + c
    return float(k + (0.5 * (a - c) / den if den else 0.0))


def _theil_sen(pts):
    """Robust straight line through (x, y) points: the median of pairwise
    slopes, then the median intercept - one bad letter can't tip it."""
    xs, ys = np.array([p[0] for p in pts]), np.array([p[1] for p in pts])
    if len(xs) < 2 or np.ptp(xs) < 1e-6:
        return 0.0, float(np.median(ys))
    i, j = np.triu_indices(len(xs), 1)
    dx = xs[j] - xs[i]
    ok = np.abs(dx) > 1e-6
    slope = float(np.median((ys[j] - ys[i])[ok] / dx[ok])) if ok.any() else 0.0
    return slope, float(np.median(ys - slope * xs))


def baseline_at(pts, x, span=None, max_tilt=0.15):
    """(baseline y, tilt in radians) at x, from (x, y) baseline points: a
    robust line through all of them, or through those within `span` of x
    (adaptive). With fewer than 3 points to go on: flat, at their median."""
    near = pts if span is None else [p for p in pts if abs(p[0] - x) <= span]
    if len(near) < 3:
        near = sorted(pts, key=lambda p: abs(p[0] - x))[:max(3, len(near))]
    if len(near) < 3:
        return float(np.median([p[1] for p in near])), 0.0
    slope, icpt = _theil_sen(near)
    slope = float(np.clip(slope, -max_tilt, max_tilt))
    return slope * x + icpt, float(np.arctan(slope))


def all_cells(project, with_pages=False, sid=None):
    """Cells of every box; a cell still without a baseline borrows the
    median baseline of boxes on its page that overlap it vertically."""
    snapshot = project.box_list(sid)
    per_box = {b["id"]: box_cells(project, b) for b in snapshot}
    boxes = {b["id"]: b for b in snapshot}
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
                    near += [(x["x"] + x["w"] / 2, x["baseline"]) for x in oc if x["baseline"] is not None and not x.get("borrowed")]
            if near:
                mode = project.settings(b.get("set", 1)).get("baseline_mode", "adaptive")
                c["baseline"], c["tilt"] = baseline_at(near, c["x"] + c["w"] / 2, 8 * c["w"] if mode == "adaptive" else None)
            c["borrowed"] = True
    # a single-glyph box doesn't show the pitch: take it from runs on the
    # same page, else from the pitch setting at the page's dpi
    run_units = {}
    for bid, cells in per_box.items():
        for c in cells:
            if c.get("unit"):
                run_units.setdefault(boxes[bid]["page"], []).append(c["unit"])
    for bid, cells in per_box.items():
        b = boxes[bid]
        for c in cells:
            if not c.get("unit"):
                us = run_units.get(b["page"])
                c["unit"] = float(np.median(us)) if us else pitch_px(project, project.page(b["page"]), project.settings(b.get("set", 1)))
    if with_pages:
        return per_box, {bid: b["page"] for bid, b in boxes.items()}
    return per_box


# ------------------------------------------------------------------ pitch
def pitch_px(project, page, settings):
    dpi = page.get("dpi") or 600.0
    return settings["pitch_mm"] / 25.4 * dpi


def estimate_pitch_mm(project, sid=None):
    """From runs of text: box width / characters, in mm at each page's dpi."""
    vals = []
    for b in project.box_list(project.meta["active_set"] if sid is None else sid):
        t = b.get("text") or ""
        if len(t) >= 4:
            dpi = project.page(b["page"]).get("dpi") or 600.0
            vals.append(b["w"] / len(t) / dpi * 25.4)
    return float(np.median(vals)) if vals else None


# ------------------------------------------------------------------ averaging
def _sample(project, cell, page, settings):
    """One cell's ink. The capture window is the cell widened by
    expand_left / expand_right (fractions of the cell's width) - ink there
    counts when its piece reaches the middle of the cell, so a wide letter
    spilling past its cell is kept and a neighbour that only reaches in
    isn't. -> (mask float, base_row, cx_col) | None"""
    if not cell["has_ink"] or cell["baseline"] is None:
        return None
    g = project.grey(page["id"])
    w = cell["w"]
    el = max(0.0, float(settings.get("expand_left", 0.5)))
    er = max(0.0, float(settings.get("expand_right", 0.5)))
    cx = cell["x"] + w / 2
    x0, x1 = int(cell["x"] - el * w), int(np.ceil(cell["x"] + w + er * w))
    # vertically: at least from two cells above the baseline to most of a
    # cell below, whatever the box - a snug box round a line of capitals
    # would otherwise cut the dots off Ä and Ü
    u0 = float(cell.get("unit") or w)
    y0 = int(min(cell["y"], cell["baseline"] - 2.0 * u0))
    y1 = int(max(cell["y"] + cell["h"], cell["baseline"] + 0.9 * u0))
    x0c = max(0, x0)
    crop = np.asarray(g[max(0, y0):y1, x0c:x1]).astype(np.float32)
    if crop.size == 0:
        return None
    ink = _ink(crop, cell["thr"])
    # keep only ink that belongs to this cell: pieces reaching its core
    # which pieces of ink belong to this cell: a piece whose centre falls in
    # the cell and that reaches the band just above its baseline (the body
    # of the letter), plus separate pieces whose centre sits in the cell a
    # little above or below it (the dot of an i, umlauts, the circles of %,
    # the top of = and :). The line above or below, and the next letter,
    # have their centres elsewhere.
    lab, n = ndimage.label(ink, structure=np.ones((3, 3)))
    u = float(cell.get("unit") or w)
    br0 = cell["baseline"] - max(0, y0)
    cell_a, cell_b = cell["x"] - x0c, cell["x"] - x0c + w
    keep, floating, loose = set(), set(), set()
    if n:
        idx = np.arange(1, n + 1)
        cents = ndimage.center_of_mass(ink, lab, idx)
        sizes = ndimage.sum(ink, lab, idx)
        objs = ndimage.find_objects(lab)
        min_size = max(2.0, (0.03 * u) ** 2)
        for k, ((cr, ccol), size, sl) in enumerate(zip(cents, sizes, objs), start=1):
            if size < min_size:
                continue
            inside = cell_a - 0.1 * u <= ccol <= cell_b + 0.1 * u
            top, bot = sl[0].start, sl[0].stop
            reaches_band = top <= br0 + 0.05 * u and bot >= br0 - 0.6 * u
            if inside and reaches_band:
                keep.add(k)
            elif (inside and br0 - 1.5 * u <= cr <= br0 - 0.1 * u
                  and sl[0].stop - sl[0].start <= 0.6 * u and sl[1].stop - sl[1].start <= 0.7 * u
                  and sl[0].start > 0 and sl[0].stop < ink.shape[0]):
                floating.add(k)                # small, whole and above the line: maybe a dot or an accent
            if (inside and br0 - 1.6 * u <= cr <= br0 - 0.1 * u
                    and sl[0].stop - sl[0].start <= 1.0 * u and sl[1].stop - sl[1].start <= 0.9 * u
                    and sl[0].start > 0 and sl[0].stop < ink.shape[0]):
                loose.add(k)                   # for a glyph that is only marks (' " ` ^): a little larger
            elif sl[1].stop - sl[1].start > 1.6 * u and reaches_band and sl[1].start < cell_b and sl[1].stop > cell_a:
                keep.add(k)                    # letters run together into one piece: trimmed to the cell below
    if keep:
        # a separate mark belongs to this letter only when it sits over the
        # letter's own width and close above its top (i, j, umlauts, accents,
        # the circles of %, the top of :) - a dot from the line below, or a
        # stop from the line above, doesn't
        bx0 = min(objs[k - 1][1].start for k in keep)
        bx1 = max(objs[k - 1][1].stop for k in keep)
        btop = min(objs[k - 1][0].start for k in keep)
        for k in list(floating):
            sl = objs[k - 1]
            ccol = cents[k - 1][1]
            if not (bx0 - 0.15 * u <= ccol <= bx1 + 0.15 * u) or btop - sl[0].stop > 0.6 * u:
                floating.discard(k)
    if not keep:
        floating = loose                       # no body at all (' " ` ^): the marks are the glyph
    keep |= floating
    wide = {k for k in keep if objs[k - 1][1].stop - objs[k - 1][1].start > 1.25 * u}
    mask = np.isin(lab, list(keep)).astype(np.float32)
    # letters touching: a piece that reaches into the next cell's middle
    # carries part of that letter - cut it at the thinnest column near the
    # cell's edge (W's overhang doesn't reach that far, a neighbour does)
    for k in keep:
        sl = objs[k - 1]
        piece = lab == k
        colsum = piece.sum(axis=0)
        for side in ("right", "left"):
            edge = cell_b if side == "right" else cell_a
            beyond = edge + 0.3 * u if side == "right" else edge - 0.3 * u
            reach = sl[1].stop > beyond if side == "right" else sl[1].start < beyond
            if not reach:
                continue
            a = int(max(0, edge - 0.2 * u)) if side == "right" else int(max(0, edge - 0.3 * u))
            b = int(min(len(colsum), edge + 0.3 * u)) if side == "right" else int(min(len(colsum), edge + 0.2 * u))
            if b <= a:
                continue
            cut = a + int(np.argmin(colsum[a:b]))
            if side == "right":
                mask[:, cut:][piece[:, cut:]] = 0
            else:
                mask[:, :cut + 1][piece[:, :cut + 1]] = 0
    if wide:                                   # a piece wider than a letter is two letters touching:
        lo, hi = int(max(0, cell_a - 0.15 * u)), int(cell_b + 0.15 * u)   # keep only this cell's part
        trim = np.isin(lab, list(wide))
        trim[:, lo:hi] = False
        mask[trim] = 0
    if not mask.any():
        return None
    # keep the scan's grey edge instead of a hard black/white cut: how dark
    # each pixel is pins the true edge down to a fraction of a pixel, so even
    # a single sample traces smooth. 0 = paper, 1 = full ink, only on and
    # around the pieces kept above.
    if settings.get("soft_edges", True):
        paper = float(np.percentile(crop, 90))
        inkv = float(np.percentile(crop[mask > 0], 10)) if (mask > 0).any() else float(crop.min())
        if paper - inkv > 10:
            soft = np.clip((paper - crop) / (paper - inkv), 0, 1)
            near = ndimage.binary_dilation(mask > 0, iterations=2)
            mask = (soft * near).astype(np.float32)
    br, bc = cell["baseline"] - max(0, y0), cx - x0c
    t = float(cell.get("tilt") or 0.0)
    if settings.get("tilt_correction", True) and abs(t) > 0.002:
        # level the glyph: turn it about its baseline point by the line's tilt
        cs, sn = np.cos(t), np.sin(t)
        m = np.array([[cs, sn], [-sn, cs]])
        ctr = np.array([br, bc])
        mask = ndimage.affine_transform(mask, m, offset=ctr - m @ ctr, order=1)
    return mask, br, bc, {"box": cell.get("box"), "w": w, "unit": float(cell.get("unit") or w)}


def average(samples, pitch, max_shift_frac=0.12):
    """Line samples up (cell centre, baseline), register each to the median
    with sub-pixel phase correlation, then compare each with the majority
    shape: with 3 or more samples, one that clearly disagrees (most often a
    label mistake - an a that is really an a-umlaut, a letter one cell off)
    is left out. -> (prob map, base_row, cx, n used, [odd sample info])"""
    # scans of one face at different sizes: bring every sample to the same
    # px per cell (the largest, capped) before anything is compared
    units = [s[3]["unit"] for s in samples if len(s) > 3 and s[3].get("unit")]
    target = min(max(units), 240.0) if units else float(pitch)
    scaled = []
    for s in samples:
        f = target / s[3]["unit"] if len(s) > 3 and s[3].get("unit") else 1.0
        if abs(f - 1) > 0.01:
            m = np.clip(ndimage.zoom(s[0], f, order=1), 0, 1)
            scaled.append((m, s[1] * f, s[2] * f, {**s[3], "w": s[3]["w"] * f, "orig_unit": s[3]["unit"]}))
        else:
            scaled.append((s[0], s[1], s[2], {**(s[3] if len(s) > 3 else {}), "orig_unit": target}))
    samples = scaled
    pitch = target
    above = max(s[1] for s in samples)
    below = max(s[0].shape[0] - s[1] for s in samples)
    left = max(s[2] for s in samples)                    # room for the widest sample on each side
    right = max(s[0].shape[1] - s[2] for s in samples)
    H = int(np.ceil(above + below)) + 4
    W = int(np.ceil(left + right)) + 4
    base, cx = above + 2, left + 2
    stack = []
    for m, br, scx, *_ in samples:
        canvas = np.zeros((H, W), np.float32)
        dy, dx = base - br, cx - scx
        iy, ix = int(np.floor(dy)), int(np.floor(dx))
        ys, xs = max(0, iy), max(0, ix)
        ye, xe = min(H, iy + m.shape[0]), min(W, ix + m.shape[1])
        canvas[ys:ye, xs:xe] = m[ys - iy:ye - iy, xs - ix:xe - ix]
        canvas = ndimage.shift(canvas, (dy - iy, dx - ix), order=1)
        stack.append(canvas)
    stack = np.array(stack)
    # a sharp scan counts for more than a small one: weight = its own px per
    # cell over the largest (thin strokes of a low-resolution scan threshold
    # away and would outvote the good ones)
    wts = np.array([(s[3].get("orig_unit", target) / target) if len(s) > 3 else 1.0 for s in samples])
    odd = []
    if len(stack) >= 2:                      # register: overlap the samples as well as they go
        ref = np.median(stack, axis=0)
        # sideways a cell split can drift by up to half a cell along a line,
        # so the samples line themselves up by up to that; up and down only a
        # little (the baseline is measured)
        cellw = float(np.median([s[3]["w"] for s in samples if len(s) > 3] or [pitch]))
        for _ in range(2):
            for i in range(len(stack)):
                # against the OTHER samples only: matched against an average
                # that includes itself, a sample finds its own copy and
                # never moves (with two samples, nothing would line up)
                others = [j for j in range(len(stack)) if j != i]
                ref = np.median(stack[others], axis=0) if len(others) >= 3 else _wmean(stack[others], wts[others])
                (dy, dx), _, _ = phase_cross_correlation(ref, stack[i], upsample_factor=8, normalization=None)
                if abs(dx) <= 0.5 * cellw and abs(dy) <= max_shift_frac * cellw:
                    stack[i] = ndimage.shift(stack[i], (dy, dx), order=1)
        major = _wmean(stack, wts) >= 0.49
        ious = []
        for i in range(len(stack)):
            m = stack[i] >= 0.5
            u = (m | major).sum()
            ious.append(float((m & major).sum() / u) if u else 0.0)
        med = float(np.median(ious))
        cut = med - 0.15
        keep = [i for i, v in enumerate(ious) if v >= cut]
        # leaving one out needs 3 or more to say which one is odd
        if len(stack) >= 3 and len(keep) >= max(2, len(stack) // 2) and len(keep) < len(stack):
            for i, v in enumerate(ious):
                if v < cut:
                    info = samples[i][3] if len(samples[i]) > 3 else {}
                    odd.append({"box": info.get("box"), "match": round(v, 3), "typical": round(med, 3)})
            stack, wts = stack[keep], wts[keep]
    return _wmean(stack, wts), base, cx, len(stack), odd, target


def _wmean(stack, wts):
    return np.tensordot(wts / wts.sum(), stack, axes=1)


# ------------------------------------------------------------------ geometry
def _trace(prob, level=0.49):
    """The half-way contour of a probability map as one shape (px, y down) -
    an exact tie (one of two samples) counts as ink."""
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


def regenerate(project, chars=None, log=print, sid=None):
    """One font set (the one being labelled, by default) -> its fonts."""
    sid = project.meta["active_set"] if sid is None else sid
    fs = project.font_set(sid)
    st = fs["settings"]
    log(f"Font set: {fs['name']}")
    per_box, pages = all_cells(project, with_pages=True, sid=sid)
    by_char = {}
    for bid, cells in per_box.items():
        page = project.page(pages[bid])
        for c in cells:
            by_char.setdefault(c["char"], []).append((c, page))
    out_dir = project.out_dir(sid)
    os.makedirs(os.path.join(out_dir, "seeds"), exist_ok=True)
    results, seed_glyphs, avg_glyphs = {}, {}, {}
    adv, upem = st["advance"], st["upem"]
    # glyph images (cleaned-up or drawn pictures) stand in for the scans
    pics = {g["char"]: g for g in project.image_list(sid) if g.get("enabled", True) and g.get("char") and g.get("place")}
    known = set(by_char) | set(pics)
    todo = sorted(known) if not chars else [c for c in chars if c in known]
    for n_done, ch in enumerate(todo, 1):
        res = None
        items = by_char.get(ch, [])
        if items:
            page0 = items[0][1]
            p = pitch_px(project, page0, st)
            samples = [s for s in (_sample(project, c, pg, st) for c, pg in items) if s is not None]
            skipped = len(items) - len(samples)
            log(f"[{n_done}/{len(todo)}] {ch!r}: {len(samples)} samples" + (f" ({skipped} without ink or baseline)" if skipped else ""))
            if not samples:
                results[ch] = {"n": 0, "error": "no usable samples"}
            else:
                try:
                    res = _one_glyph(ch, samples, p, page0, st, out_dir, skipped, log)
                except Exception as e:                 # one bad glyph shouldn't stop the font
                    import traceback
                    log(f"    {ch!r} failed: {e}\n" + traceback.format_exc(limit=3))
                    results[ch] = {"n": len(samples), "error": f"failed: {e}"}
        if ch in pics:
            if not items:
                log(f"[{n_done}/{len(todo)}] {ch!r}: glyph image")
            try:
                res = _image_glyph(project, ch, pics[ch], res, st, out_dir, log)
            except Exception as e:
                import traceback
                log(f"    {ch!r} glyph image failed: {e} - " + ("the scans' average is used" if res else "left out")
                    + "\n" + traceback.format_exc(limit=3))
        if res is None:
            continue
        results[ch], grown_u, avg_u = res
        if grown_u is not None:
            seed_glyphs[ch] = grown_u
        if avg_u is not None:
            avg_glyphs[ch] = avg_u
    return _finish(project, fs, results, seed_glyphs, avg_glyphs, out_dir, adv, upem, log)


def _one_glyph(ch, samples, p, page0, st, out_dir, skipped=0, log=print):
    """Average one character's samples, trace it, seed it, grow it back.
    -> (result for the page, regrown shape, traced shape), font units. If
    only the seed step fails, the traced average still goes in the font."""
    prob, base, cx, n, odd, p = average(samples, p)      # p: the common px per cell
    if odd:
        log(f"    {len(odd)} sample{'s' if len(odd) > 1 else ''} of {ch!r} left out - unlike the others "
            f"(box {', '.join(str(o['box']) for o in odd)}): check the label")
    return _from_prob(ch, prob, base, cx, n, odd, p, page0.get("dpi"), st, out_dir, skipped, log)


def _image_glyph(project, ch, pic, from_scans, st, out_dir, log=print):
    """A glyph image in place of the scans' average, where it was placed.
    The scans' own result (if any) rides along for the page to compare."""
    ink = images.load_ink(project.image_path(pic))
    prob, base, cx, p = images.working_prob(ink, pic["place"], st["advance"])
    res, grown_u, avg_u = _from_prob(ch, prob, base, cx, 0, [], p, None, st, out_dir, 0, log, picture=True)
    res["image"] = {"id": pic["id"], "file": pic["file"], "name": pic.get("name")}
    if from_scans is not None:
        r0 = from_scans[0]
        res["n"], res["skipped"] = r0["n"], r0.get("skipped", 0)
        res["scans"] = {k: r0.get(k) for k in ("avg_png", "avg_img", "avg_svg", "avg_bounds", "n")}
        res["avg_bounds"] = r0.get("avg_bounds")      # so a re-placed picture still lands on the scans
    else:
        res["avg_bounds"] = None
    return res, grown_u, avg_u


def _from_prob(ch, prob, base, cx, n, odd, p, dpi, st, out_dir, skipped=0, log=print, picture=False):
    """A glyph's ink map (averaged scans, or a picture) -> traced outline,
    seed and regrown outline. picture: a clean picture already placed by
    hand - no extra smoothing, no re-centring."""
    adv, upem, up = st["advance"], st["upem"], int(st["upsample"])
    # smoothing that fades as samples are added: averaging n samples
    # smooths an edge on its own, so few samples get a little more blur -
    # 'smooth' scales it (0 = off); measured in px at the common scale
    sm = 0.0 if picture else float(st.get("smooth", 1.0))
    if sm > 0:
        sigma = sm * 0.012 * p / np.sqrt(max(1, n))
        if sigma > 0.2:
            prob = ndimage.gaussian_filter(prob, sigma)
    hi = np.clip(ndimage.zoom(prob, up, order=3), 0, 1)
    k = adv / (p * up)                                   # font units per upsampled pixel
    avg_shape = _trace(hi)
    avg_u = _to_units(avg_shape, k, cx * up, base * up, adv).simplify(1.0) if avg_shape is not None else None
    grown_u, seed, seed_error = None, None, None
    ink = clean(hi >= 0.49)
    if ink.any():
        try:
            isl = island.sink(ink)
            island.prune(isl)
            grid = Grid(adv / 2 - cx * up * k, base * up * k, 1.0 / k, ink.shape)
            seed = from_island(isl, grid, ch, adv, upem)
            seed.meta = {"samples": n, "pitch_mm": st["pitch_mm"], "dpi": dpi, **({"source": "image"} if picture else {})}
            grown = G.grow(seed, weight=st["weight"], add=st["add"])
            grown_u = grown.simplify(1.5) if grown is not None else None
            with open(os.path.join(out_dir, "seeds", f"seed_{ord(ch):04x}.json"), "w", encoding="utf-8") as f:
                f.write(seed.to_json())
        except Exception as e:
            import traceback
            seed_error = f"seed failed: {e}"
            log(f"    {ch!r} {seed_error} (its traced average is still in the font)\n" + traceback.format_exc(limit=3))
    buf = io.BytesIO()
    rgba = np.zeros(prob.shape + (4,), np.uint8)               # black ink, the paper transparent
    rgba[..., 3] = (np.clip(prob, 0, 1) * 255).astype(np.uint8)
    Image.fromarray(rgba, "RGBA").save(buf, "PNG")
    # where the averaged image sits in font units (SVG, y down): pixel
    # (col, row) -> x = (col - cx) * k0 + adv/2, y = (row - base) * k0
    k0 = adv / p
    H, W = prob.shape
    # placement: as typed (where the glyph sat in its typewriter cell), or
    # centred - the ink's middle (traced outline) on the middle of the cell
    dx = 0.0
    ref = avg_u if avg_u is not None and not avg_u.is_empty else grown_u
    if st.get("placement") == "center" and not picture and ref is not None and not ref.is_empty:
        # centre on the letter's own ink: pieces under 3% of it (a speck, a
        # stray dot) don't get a say
        parts = list(getattr(ref, "geoms", [ref]))
        total = sum(q.area for q in parts) or 1.0
        main = [q for q in parts if q.area >= 0.03 * total] or parts
        bs = [q.bounds for q in main]
        b0 = (min(b[0] for b in bs), 0, max(b[2] for b in bs), 0)
        dx = adv / 2 - (b0[0] + b0[2]) / 2
        from shapely.affinity import translate
        avg_u = translate(avg_u, dx) if avg_u is not None else None
        grown_u = translate(grown_u, dx) if grown_u is not None else None
    shapes = [g for g in (grown_u, avg_u) if g is not None and not g.is_empty]
    if shapes:
        bx = [g.bounds for g in shapes]
        bounds = [min(b[0] for b in bx), min(b[1] for b in bx), max(b[2] for b in bx), max(b[3] for b in bx)]
    else:
        bounds = None
    result = {
        "n": n, "skipped": skipped, "bounds": bounds, "odd": odd,
        "avg_img": [round(adv / 2 - cx * k0 + dx, 1), round(-base * k0, 1), round(W * k0, 1), round(H * k0, 1)],
        "avg_png": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode(),
        "seed_svg": _svg(grown_u), "avg_svg": _svg(avg_u),
        "strokes": [[(x + dx, y) for x, y in s_.points] for s_ in seed.strokes] if seed else [],
        "shift": round(dx, 1),
        "avg_bounds": [round(v, 1) for v in avg_u.bounds] if avg_u is not None and not avg_u.is_empty and not picture else None,
        "iou": round(G.iou(grown_u, avg_u), 3) if grown_u is not None and avg_u is not None else None,
    }
    if seed_error:
        result["warning"] = seed_error
    return result, grown_u, avg_u


def _finish(project, fs, results, seed_glyphs, avg_glyphs, out_dir, adv, upem, log):
    st = fs["settings"]
    name = st.get("font_name") or fs["name"] or "Digitype"
    fonts = {}
    for label, glyphs in (("Seed", seed_glyphs), ("Average", avg_glyphs)):
        if glyphs:
            path = os.path.join(out_dir, f"{_safe(name)}-{label}.ttf")
            write_font(path, f"{name} {label}", glyphs, upem, adv)
            fonts[label.lower()] = os.path.basename(path)
            log(f"wrote {path}")
    # one frame for every glyph (font units, y up), so they compare at one scale
    bs = [r["bounds"] for r in results.values() if r.get("bounds")]
    extent = ([min(0.0, min(b[0] for b in bs)), min(b[1] for b in bs), max(float(adv), max(b[2] for b in bs)), max(b[3] for b in bs)]
              if bs else [0.0, -0.25 * upem, float(adv), 0.8 * upem])
    summary = {"chars": results, "fonts": fonts, "advance": adv, "upem": upem, "set": fs["id"], "set_name": fs["name"],
               "extent": [round(v, 1) for v in extent],
               "out": os.path.basename(out_dir),
               "pitch_mm": st["pitch_mm"], "estimated_pitch_mm": estimate_pitch_mm(project, fs["id"])}
    with open(os.path.join(out_dir, "result.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False)
    return summary


def _safe(s):
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in s).strip("_") or "Digitype"


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
    # Windows' installer wants the full set: family, style, unique id, full
    # name, version and an ASCII PostScript name without spaces
    import re
    import time as _time
    ps = re.sub(r"[^A-Za-z0-9-]", "", family.replace(" ", "-"))[:58] or "Digitype"
    fb.setupNameTable({"familyName": family, "styleName": "Regular",
                       "uniqueFontIdentifier": f"Digitype: {family}: {_time.strftime('%Y-%m-%d %H:%M')}",
                       "fullName": f"{family} Regular", "version": "Version 1.000",
                       "psName": f"{ps}-Regular"})
    from fontTools.ttLib.tables.O_S_2f_2 import Panose
    pan = Panose()
    for k in ("bFamilyType", "bSerifStyle", "bWeight", "bProportion", "bContrast", "bStrokeVariation",
              "bArmStyle", "bLetterForm", "bMidline", "bXHeight"):
        setattr(pan, k, 0)
    pan.bFamilyType, pan.bProportion = 2, 9          # Latin text, monospaced - what font menus check
    fb.setupOS2(sTypoAscender=asc, sTypoDescender=desc, usWinAscent=asc, usWinDescent=-desc,
                xAvgCharWidth=advance, panose=pan)
    fb.setupPost(isFixedPitch=1)
    fb.save(path)
