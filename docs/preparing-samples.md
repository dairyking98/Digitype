# Preparing type samples

How to type and scan a sample sheet that Digitype turns into a clean font. Old samples you
already have still work; these are the rules for sheets you make yourself.

- [Typing](#typing)
- [Scanning](#scanning)
- [A sample sheet to type](#a-sample-sheet-to-type)
- [Labelling it](#labelling-it)

## Typing

**Line spacing 1.5 or more, double is best.** At single spacing, the descenders of one line
(g, j, p, q, y) come close to the accents, dots and capitals of the next. Extra space keeps
every mark with its own letter.

**A space between characters.** Type `a b c d`, not `abcd`. Each glyph then stands on its own:
cells land exactly on their glyphs and letters never touch, even on a heavy face or a
well-inked ribbon. (Run-together text works too, but touching letters have to be cut apart and
are never quite as clean.)

**Repeat every character, several times.** Each impression of a letter is a little different
(ink, pressure, paper); the average of many is the true letter. Three is a minimum, five or
more is better. Spread the repeats over different lines rather than side by side, so dirt and
uneven inking don't repeat with them.

**The whole character set.** Lowercase, capitals, figures, punctuation, and every accented
letter and symbol the machine has (`ä ö ü ß é è ç ½ ¼ ¢ § £ @ #`…). Anything you don't type
won't be in the font.

**An even impression.** A fresh, not over-inked ribbon, even key pressure (or an electric
machine), clean type slugs. Over-inking fills counters and makes letters touch; a dry ribbon
breaks strokes.

**A line of normal text** at the top (a pangram, typed without extra spaces), so the sheet also
shows the face's natural spacing, and a note of the machine, typeface name and pitch.

**Leave margins**, and keep the typing away from folds and the edges of the paper.

## Scanning

- **600 dpi** (1200 for very small type such as 12 or 15 pitch).
- **Greyscale, not black-and-white.** Digitype reads each glyph's grey edge to place it to a
  fraction of a pixel; a black-and-white scan throws that away.
- **Lossless files:** TIFF or PNG. JPEG is usable but adds noise around the letters.
- **Flat and straight:** close the scanner lid; a slight skew is fine (Digitype straightens it,
  and each page can be rotated), but a curled page distorts letters.
- **No sharpening, no automatic "text enhancement".** Plain scans average better.

## A sample sheet to type

Type this double-spaced, with a space between characters as shown. Repeat the block two or
three times on the sheet (or on several sheets) for more samples. Add the accented letters and
symbols your machine has to the last lines.

```
The quick brown fox jumps over the lazy dog. 1234567890

a b c d e f g h i j k l m n o p q r s t u v w x y z

A B C D E F G H I J K L M N O P Q R S T U V W X Y Z

1 2 3 4 5 6 7 8 9 0 . , : ; ! ? ' " - _ ( ) / & % $ # @ * + =

n o p q r s t u v w x y z a b c d e f g h i j k l m

N O P Q R S T U V W X Y Z A B C D E F G H I J K L M

0 9 8 7 6 5 4 3 2 1 = + * @ # $ % & / ) ( _ - " ' ? ! ; : , .

ä ö ü Ä Ö Ü ß é è à ç ½ ¼ ¾ ¢ § £
```

The second half repeats the first in a different order, so each letter gets different
neighbours, which helps the averaging tell a letter from what's around it.

A ready-to-copy text file: [sample-sheet.txt](sample-sheet.txt).

## Labelling it

Draw one box per line and type it exactly as on the page, **spaces included** (`a b c d`).
With a space after every character, each cell lands on its glyph and the warnings stay quiet.
Put each typeface in its own font set, set its pitch (the panel suggests the measured one),
and Regenerate. See the [user guide](user-guide.md).
