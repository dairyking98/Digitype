# User guide

Digitype turns scans of typewriter type samples into clean, installable fonts. You show it
where each character is on the scans; it averages every impression of each character, cleans
it up and writes two fixed-pitch TrueType fonts.

- [Install and start](#install-and-start)
- [Projects, pages and font sets](#projects-pages-and-font-sets)
- [Labelling](#labelling)
- [Baselines](#baselines)
- [Warnings and checks](#warnings-and-checks)
- [Regenerate](#regenerate)
- [Results and test type](#results-and-test-type)
- [The fonts](#the-fonts)
- [Tips for good results](#tips-for-good-results)
- [Controls](#controls)

See also: [How it works](how-it-works.md) · [Settings reference](settings.md)

## Install and start

You need Python 3.10 or newer.

**Windows:** run `run.bat` with your samples, from a terminal in the repository folder:

```
run.bat "D:\Scans\Type Samples"
```

The first run creates a `.venv` and installs the requirements; after that it starts at once.

**Anywhere:**

```
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python3 -m digitype label "path/to/samples" [more files or folders] [--project DIR]
```

It opens **http://127.0.0.1:8770** in your browser. Options: `--port`, `--host` (use
`0.0.0.0` to reach it from another computer on your network), `--no-browser`.

Everything stays on your computer: the page talks only to the local server.

## Projects, pages and font sets

A **project** is a folder (by default `projects/<name of the first source>`) that holds your
labels, settings, a cache of the pages, and everything Regenerate writes. Projects are not part
of git.

**Pages.** Every image file is a page, every frame of a multi-page TIFF is a page, and every
page of a PDF is a page (rendered at 600 dpi). Add more at any time:

- **Upload PDF / images…** (top of the Pages list) copies files from your browser into the
  project's `uploads/` folder.
- **Add from a path…** adds a file or folder already on the computer running Digitype.

Big scans are fine: each page is converted to grey once and cached, so later visits are quick.

**Font sets.** One project can hold several typefaces, for example a sample book with a
different face on every page. Each typeface is its own **font set**, chosen in the top bar:

- **+ New font** starts a new, empty set (its settings start as a copy of the current set's).
- **Rename**, and **Delete** (only for a set with no boxes, so nothing is lost by accident).
- Boxes, pitch, font name and every other setting are kept per set.
- Regenerate only uses the set you're on, so two typefaces are never averaged together.
- Other sets' boxes show dimmed and dashed on the page, and can't be clicked.
- A selected box can be moved to another set with the **Font** picker in the side panel.

## Labelling

**Draw a box** around a character and type it, then press Enter.

**Type a whole line.** Draw one box around a run of text and type exactly what it says,
spaces included. The box is split into fixed-pitch cells, one per character, and each cell's
character is drawn under it so you can check the fit at a glance. This is the fast way to
label body text, which gives you many samples of every common letter.

- The split uses the font set's pitch when the box agrees with it, and finds where the cell
  boundaries go from the gaps between letters and the spaces in your text.
- A cell that clearly missed its glyph (in a chart whose glyphs are grouped rather than evenly
  spaced) snaps onto the nearest glyph. Small offsets are left alone: they are the face's own
  placement in its cell.
- Type accented letters as they are on the page (Ä, ü, é…). A plain letter labelled where the
  page has an accented one adds the accent to the plain letter.

**Editing boxes.**

- Click a box to select it. Double-click (or Enter) edits its text.
- Drag one of the eight handles of the selected box to resize it.
- Shift+drag moves a box. Boxes are otherwise locked, so a click that wobbles never changes
  your data.
- Arrow keys nudge the selected box by a pixel (Shift: 10).
- Delete removes it.

The **Glyphs** panel lists every character labelled in the current set with its sample count;
click one to visit its samples in turn.

## Baselines

Every sample needs a baseline, the line the letters sit on.

- Letters that sit on the line (most letters, figures, `. ! ? :`) give it: the bottom of
  their ink.
- Letters that hang below or float above (`g j p q y , ; ( ) _ ' "`…) take it from the
  others on their line, or, in a single-glyph box, from boxes beside it on the page.
- **Adaptive** baselines (the default) are fitted locally along each line, so a skewed scan or
  a wavy line is followed. **Straight** fits one straight, possibly tilted, line per run.
- **Tilt correction** turns each sample level by its line's tilt before averaging.
- Shift+drag the red baseline of a selected box to set it by hand; **Auto baseline** goes back.

The red lines on the page are drawn at their actual slope.

## Warnings and checks

A box turns **red** and explains itself in the side panel when the text can't match the ink:

- the number of characters doesn't fit the box at the font's pitch;
- the ink spans more or fewer cells than the text has characters;
- spaces land on ink, or letters on blank paper (usually a missing or extra space; the warning
  shows where it starts).

During Regenerate, a sample that clearly disagrees with the other samples of its character
(often a label mistake, such as an ä labelled a, or a letter one cell off) is **left out** of
the average. It is listed in the log, on the glyph's card ("1 odd left out"), and in the
enlarged view with a button to jump to its box.

## Regenerate

**Regenerate** builds the current font set. The log shows each character as it goes. For each
character, every sample is cut out, scaled to a common size, lined up with the others,
averaged, traced, and also reduced to a seed and grown back. Details:
[How it works](how-it-works.md).

Output goes to `projects/<name>/out/<NN set name>/`:

| File | What it is |
|---|---|
| `<name>-Average.ttf` | The averaged samples, traced: the most faithful version |
| `<name>-Seed.ttf` | Each glyph reduced to its skeleton (seed) and grown back |
| `seeds/seed_XXXX.json` | The seed of each character: strokes, half-widths, nodes |
| `result.json` | What the Results page shows |

## Results and test type

The **Results** tab shows every character as three panels, all at one common scale with the
baseline and cell drawn in:

1. **averaged**: the averaged scan,
2. **traced**: its outline (the Average font),
3. **seed, regrown**: the Seed font, with the seed's strokes in orange (toggle *seed strokes*).

Each card shows the sample count and *match*, how closely the seed and the trace agree.
**Sort** by character, most or fewest samples, or worst match first (a quick way to find what
needs more samples or a label fix). Click a card to enlarge it.

**Test type** at the top sets any text you type in both fonts as soon as they're written, with
a size slider. Characters the font doesn't have yet are listed underneath.

## The fonts

Both fonts are true monospaced fonts: every glyph is one cell wide (600 units on a
1000-unit em), and they are flagged fixed-pitch (`post.isFixedPitch` and PANOSE
`bProportion = 9`), so font menus list them as monospaced. They carry the full set of names
Windows needs and install normally.

The family name is the font set's **Font name** setting, or the set's name.

## Tips for good results

- **Label lots of body text.** More samples of a letter means a cleaner, more faithful average.
  Single samples work, and are smoothed to look consistent, but more is always better.
- **One typeface per font set.** Different faces averaged together produce mush.
- **Set the pitch.** After labelling a couple of lines, the Settings panel suggests the pitch it
  measured; click **Use** to take it.
- **Type accents.** An ä labelled a puts dots on your a.
- **Boxes can be loose vertically.** Samples are captured well above and below the baseline
  whatever the box size, and marks from neighbouring lines are kept out.
- **Look at "worst match first"** in Results to see which glyphs need attention.

## Controls

| Action | How |
|---|---|
| Zoom | Mouse wheel (the full-resolution scan loads as you zoom in) |
| Pan | Right-drag, middle-drag, or Space + drag |
| Fit the page | F |
| New box | Drag on empty page, type its text, Enter (Esc cancels) |
| Select | Click a box |
| Edit text | Double-click, or Enter |
| Resize | Drag a handle of the selected box |
| Move | Shift + drag the box |
| Set baseline | Shift + drag the red line |
| Nudge | Arrow keys (Shift: ×10) |
| Delete | Delete or Backspace |
