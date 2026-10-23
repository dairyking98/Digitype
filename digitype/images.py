"""Glyph images: a clean picture of one character (cleaned up by hand or by
an AI image tool, or drawn from scratch) used in place of the scans' average.

The picture can be any size; where it sits in the font is kept as a
placement {x, y, s}: picture pixel (col, row) -> font units
X = x + col * s, Y = y - row * s (y up, the baseline at 0).

A new picture is placed automatically: onto the character's own average when
it has samples (the cleaned-up version of a glyph lands on top of the glyph),
otherwise where a reference monospaced font puts that character, scaled to
this face's cap height. Either way it can be moved and scaled by hand."""
import os

import numpy as np
from PIL import Image

CAPS = "HEFIKLMNTZ"           # flat-topped capitals: the cap height
SMALL = "xzvwnmru"            # no ascenders: the x-height
_REF = None


def load_ink(path):
    """-> ink amount 0..1 per pixel (dark on light, or opaque on transparent)."""
    im = Image.open(path)
    if im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info):
        rgba = np.asarray(im.convert("RGBA"), np.float32) / 255.0
        a = rgba[..., 3]
        if a.min() < 0.5:     # a cut-out: ink is what's opaque (and dark)
            return np.clip(a * (1.0 - rgba[..., :3].mean(axis=2)), 0, 1)
    grey = np.asarray(im.convert("L"), np.float32) / 255.0
    ink = 1.0 - grey
    # white on black: turn it round - judged by the border, where the paper
    # is (a tight crop of a bar or a blob can be mostly ink)
    edge = np.concatenate([ink[0], ink[-1], ink[:, 0], ink[:, -1]])
    if edge.mean() > 0.5:
        ink = 1.0 - ink
    # the paper isn't always pure white: stretch so its level is 0
    paper = float(np.percentile(ink, 10))
    if paper > 0.02:
        ink = np.clip((ink - paper) / max(1e-3, 1.0 - paper), 0, 1)
    return ink


def ink_bbox(ink, level=0.5):
    """-> (col0, row0, col1, row1) of the ink, pixel edges, or None."""
    rows = np.where((ink >= level).any(axis=1))[0]
    cols = np.where((ink >= level).any(axis=0))[0]
    if not len(rows):
        return None
    return float(cols[0]), float(rows[0]), float(cols[-1] + 1), float(rows[-1] + 1)


def _ref_font():
    global _REF
    if _REF is None:
        import matplotlib
        from fontTools.ttLib import TTFont
        _REF = TTFont(os.path.join(matplotlib.get_data_path(), "fonts", "ttf", "DejaVuSansMono.ttf"))
    return _REF


def _ref_bounds(ch):
    """Where DejaVu Sans Mono puts a character: (xmin, ymin, xmax, ymax), advance, cap height."""
    f = _ref_font()
    cmap = f.getBestCmap()
    gs = f.getGlyphSet()

    def bounds(c):
        from fontTools.pens.boundsPen import BoundsPen
        name = cmap.get(ord(c))
        if not name:
            return None
        pen = BoundsPen(gs)
        gs[name].draw(pen)
        return pen.bounds
    cap = bounds("H")[3]
    b = bounds(ch) if len(ch) == 1 else None
    return b, f["hmtx"][cmap[ord("H")]][0], cap


def face_metrics(result):
    """Cap height and x-height of the face from its last Regenerate (font units)."""
    chars = (result or {}).get("chars", {})

    def tops(cs):
        v = [chars[c]["bounds"][3] for c in cs if chars.get(c, {}).get("bounds")]
        return float(np.median(v)) if v else None
    return {"cap": tops(CAPS) or tops("ABDGOPRSUVWXY"), "x": tops(SMALL) or tops("aceos")}


def stroke_px(mask):
    """Typical stroke thickness of a black-and-white glyph, px: twice the
    median distance to the edge along its centre lines."""
    from scipy import ndimage
    from skimage.morphology import skeletonize
    if mask.sum() < 20:
        return None
    dt = ndimage.distance_transform_edt(np.pad(mask, 1))[1:-1, 1:-1]
    sk = skeletonize(mask)
    v = dt[sk]
    return float(2 * np.median(v)) if len(v) >= 5 else None


STEMS = "lHIinmhuEFTLtrkd"       # plain strokes: the face's stroke weight


def face_stroke(result):
    """The face's stroke thickness in font units, from the averaged scans
    of its plainest letters (median over them), or None."""
    import base64
    import io
    chars = (result or {}).get("chars", {})
    found = []
    for c in STEMS + "".join(sorted(chars)):
        g = chars.get(c) or {}
        if g.get("image") or not g.get("avg_png") or not g.get("avg_img"):
            continue
        im = Image.open(io.BytesIO(base64.b64decode(g["avg_png"].split(",", 1)[1])))
        a = np.asarray(im.convert("RGBA"))[..., 3] >= 128
        w = stroke_px(a)
        if w:
            found.append(w * g["avg_img"][2] / a.shape[1])     # px -> font units
        if len(found) >= 8:
            break
    return float(np.median(found)) if found else None


def auto_place(ink, ch, result, advance, upem):
    """-> placement {x, y, s} and how it was found ('average' or 'reference')."""
    bb = ink_bbox(ink)
    if bb is None:
        return {"x": 0.0, "y": upem * 0.7, "s": 1.0}, "empty"
    c0, r0, c1, r1 = bb
    iw, ih = c1 - c0, r1 - r0
    g = ((result or {}).get("chars") or {}).get(ch) or {}
    if g.get("avg_bounds"):              # the scans' own average (not a picture)
        tx0, ty0, tx1, ty1 = g["avg_bounds"]                      # font units, y up
        how = "average"
    else:
        rb, radv, rcap = _ref_bounds(ch)
        fm = face_metrics(result)
        cap = fm["cap"] or upem * 0.66
        k = cap / rcap
        mid = advance / 2.0
        if rb is None:                       # unknown character: centred on the x-height
            xh = fm["x"] or cap * 0.68
            tx0, tx1, ty0, ty1 = mid, mid, xh / 2.0, xh / 2.0
        else:                                # the reference glyph's box, scaled to this face
            tx0, tx1 = mid + (rb[0] - radv / 2.0) * k, mid + (rb[2] - radv / 2.0) * k
            ty0, ty1 = rb[1] * k, rb[3] * k
        how = "reference"
    tw, th = tx1 - tx0, ty1 - ty0
    # size by height - unless the glyph is small or flat (an accent, a dash,
    # an underscore) or unknown: there the box says little, so match the
    # picture's stroke thickness to the face's
    small = th < 0.4 * (face_metrics(result)["cap"] or upem * 0.66) or th < 0.3 * tw
    s = None
    if how != "average" and (small or th <= 0):
        fw, iws = face_stroke(result), stroke_px(ink >= 0.5)
        if fw and iws:
            s = fw / iws
    if s is None:
        s = th / ih if th >= 0.3 * tw else tw / iw
    cx, cy = (tx0 + tx1) / 2.0, (ty0 + ty1) / 2.0
    x = cx - (c0 + iw / 2.0) * s
    y = cy + (r0 + ih / 2.0) * s
    return {"x": round(x, 2), "y": round(y, 2), "s": round(s, 5)}, how


def working_prob(ink, place, advance, p=240.0):
    """The picture at the scans' working scale (p px per cell), as the
    averaged scans come out -> (prob, base_row, cell_centre_col, p)."""
    from scipy import ndimage
    k0 = advance / p                     # font units per working pixel
    z = place["s"] / k0
    m = np.clip(ndimage.zoom(ink, z, order=1), 0, 1) if abs(z - 1) > 1e-3 else ink.copy()
    # anti-aliased already; a little blur at big reductions keeps it from stepping
    if z < 0.5:
        m = ndimage.gaussian_filter(m, 0.35)
    pad = 6
    m = np.pad(m, pad)
    # X = x + col_orig * s = x + (col - pad) * k0  =>  cell centre col = (adv/2 - x)/k0 + pad
    cx = (advance / 2.0 - place["x"]) / k0 + pad
    base = place["y"] / k0 + pad         # Y = y - (row - pad) * k0 = 0
    return m.astype(np.float32), base, cx, p
