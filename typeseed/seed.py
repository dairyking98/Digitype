"""A glyph's seed: what is left of the letter once the island has sunk -
its trail as clean centerlines, each with a linearized half-width, plus the
nodes where strokes meet or end. Font units, y up. Growing a seed back
(grow.py) gives the letter again; editing the seed first changes it.
"""
import json
from dataclasses import asdict, dataclass, field

import numpy as np
from shapely.geometry import LineString


@dataclass
class SeedNode:
    xy: tuple
    r: float              # half-width where the trails meet (the depth it went under at)
    kind: str             # junction | end | loop


@dataclass
class Stroke:
    a: int
    b: int
    points: list          # centerline, simplified
    r: tuple              # (half-width at a, half-width at b) - linearized
    r_fit_error: float    # how far the measured depth strays from that line (rms, units)


@dataclass
class Seed:
    char: str
    advance: float
    upem: int
    nodes: dict = field(default_factory=dict)
    strokes: list = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    def to_json(self):
        d = asdict(self)
        d["nodes"] = {str(k): v for k, v in d["nodes"].items()}
        return json.dumps(d, indent=1)

    @classmethod
    def from_json(cls, text):
        d = json.loads(text)
        s = cls(d["char"], d["advance"], d["upem"], meta=d.get("meta", {}))
        s.nodes = {int(k): SeedNode(tuple(v["xy"]), v["r"], v["kind"]) for k, v in d["nodes"].items()}
        s.strokes = [Stroke(**{**v, "r": tuple(v["r"])}) for v in d["strokes"]]
        return s


def _linear_fit(arc, depth, skip_a, skip_b):
    """Half-width along the stroke as a straight line in arc length. Samples
    near a junction are left out - the island is deeper where strokes meet,
    which belongs to the junction, not to either stroke."""
    L = arc[-1] if len(arc) else 0.0
    keep = (arc >= skip_a) & (arc <= L - skip_b)
    if keep.sum() < 3:
        keep = np.ones_like(arc, dtype=bool)
    if keep.sum() < 2 or L <= 0:
        r = float(np.median(depth))
        return r, r, 0.0
    k, m = np.polyfit(arc[keep], depth[keep], 1)
    rms = float(np.sqrt(np.mean((depth[keep] - (k * arc[keep] + m)) ** 2)))
    return float(m), float(k * L + m), rms


def _smooth(pts, window):
    """Moving average along the path, ends pinned - irons out the wobble a
    ragged edge puts in the trail without moving where a stroke starts or
    stops. `window` in samples (about one stroke half-width)."""
    n = len(pts)
    if n < 5 or window < 3:
        return pts
    w = min(window | 1, n - (1 - n % 2))
    k = np.ones(w) / w
    pad = w // 2
    ext = np.vstack([np.repeat(pts[:1], pad, 0), pts, np.repeat(pts[-1:], pad, 0)])
    out = np.column_stack([np.convolve(ext[:, i], k, mode="valid") for i in range(2)])
    out[0], out[-1] = pts[0], pts[-1]
    return out


def from_island(isl, grid, char, advance, upem, simplify_em=0.004):
    """The pruned island's graph -> a Seed (pixels -> font units)."""
    s = Seed(char, advance, upem)
    u = 1.0 / grid.scale
    for nid, nd in isl.nodes.items():
        x, y = grid.to_units(nd.rc[1], nd.rc[0])
        s.nodes[nid] = SeedNode((float(x), float(y)), nd.depth * u, nd.kind)
    for br in isl.branches:
        x, y = grid.to_units(br.path[:, 1], br.path[:, 0])
        pts = np.column_stack([x, y])
        arc = np.concatenate([[0], np.cumsum(np.hypot(*np.diff(pts, axis=0).T))])
        depth = br.depth * u
        na, nb = isl.nodes[br.a], isl.nodes[br.b]
        skip_a = na.depth * u * 1.2 if na.kind == "junction" else 0.0
        skip_b = nb.depth * u * 1.2 if nb.kind == "junction" else 0.0
        r0, r1, err = _linear_fit(arc, depth, skip_a, skip_b)
        pts_s = _smooth(pts, window=max(3, int(np.median(br.depth) * 1.0)))
        line = LineString(pts_s) if len(pts_s) > 1 else None
        simple = list(line.simplify(simplify_em * upem).coords) if line is not None else pts.tolist()
        s.strokes.append(Stroke(br.a, br.b, [(round(px, 2), round(py, 2)) for px, py in simple],
                                (round(max(r0, 0), 2), round(max(r1, 0), 2)), round(err, 2)))
    return s
