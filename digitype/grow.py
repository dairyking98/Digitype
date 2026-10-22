"""Growing a seed back into a letter: a disc of the stroke's half-width at
every point of every centerline (tapered capsules between points), plus a
disc at each junction for the extra depth where strokes meet.

`weight` scales every half-width (a lighter or bolder cut from one seed);
`add` adds a fixed amount to every half-width (font units, + bolder)."""
import numpy as np
from shapely.geometry import Point
from shapely.ops import unary_union


def _capsule(p, rp, q, rq, quad=12):
    """Two discs and everything between them: a tapered stroke segment."""
    return Point(p).buffer(rp, quad).union(Point(q).buffer(rq, quad)).convex_hull


def grow(seed, weight=1.0, add=0.0, junctions=True):
    parts = []
    for st in seed.strokes:
        pts = np.asarray(st.points, dtype=float)
        if len(pts) == 1:
            parts.append(Point(pts[0]).buffer(max(st.r[0] * weight + add, 0.1)))
            continue
        seg = np.hypot(*np.diff(pts, axis=0).T)
        arc = np.concatenate([[0], np.cumsum(seg)])
        L = arc[-1] or 1.0
        r = (st.r[0] + (st.r[1] - st.r[0]) * arc / L) * weight + add
        r = np.maximum(r, 0.1)
        for i in range(len(pts) - 1):
            parts.append(_capsule(tuple(pts[i]), r[i], tuple(pts[i + 1]), r[i + 1]))
    if junctions:
        for nd in seed.nodes.values():
            if nd.kind in ("junction", "dot"):
                parts.append(Point(nd.xy).buffer(max(nd.r * weight + add, 0.1), 12))
    return unary_union(parts) if parts else None


def iou(a, b):
    if a is None or b is None or a.is_empty or b.is_empty:
        return 0.0
    return a.intersection(b).area / a.union(b).area
