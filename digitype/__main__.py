"""python -m digitype demo FONT CHARS [--dirty] [--out DIR]

For each character: the clean glyph and (with --dirty) a simulated worn,
badly scanned impression of it are each sunk, pruned to a seed and grown
back; a report PNG shows every stage and how close each regrown letter is
to the original (IoU, 1.0 = identical)."""
import argparse
import os

import numpy as np

from . import grow as G
from . import island, raster, report
from .glyph import Font
from .seed import from_island


def extract(ink, grid, char, advance, upem, prune_ratio):
    isl = island.sink(ink)
    raw = (len(isl.nodes), len(isl.branches))
    island.prune(isl, ratio=prune_ratio)
    return isl, raw, from_island(isl, grid, char, advance, upem)


def main():
    ap = argparse.ArgumentParser(prog="digitype")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("demo", help="seed + regrow characters of a font, clean and dirtied")
    d.add_argument("font")
    d.add_argument("chars")
    d.add_argument("--dirty", action="store_true", help="also run a simulated dirty scan of each character")
    d.add_argument("--px-per-em", type=float, default=600)
    d.add_argument("--prune-ratio", type=float, default=1.8)
    d.add_argument("--seed", type=int, default=1, help="random seed for the dirt")
    d.add_argument("--out", default="out")
    lb = sub.add_parser("label", help="the labelling tool: box glyphs on scans, then regenerate a font")
    lb.add_argument("sources", nargs="*", help="sample files or folders (images, multi-page TIFF, PDF)")
    lb.add_argument("--project", default=None, help="project folder (default: projects/<first source's name>)")
    lb.add_argument("--port", type=int, default=8770)
    lb.add_argument("--host", default="127.0.0.1")
    lb.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    if a.cmd == "label":
        return label(a)

    font = Font(a.font)
    os.makedirs(a.out, exist_ok=True)
    rng = np.random.default_rng(a.seed)
    for ch in a.chars:
        shape, adv = font.glyph(ch)
        if shape is None:
            print(f"{ch!r}: blank glyph, skipped")
            continue
        grid = raster.grid_for(shape, a.px_per_em, font.upem)
        ink = raster.rasterize(shape, grid)
        runs = [("clean", ink)]
        if a.dirty:
            scan = raster.dirty(ink, rng)
            runs.append(("dirty", raster.clean(scan), scan))
        results = []
        for name, im, *orig in runs:
            isl, raw, seed = extract(im, grid, ch, adv, font.upem, a.prune_ratio)
            grown = G.grow(seed)
            score = G.iou(grown, shape)
            results.append(dict(name=name, ink=orig[0] if orig else im, cleaned=im, island=isl, raw=raw,
                                seed=seed, grown=grown, iou=score))
            tag = f"{ord(ch):04x}_{name}"
            with open(os.path.join(a.out, f"seed_{tag}.json"), "w") as f:
                f.write(seed.to_json())
            print(f"{ch!r} {name}: trail {raw[0]} nodes / {raw[1]} branches -> seed {len(seed.nodes)} nodes / "
                  f"{len(seed.strokes)} strokes, regrown IoU {score:.3f}")
        png = os.path.join(a.out, f"report_{ord(ch):04x}.png")
        report.draw(png, ch, shape, grid, results)
        print(f"  -> {png}")


def label(a):
    import threading
    import webbrowser

    import uvicorn

    from .project import Project
    from .server import make_app
    root = a.project
    if root is None:
        name = os.path.basename(os.path.normpath(a.sources[0])) if a.sources else "untitled"
        root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "projects", name)
    proj = Project(root, a.sources)
    url = f"http://{a.host}:{a.port}/"
    print(f"Digitype: project {proj.root} - {len(proj.meta['pages'])} pages, {len(proj.box_list())} boxes")
    print(f"open {url}")
    if not a.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(make_app(proj), host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
