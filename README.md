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
| average several impressions of the same letter | `regen.average` | done |
| real scans: labelling tool, cells, baselines, averaging, .ttf export | `project.py` `regen.py` `server.py` `web/` | done |
| standardize across the alphabet | | planned |
| terminal / serif styles (flat ends come back round today) | | planned |
| export a .ttf (fontTools) | `regen.write_font` | done |

## Labelling tool (real scans)

```
run.bat "E:\Leonard\Typewriter\Typefaces\Type Samples\Peli"      (Windows; first run sets up .venv)
.venv/bin/python3 -m typeseed label path/to/samples [--project DIR]  (anywhere)
```

Opens a local page (http://127.0.0.1:8770). Every image, multi-page TIFF frame and PDF page
is a page. Drag a box round a glyph and type it; type a whole run (spaces included) to split a
line into fixed-pitch cells at the project's pitch. The page warns when the text can't match
the ink (a missing or extra space). Baselines are automatic (letters that sit on the line give
it; g j p , ( ... borrow it from their line); drag the red line to set one by hand.

**Regenerate** averages every sample of each character (lined up on cell and baseline,
registered sub-pixel), then writes two fixed-pitch fonts to `projects/<name>/out/`:
`-Average.ttf` (the averaged ink, traced) and `-Seed.ttf` (sunk to a seed and regrown).
Results shows each glyph's average, trace and seed. Projects live in `projects/` (not in git).

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
