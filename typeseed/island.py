"""The sinking island.

Lower the letter into water a little at a time. The shoreline at each level
is the letter thinned by that much; where shore from two sides meets and
goes under, it leaves a trail. The trail (the ridges of the island) is the
letter's skeleton, and the depth at which each trail point went under is
the stroke's half-width there - so disc(depth) at every trail point gives
the letter back.

Here that is computed on pixels: the depth map is the distance transform
(distance to the nearest shore), the trail its medial axis. The trail is
then read as a graph: NODES where trails meet (junctions - the middle of
k, the crotch of y) or end (terminals, corners), and BRANCHES between them,
each a pixel path with its depth along it.

Every sharp outside corner leaves a short ridge running into the corner
whose depth falls to zero at the shore; a real stroke runs much further
than it is deep. prune() removes branches that end at the shore and are
short for their depth.
"""
from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage
from skimage.morphology import medial_axis

_NB = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


@dataclass
class Node:
    id: int
    rc: tuple             # (row, col), mean of the node's pixels
    depth: float          # px
    kind: str = ""        # junction | end | loop


@dataclass(eq=False)
class Branch:
    a: int                # node ids at each end
    b: int
    path: np.ndarray      # (n, 2) rows/cols, a -> b
    depth: np.ndarray     # (n,) px

    @property
    def length(self):
        return float(np.hypot(*np.diff(self.path, axis=0).T).sum()) if len(self.path) > 1 else 0.0


@dataclass
class Island:
    depth: np.ndarray                 # distance to shore, px (0 outside the ink)
    trail: np.ndarray                 # bool: the medial axis
    nodes: dict = field(default_factory=dict)
    branches: list = field(default_factory=list)

    def degree(self, nid):
        return sum((br.a == nid) + (br.b == nid) for br in self.branches)


def sink(ink):
    """Depth map + trail + its graph."""
    trail, dist = medial_axis(ink, return_distance=True)
    isl = Island(depth=dist, trail=trail)
    _graph(isl)
    return isl


def _graph(isl):
    sk = isl.trail
    rows, cols = sk.shape
    pad = np.pad(sk, 1)
    deg = sum(np.roll(np.roll(pad, -dr, 0), -dc, 1) for dr, dc in _NB)[1:-1, 1:-1] * sk
    is_node = sk & (deg != 2)
    lab, n = ndimage.label(is_node, structure=np.ones((3, 3)))
    nodes = {}
    for i, sl in enumerate(ndimage.find_objects(lab), start=1):
        rr, cc = np.nonzero(lab[sl] == i)
        rr, cc = rr + sl[0].start, cc + sl[1].start
        nodes[i] = Node(i, (float(rr.mean()), float(cc.mean())), float(isl.depth[rr, cc].max()))
    seen = np.zeros_like(sk)

    def nbrs(r, c):
        for dr, dc in _NB:
            rr, cc = r + dr, c + dc
            if 0 <= rr < rows and 0 <= cc < cols and sk[rr, cc]:
                yield rr, cc

    def walk(start, r, c, pr, pc):
        """From node pixel (pr, pc) step to (r, c) and follow the trail
        until it reaches a node; -> (pixel path, end node id or None)."""
        path, prev = [(pr, pc), (r, c)], (pr, pc)
        while not is_node[r, c]:
            seen[r, c] = True
            cand = [p for p in nbrs(r, c) if p != prev and (is_node[p] or not seen[p])
                    and not (len(path) < 4 and lab[p] == start)]
            if not cand:
                break
            # step onto a node when one is next to us, else the nearest pixel
            cand.sort(key=lambda p: (not is_node[p], abs(p[0] - r) + abs(p[1] - c)))
            prev, (r, c) = (r, c), cand[0]
            path.append((r, c))
        return path, (int(lab[r, c]) if is_node[r, c] else None)

    branches = []
    for nid in nodes:
        for pr, pc in zip(*np.nonzero(lab == nid)):
            for r, c in nbrs(pr, pc):
                if lab[r, c] == nid or seen[r, c]:
                    continue
                if is_node[r, c]:                     # two node clusters touching
                    if lab[r, c] > nid:
                        branches.append((nid, lab[r, c], [(pr, pc), (r, c)]))
                    continue
                path, end = walk(nid, r, c, pr, pc)
                if end is None:
                    continue
                branches.append((nid, end, path))
    # closed loops with no node at all (the trail of an o): cut at one pixel
    rest = sk & ~seen & ~is_node
    lab2, n2 = ndimage.label(rest, structure=np.ones((3, 3)))
    for i in range(1, n2 + 1):
        rr, cc = np.nonzero(lab2 == i)
        if len(rr) < 4:
            continue
        nid = max(nodes, default=0) + 1
        r0, c0 = rr[0], cc[0]
        nodes[nid] = Node(nid, (float(r0), float(c0)), float(isl.depth[r0, c0]), "loop")
        seen[r0, c0] = True
        path = [(r0, c0)]
        r, c = r0, c0
        while True:
            nxt = [p for p in nbrs(r, c) if not seen[p] and lab2[p] == i]
            if not nxt:
                break
            nxt.sort(key=lambda p: abs(p[0] - r) + abs(p[1] - c))
            r, c = nxt[0]
            seen[r, c] = True
            path.append((r, c))
        path.append((r0, c0))
        branches.append((nid, nid, path))
    isl.nodes = nodes
    isl.branches = []
    for a, b, path in branches:
        p = np.array(path, dtype=float)
        isl.branches.append(Branch(a, b, p, isl.depth[p[:, 0].astype(int), p[:, 1].astype(int)]))
    _classify(isl)


def _classify(isl):
    for nid, nd in isl.nodes.items():
        if nd.kind == "loop":
            continue
        d = isl.degree(nid)
        nd.kind = "end" if d <= 1 else "junction" if d >= 3 else "pass"


def prune(isl, ratio=2.5, corner_frac=0.35, min_px=3.0, rounds=8):
    """Drop corner ridges: a branch with a free end that is short (under
    `ratio` x the depth where it leaves its junction - a corner ridge runs
    about 1.4 x that depth into a right-angle corner, further into an acute
    one) AND runs out at the shore (its depth at the free end under
    `corner_frac` x the junction's). A serif or a short arm keeps its
    thickness right to its end, so it stays. Shortest first, repeated -
    removing a serif's own corner ridges is what frees its end. Separate
    pieces (the dot of an i, a full stop) are never dropped here: specks
    are removed from the ink before the island sinks. Then joins branches
    through nodes left with exactly two."""
    _merge_passes(isl)
    for _ in range(rounds):
        _collapse_short(isl)
        drop = []
        for i, br in enumerate(isl.branches):
            if br.a == br.b:                 # a loop: a pinhole's ring if it's short for its depth
                if isl.degree(br.a) > 2 and br.length < 4 * max(br.depth.max(), 1):
                    drop.append(i)
                continue
            da, db = isl.degree(br.a), isl.degree(br.b)
            if da == 1 and db == 1:          # a lone segment: its own piece of ink - keep
                continue
            if da == 1 or db == 1:
                attach = br.b if da == 1 else br.a
                end_depth = br.depth[0] if da == 1 else br.depth[-1]
                att = isl.nodes[attach].depth
                if br.length < max(min_px, ratio * att) and end_depth < max(1.5, corner_frac * att):
                    drop.append((br.length, i))
        if not drop:
            break
        # shortest first, and never two spurs off the same junction in one
        # round - the second may stop being a spur once the first is gone
        drop.sort()
        gone, touched = set(), set()
        for _, i in drop:
            br = isl.branches[i]
            if br.a in touched or br.b in touched:
                continue
            gone.add(i)
            touched |= {br.a, br.b}
        isl.branches = [br for i, br in enumerate(isl.branches) if i not in gone]
        _merge_passes(isl)
    used = {br.a for br in isl.branches} | {br.b for br in isl.branches}
    # a round dot's trail can shrink to a single point: keep it as a dot
    isl.nodes = {k: v for k, v in isl.nodes.items() if k in used or isl.degree(k) == 0}
    _classify(isl)
    for k, v in isl.nodes.items():
        if k not in used:
            v.kind = "dot"
    return isl


def _collapse_short(isl, frac=0.6):
    """Two junctions closer than `frac` x their depth are one junction that
    a ragged edge split in two: merge them (the edge between disappears)."""
    changed = True
    while changed:
        changed = False
        for br in list(isl.branches):
            if br.a == br.b:
                continue
            na, nb = isl.nodes[br.a], isl.nodes[br.b]
            if isl.degree(br.a) < 3 or isl.degree(br.b) < 3:
                continue
            if br.length < frac * max(na.depth, nb.depth):
                keep, gone = (na, nb) if na.depth >= nb.depth else (nb, na)
                isl.branches = [o for o in isl.branches if o is not br]
                for o in isl.branches:
                    if o.a == gone.id:
                        o.a = keep.id
                    if o.b == gone.id:
                        o.b = keep.id
                del isl.nodes[gone.id]
                changed = True
                break


def _merge_passes(isl):
    """A node with exactly two branches is just a point along one stroke."""
    changed = True
    while changed:
        changed = False
        for nid in list(isl.nodes):
            mine = [br for br in isl.branches if nid in (br.a, br.b) and br.a != br.b]
            if len(mine) != 2 or isl.degree(nid) != 2 or isl.nodes[nid].kind == "loop":
                continue
            b1, b2 = mine
            p1, d1 = (b1.path, b1.depth) if b1.b == nid else (b1.path[::-1], b1.depth[::-1])
            p2, d2 = (b2.path, b2.depth) if b2.a == nid else (b2.path[::-1], b2.depth[::-1])
            a = b1.a if b1.b == nid else b1.b
            b = b2.b if b2.a == nid else b2.a
            isl.branches = [br for br in isl.branches if br is not b1 and br is not b2]
            isl.branches.append(Branch(a, b, np.vstack([p1, p2[1:]]), np.concatenate([d1, d2[1:]])))
            del isl.nodes[nid]
            changed = True
            break
