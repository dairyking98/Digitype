# How it works

From labelled boxes to a font, step by step. The code is in `digitype/regen.py` (cells,
samples, averaging, fonts), `island.py` and `seed.py` (the seed), and `grow.py` (growing it
back).

- [1. Cells](#1-cells)
- [2. Baselines](#2-baselines)
- [3. Cutting out a sample](#3-cutting-out-a-sample)
- [4. Averaging](#4-averaging)
- [5. Tracing](#5-tracing)
- [6. The seed: a sinking island](#6-the-seed-a-sinking-island)
- [7. Growing it back](#7-growing-it-back)
- [8. Placement and the font file](#8-placement-and-the-font-file)

## 1. Cells

A typewriter prints every character in a cell of the same width (the pitch). A box labelled
with one character is one cell. A box labelled with a run of text is split into one cell per
character:

- **Pitch.** When the box's width divided by its characters agrees with the font set's pitch
  (within 8%), the set's pitch is used; otherwise the box's own.
- **Where the boundaries go.** Every pitch and offset in a small range is scored. A good split
  puts little ink on cell boundaries, puts the text's spaces on blank paper, and no letter on
  blank paper. The spaces matter most: in a table with a space after every letter, they tell
  "this glyph is in cell 4" from "it's in cell 5".
- **Snapping.** If a letter's cell has almost no ink in its middle (it missed its glyph, as in
  a keyboard chart whose glyphs are grouped), it moves onto the nearest single glyph within
  three quarters of a cell. Cells that already hold their glyph never move.
- **Checks.** The ink's span is compared with the number of characters, and every cell's ink
  with what the text says should be there; mismatches raise a warning on the box.

Each cell's width in pixels is its **unit**: the scale it was typed at. A single-glyph box
doesn't show the pitch, so it takes the unit of runs on the same page, or the pitch setting at
the page's dpi.

## 2. Baselines

Letters that sit on the line give a baseline point: the bottom of their ink. Every cell's
baseline then comes from a **Theil-Sen line** (the median of the slopes between every pair of
points, then the median intercept) through those points: robust, so one odd letter can't tip
it.

- **Adaptive:** only the points within about 8 cells are used, so the baseline follows a line
  that waves or drifts.
- **Straight:** all the points of the run, so one straight (possibly tilted) line.

The slope gives each cell its **tilt**. A single-glyph box fits the same kind of line through
boxes beside it on the page.

## 3. Cutting out a sample

For each cell, a window is cut from the page: the cell widened by *expand left* and *expand
right*, and vertically from two units above the baseline to most of a unit below it, whatever
the box size. The window is thresholded (Otsu) and split into pieces of connected ink. A piece
belongs to the cell when:

- **the body:** its centre is in the cell and it reaches the band just above the baseline;
- **marks:** it is small and whole, sits above the baseline, over the body's width and close
  above its top (dots of i and j, umlauts, accents, the circles of %, the top of `:` and `=`);
- with no body at all (`' " ^`), the marks are the glyph.

The line above and below, and the letters beside it, have their centres elsewhere. A piece
wider than a letter (two letters touching) is trimmed to its own cell.

**Soft edges.** Instead of keeping the black/white cut, the sample keeps the scan's grey
levels around the kept pieces: 0 for paper, 1 for full ink. How dark an edge pixel is pins the
true edge down to a fraction of a pixel, so even a single sample has a smooth edge.

**Tilt correction** turns the sample level about its baseline point by its line's tilt.

## 4. Averaging

All the samples of a character are combined:

1. **Common scale.** Each sample is resized so its unit (pixels per cell) matches the largest
   (up to 240 px), so scans of the same face at different sizes can be combined.
2. **Weights.** A sample counts in proportion to its original resolution, so a small, blurry
   scan can't outvote a sharp one.
3. **First placement** by cell centre and baseline.
4. **Registration.** Each sample is shifted, with sub-pixel phase correlation, to overlap the
   **other** samples as well as possible: up to half a cell sideways (which corrects drift in
   the cell split) and only a little up or down. It is compared with the others only, never
   with an average that includes itself, or it would just find its own copy.
5. **Odd samples.** With 3 or more samples, each is compared with the majority shape; one that
   matches much worse than is typical is left out and reported.
6. **Weighted mean** of what's left: a map of how likely each point is to be ink.

## 5. Tracing

The averaged map is lightly blurred (more when there are few samples, fading as samples are
added: averaging n samples smooths an edge on its own), enlarged with cubic interpolation
(*Detail*), and traced where at least half of the samples have ink. That outline is the
**Average** font's glyph.

## 6. The seed: a sinking island

The Seed font rebuilds each letter from its skeleton.

Imagine lowering the letter into water a little at a time. The shoreline at each level is the
letter thinned by that much. Where shore from two sides meets and goes under, it leaves a
**trail**; the depth at which each trail point went under is the stroke's half-width there.
The trail plus the depth is the letter's **seed**, and a disc of that radius at every trail
point gives the letter back.

In practice (`island.py`):

- the depth map is the distance transform of the ink, the trail its medial axis;
- the trail is read as a graph: **nodes** where trails meet (junctions) or end, **branches**
  between them, each with its depth along it;
- **pruning:** every sharp outside corner leaves a short ridge running into the corner whose
  depth falls to zero at the shore. A branch is dropped when it is short for its depth *and*
  runs out at the shore; a serif or short arm keeps its thickness to its end, so it stays.
  Junctions a ragged edge split in two are merged; separate pieces (the dot of an i) are kept.

Then (`seed.py`) each branch becomes a **stroke**: its centreline smoothed and simplified, and
its half-width fitted as a straight line along it (leaving out the extra depth where strokes
meet, which belongs to the junction).

## 7. Growing it back

`grow.py` puts a disc of the stroke's half-width at every point of each centreline, joins
neighbouring discs into tapered segments, and adds a disc at each junction. *Weight* scales
every half-width and *Add* adds to it, so one seed can grow a lighter or bolder cut.

Stroke ends come back round, and corners slightly soft: restoring flat terminals and sharp
corners is planned.

## 8. Placement and the font file

**Placement.** *As typed* keeps each glyph where it sat in its cell (the face's own placement).
*Centred* moves it so its ink is centred in the cell, measured on the letter's main ink
(pieces under 3% of it don't count).

**The font file** (fontTools): one glyph per character, every advance one cell (600 units on a
1000-unit em), outer contours clockwise, `post.isFixedPitch` and PANOSE monospaced, and the
full name table Windows requires (family, style, unique id, full name, version, PostScript
name).
