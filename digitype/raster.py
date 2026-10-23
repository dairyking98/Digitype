"""Between shapes and pixels: rasterizing a glyph, dirtying it like a worn
typewriter impression on a bad scan, and the first clean-up pass."""
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage
from skimage import morphology


@dataclass
class Grid:
    """Maps font units <-> pixels: col = (x - x0) * scale, row = (y0 - y) * scale."""
    x0: float
    y0: float
    scale: float          # pixels per font unit
    shape: tuple          # (rows, cols)

    def to_px(self, x, y):
        return (np.asarray(x) - self.x0) * self.scale, (self.y0 - np.asarray(y)) * self.scale

    def to_units(self, col, row):
        return self.x0 + np.asarray(col) / self.scale, self.y0 - np.asarray(row) / self.scale


def grid_for(shape, px_per_em, upem, margin=0.15):
    minx, miny, maxx, maxy = shape.bounds
    pad = margin * upem
    scale = px_per_em / upem
    cols = int(np.ceil((maxx - minx + 2 * pad) * scale))
    rows = int(np.ceil((maxy - miny + 2 * pad) * scale))
    return Grid(minx - pad, maxy + pad, scale, (rows, cols))


def rasterize(shape, grid):
    img = Image.new("1", (grid.shape[1], grid.shape[0]), 0)
    d = ImageDraw.Draw(img)
    for poly in getattr(shape, "geoms", [shape]):
        c, r = grid.to_px(*poly.exterior.xy)
        d.polygon(list(zip(c, r)), fill=1)
        for hole in poly.interiors:
            c, r = grid.to_px(*hole.xy)
            d.polygon(list(zip(c, r)), fill=0)
    return np.array(img, dtype=bool)


def dirty(ink, rng, spread_px=2.0, rough=1.2, speckle=0.0015, blobs=3, breaks=2, holes=0.0008):
    """A worn impression on a poor scan: ink spread, ragged edges, speckle,
    ink blobs, small breaks and pinholes. All sizes in pixels."""
    h, w = ink.shape
    d = ndimage.distance_transform_edt(~ink) - ndimage.distance_transform_edt(ink)   # signed, + outside
    noise = ndimage.gaussian_filter(rng.standard_normal(ink.shape), 2.0)
    noise *= rough / (noise.std() + 1e-9)
    out = d < spread_px + noise * 2.5
    yy, xx = np.mgrid[0:h, 0:w]
    ys, xs = np.nonzero(out)
    for _ in range(blobs):                                   # ink blobs on the strokes
        i = rng.integers(len(ys))
        r = rng.uniform(2, 5) * (1 + spread_px / 3)
        out |= (yy - ys[i]) ** 2 + (xx - xs[i]) ** 2 < r * r
    for _ in range(breaks):                                  # the type didn't quite hit
        i = rng.integers(len(ys))
        r = rng.uniform(1.5, 3.0)
        out &= ~((yy - ys[i]) ** 2 + (xx - xs[i]) ** 2 < r * r)
    out |= rng.random(ink.shape) < speckle                   # paper speckle
    out &= ~(rng.random(ink.shape) < holes)                  # pinholes in the ink
    return out


def drop_small(ink, max_size):
    """Drop ink pieces of at most `max_size` pixels. scikit-image 0.26 calls
    the limit max_size; older ones (the browser build's) take min_size, which
    drops only pieces smaller than it - so one more."""
    try:
        return morphology.remove_small_objects(ink, max_size=max_size)
    except TypeError:
        return morphology.remove_small_objects(ink, min_size=max_size + 1)


def fill_small_holes(ink, max_size):
    """Fill holes of at most `max_size` pixels (older scikit-image: area_threshold, one more)."""
    try:
        return morphology.remove_small_holes(ink, max_size=max_size)
    except TypeError:
        return morphology.remove_small_holes(ink, area_threshold=max_size + 1)


def clean(ink, min_area_frac=0.002, hole_strokes=1.5):
    """First pass: drop specks and fill pinholes - anything too small to be
    part of a letter - then smooth the edge a little. A hole is filled when
    it is smaller than a disc `hole_strokes` stroke-widths across (the
    stroke width read off the ink's own depth map); a real counter, even
    the eye of an e, is several stroke widths."""
    area = ink.sum()
    out = drop_small(ink, max(4, int(area * min_area_frac)))
    depth = ndimage.distance_transform_edt(out)
    stroke = 2 * np.percentile(depth[depth > 0], 90) if depth.max() > 0 else 4
    out = fill_small_holes(out, int(np.pi * (hole_strokes * stroke / 2) ** 2))
    out = morphology.opening(out, morphology.disk(1))
    return morphology.closing(out, morphology.disk(1))
