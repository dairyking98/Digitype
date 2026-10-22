# Type Seed

Recreate typewriter typefaces from dirty scans of type samples as clean vectors.

## The idea: a sinking island

Lower each letter into water a little at a time. The shoreline at each level is the letter
thinned by that much. Where shore from two sides meets and goes under, it leaves a **trail**.
The trail, plus the **depth** at which each point of it went under, is the letter's *seed*.
A disc of radius = depth at every trail point gives the letter back exactly.

The seed is where the cleanup happens. Dirt barely moves the trail:

- speck and edge fuzz leave short, shallow trails, which get pruned;
- ink blobs are depth bumps on a steady stroke;
- splits as the island sinks mark junctions (the middle of k, the crotch of y), last points to
  go under mark stroke ends, and lakes mark counters.

Then the seeds can be **standardized across the whole alphabet** (one stem width, true
verticals, shared baseline and x-height, shared serifs) and **grown back**: faithfully, or
modified (lighter/bolder, more or less contrast, monoline), and exported as a font.

## Pipeline

| stage | module | status |
|---|---|---|
| glyph from a font (test input) | `glyph.py` | done |
| simulated worn impression / bad scan | `raster.dirty` | done |
| first cleanup: specks, pinholes (< a stroke), edge | `raster.clean` | done |
| sinking island: depth map + trail graph (nodes, branches) | `island.sink` | done |
| prune corner ridges / speck trails, merge split junctions | `island.prune` | done, tuning |
| seed: smoothed + simplified centerlines, linearized half-widths, nodes | `seed.py` | done (polylines; Bézier fit next) |
| grow back (discs + tapered capsules, junction fill) | `grow.py` | done |
| report PNG per letter | `report.py` | done |
| average several impressions of the same letter | | next |
| real scans: threshold, split into letters, pitch grid | | next |
| standardize across the alphabet | | planned |
| terminal / serif styles (flat ends come back round today) | | planned |
| export a .ttf (fontTools) | | planned |

## Try it

```
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python3 -m typeseed demo path/to/font.ttf kyR8 --dirty --out out
```

Each letter gets `report_XXXX.png` (input -> sinking island -> seed -> grown back vs the
original, with IoU) and `seed_XXXX_{clean,dirty}.json`. `examples/` has runs on Noto Sans Mono
and FreeMono.

## Where it stands (first prototype)

- Clean letters: the trail matches the strokes (k = stem + arm + leg, R, 8, e...), and
  FreeMono's slab serifs come out as their own short strokes. Regrown IoU about 0.87-0.93; the
  gap is mostly flat terminals coming back round, until terminal styles exist.
- Dirty (simulated) scans of Noto Sans Mono usually give the same seed as the clean
  letter; about a third of runs keep an extra junction or two. Thin faces like FreeMono keep
  more junk spurs. Averaging several impressions of each letter is the planned fix.
- Ink spread shows up as fatter half-widths (expected): the standardize step will take it out.
