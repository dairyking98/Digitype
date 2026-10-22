"""One PNG per character: a row per run (clean / dirty), four panels each -
the input, the sinking island, the seed, and the letter grown back over
the original outline."""
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

NODE_STYLE = {"junction": ("o", "#d6336c"), "end": ("s", "#1c7ed6"), "loop": ("D", "#2f9e44"), "pass": (".", "#868e96")}


def _outline(ax, shape, **kw):
    for poly in getattr(shape, "geoms", [shape]):
        for ring in [poly.exterior, *poly.interiors]:
            ax.plot(*ring.xy, **kw)


def _fill(ax, shape, **kw):
    from matplotlib.patches import PathPatch
    from matplotlib.path import Path
    for poly in getattr(shape, "geoms", [shape]):
        verts, codes = [], []
        for ring in [poly.exterior, *poly.interiors]:
            xy = np.asarray(ring.coords)
            verts += xy.tolist()
            codes += [Path.MOVETO] + [Path.LINETO] * (len(xy) - 2) + [Path.CLOSEPOLY]
        ax.add_patch(PathPatch(Path(verts, codes), **kw))


def draw(path, char, shape, grid, results):
    rows = len(results)
    fig, axes = plt.subplots(rows, 4, figsize=(16, 4.4 * rows), squeeze=False)
    ext = [grid.x0, grid.x0 + grid.shape[1] / grid.scale, grid.y0 - grid.shape[0] / grid.scale, grid.y0]
    for row, res in zip(axes, results):
        isl, seed = res["island"], res["seed"]
        ax = row[0]
        ax.imshow(~res["ink"], cmap="gray", extent=ext, interpolation="nearest")
        ax.set_title(f"{res['name']}: input" + (" (simulated worn impression, bad scan)" if res["name"] == "dirty" else ""), fontsize=9)

        ax = row[1]
        depth = isl.depth / grid.scale
        ax.imshow(np.ma.masked_where(depth <= 0, depth), cmap="Blues", extent=ext, interpolation="nearest", alpha=0.55)
        if depth.max() > 0:
            levels = np.linspace(0, depth.max(), 7)[1:-1]
            ax.contour(np.flipud(depth), levels=levels, extent=ext, colors="#1864ab", linewidths=0.5)
        tr, tc = np.nonzero(isl.trail)
        x, y = grid.to_units(tc, tr)
        ax.scatter(x, y, s=0.3, c="#adb5bd")
        for br in isl.branches:
            bx, by = grid.to_units(br.path[:, 1], br.path[:, 0])
            ax.scatter(bx, by, s=1.2, c=br.depth / grid.scale, cmap="magma_r", vmin=0, vmax=depth.max() or 1)
        ax.set_title(f"sinking island: shorelines + trail by depth\n"
                     f"raw trail {res['raw'][0]} nodes / {res['raw'][1]} branches (grey = pruned corner ridges)", fontsize=9)

        ax = row[2]
        _outline(ax, shape, color="#dee2e6", lw=0.8)
        for st in seed.strokes:
            p = np.asarray(st.points)
            ax.plot(p[:, 0], p[:, 1], color="#212529", lw=1.4)
            ax.plot(p[:, 0], p[:, 1], ".", color="#495057", ms=3)
            mid = p[len(p) // 2]
            ax.annotate(f"{st.r[0]:.0f}->{st.r[1]:.0f}", mid, fontsize=6, color="#868e96", ha="center")
        for nd in seed.nodes.values():
            m, c = NODE_STYLE.get(nd.kind, ("x", "k"))
            ax.plot(*nd.xy, m, color=c, ms=6)
        npts = sum(len(st.points) for st in seed.strokes)
        ax.set_title(f"seed: {len(seed.strokes)} strokes, {npts} points, half-widths a->b\n"
                     f"pink = junction, blue = end, green = loop", fontsize=9)

        ax = row[3]
        if res["grown"] is not None:
            _fill(ax, res["grown"], facecolor="#212529", edgecolor="none", alpha=0.85)
        _outline(ax, shape, color="#e03131", lw=0.9)
        ax.set_title(f"grown back (ink) vs original outline (red)\nIoU {res['iou']:.3f}", fontsize=9)

        for ax in row:
            ax.set_aspect("equal")
            ax.set_xlim(ext[0], ext[1])
            ax.set_ylim(ext[2], ext[3])
            ax.set_xticks([])
            ax.set_yticks([])
    fig.suptitle(f"Digitype - {char!r}", fontsize=12)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)
