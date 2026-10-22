"""A font's glyph as a shapely shape (font units, y up)."""
from functools import reduce

from fontTools.pens.basePen import BasePen
from fontTools.ttLib import TTFont
from shapely.geometry import Polygon


class _FlattenPen(BasePen):
    """Records each contour as a polyline, curves split into short steps."""

    def __init__(self, glyphset, steps=16):
        super().__init__(glyphset)
        self.steps = steps
        self.contours, self._cur = [], []

    def _moveTo(self, p):
        self._cur = [p]

    def _lineTo(self, p):
        self._cur.append(p)

    def _curveToOne(self, p1, p2, p3):
        p0 = self._cur[-1]
        for i in range(1, self.steps + 1):
            t = i / self.steps
            u = 1 - t
            self._cur.append((u**3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t**3 * p3[0],
                              u**3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t**3 * p3[1]))

    def _qCurveToOne(self, p1, p2):
        p0 = self._cur[-1]
        for i in range(1, self.steps + 1):
            t = i / self.steps
            u = 1 - t
            self._cur.append((u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0],
                              u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1]))

    def _closePath(self):
        if len(self._cur) >= 3:
            self.contours.append(self._cur)
        self._cur = []

    _endPath = _closePath


class Font:
    def __init__(self, path, number=0):
        self.tt = TTFont(path, fontNumber=number)
        self.glyphset = self.tt.getGlyphSet()
        self.cmap = self.tt.getBestCmap()
        self.upem = self.tt["head"].unitsPerEm
        os2 = self.tt["OS/2"] if "OS/2" in self.tt else None
        hhea = self.tt["hhea"]
        self.ascender = getattr(os2, "sTypoAscender", hhea.ascent) if os2 else hhea.ascent
        self.descender = getattr(os2, "sTypoDescender", hhea.descent) if os2 else hhea.descent
        self.x_height = getattr(os2, "sxHeight", 0) or None
        self.cap_height = getattr(os2, "sCapHeight", 0) or None

    def glyph(self, char):
        """(shape, advance) for char; shape is None for a blank glyph.
        Contours combine even-odd, so counters are holes."""
        name = self.cmap.get(ord(char))
        if name is None:
            raise KeyError(f"{char!r} is not in the font")
        pen = _FlattenPen(self.glyphset)
        self.glyphset[name].draw(pen)
        advance = self.glyphset[name].width
        polys = [Polygon(c).buffer(0) for c in pen.contours]
        polys = [p for p in polys if not p.is_empty]
        if not polys:
            return None, advance
        return reduce(lambda a, b: a.symmetric_difference(b), polys), advance
