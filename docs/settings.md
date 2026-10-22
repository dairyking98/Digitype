# Settings reference

Settings live in the side panel and are kept **per font set**. A new set starts with a copy
of the current set's settings. Changes to how glyphs are built take effect on the next
**Regenerate**.

| Setting | Default | What it does |
|---|---|---|
| **Pitch (mm)** | 2.54 | The typewriter's character pitch: 2.54 mm = 10 per inch (pica), 2.12 mm = 12 per inch (elite). Used to split runs of text into cells. The panel suggests the pitch measured from your runs; **Use** takes it. Glyph size comes from the cells themselves, so a wrong pitch doesn't distort the font, but the split is more reliable when it's right. |
| **Expand left** | 0.5 | How far past the left edge of each cell ink is collected, as a fraction of the cell's width. Raise it when wide letters lose their left side. Ink outside the cell only counts when it belongs to a stroke whose centre is in the cell. |
| **Expand right** | 0.5 | The same for the right edge (wide letters like w, m, W). |
| **Baseline** | Adaptive | *Adaptive* fits the baseline locally along each line (skew, waves). *Straight* fits one straight, possibly tilted, line per run. |
| **Tilt correction** | on | Turns each sample level by its line's tilt before averaging, so samples from crooked lines line up. |
| **Placement** | As typed | *As typed*: each glyph keeps its position in its typewriter cell. *Centred*: each glyph's ink is centred in the cell. |
| **Soft edges** | on | Uses the scan's grey edge (sub-pixel) instead of a black/white cut. Single samples trace much smoother. |
| **Smoothing** | 1 | Extra smoothing for glyphs with few samples, fading as more are averaged. 0 = off, 2 = strong (rounds serif corners a little more). |
| **Font name** | (the set's name) | Family name of the exported fonts. |
| **Detail ×** | 4 | How much the averaged map is enlarged before tracing and taking the seed: smoother curves, slower. |
| **Weight ×** | 1 | Seed font only: scales every stroke's half-width when growing back. |
| **Add (units)** | 0 | Seed font only: adds to every stroke's half-width (font units; + bolder, − lighter), for example to take ink spread out. |

Fixed values (in `project.json`, not in the panel): `upem` 1000 and `advance` 600, the em and
the cell width of the exported fonts.

## Where settings are stored

`projects/<name>/project.json` holds the pages, the font sets and each set's settings;
`labels.json` holds the boxes. Both are plain JSON.
