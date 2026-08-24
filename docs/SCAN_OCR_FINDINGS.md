# Reading DPDC's scanned sheets: what works, what does not

Status: **36 of 36 DPDC zones are read.** 19 digital PDFs parsed directly, 17
scans read by `workers/parsers/scan_grid_v1.py`.

This note exists so nobody repeats the dead ends.

> ## Correction: "Bengali OCR does not work" was too broad
>
> An earlier version of this note concluded that free offline Bengali OCR is
> unusable on these scans, and the parser therefore published `area_text: ""`
> for every scanned zone. That conclusion came from testing exactly one thing:
> the hour-column headers, where two numerals are stacked in a ~20px cell. It
> is true there and **only** there.
>
> The area column is 575px of single-line running text, and the same Tesseract
> with the same `ben` traineddata reads it well:
>
> | | |
> |---|---|
> | OCR | নয়ামাটি, আনন্দ হটেল, প্লাস্টিক য্যান |
> | Sheet | নয়ামাটি, আনন্দ হোটেল, প্লাস্টিক ম্যান |
>
> Two diacritics wrong in forty characters. That is unusable for a transcript
> and perfectly usable for "is my area on this list", which is the only question
> being asked of it.
>
> The generalisation cost 265 rows their identity for weeks, 159 of which were
> displayed to users as `row-04`. **Glyph size and line count decide whether
> Bengali OCR works here, not the script.** Test the specific cell you need.

## What works, and is in production

The scan pipeline never needed to read Bangla:

1. Render at 300 dpi (pymupdf), deskew from the dominant near-horizontal rule.
2. Recover the ruled grid by morphology: long horizontal and vertical dark runs.
3. Identify the hour region as the columns to the right of the **widest** column
   (the free-text area-name column).
4. Find the header band by testing which early band has the most inked cells
   among those hour columns.
5. **Shading is the data.** A cell scheduled off is a filled rectangle, so mean
   cell darkness against the page's own paper brightness gives the schedule with
   no text recognition at all.
6. Feeder codes are ASCII (`A133E`, `D201E`, `B327M`) and Tesseract reads them.

Blank-column detection is reliable and independently corroborated: on Azimpur,
measuring ink says columns 10 and 14 are empty, and a human reading the rendered
header agrees those two positions are spacers. Two methods, same answer.

## What works: the metadata columns

`workers/parsers/scan_text_v1.py` reads feeder code, feeder name, load and the
area list. Measured over all 265 scanned rows:

| Field | Read | Notes |
|---|---|---|
| Area list | 227 (85%) | Bengali, occasional wrong letters |
| Feeder name | 212 (80%) | Bengali |
| Billing code | 96 (36%) | ASCII, four segmentation modes tried |
| Load MW | 136 (51%) | Only when it parses as a plausible decimal |

Eight rows out of 265 end up with no identifying text at all.

Three things had to be true for this to work:

- **Roles are decided by content, not column position.** Layouts differ more
  than expected. Narayanganj has six metadata columns with names prefixed
  `১১ কেভি`; Khilgaon has six with names carrying no prefix; Fatulla has three,
  one of which repeats the thana name on all 33 rows; Dhanmondi has two. A
  position-based reader gets all but one of those wrong.
- **A column repeating one value is a label, not an identifier.** Requiring at
  least three distinct values among sampled rows keeps Fatulla's "ফতুল্লা" out
  of the feeder-name field.
- **Several zones print the load in Bengali numerals.** An English whitelist
  reads nothing there, which is why Khilgaon initially showed no loads at all.

## What does not work: reading the hour labels

DPDC's scans label hour columns in **Bengali numerals** (`০৯:০০ - ১০:০০`), set
small, in a two-line stack, photographed with CamScanner. Every free offline
engine tried failed to recognise them.

| Attempt | Result |
|---|---|
| Tesseract `eng` | Nothing. Wrong script |
| Tesseract `ben`, per cell | Garbage: `1. বৃ 2.5,` |
| Tesseract `ben`, whitelist of Bengali digits | Empty. LSTM often collapses when whitelisted to non-Latin |
| Tesseract `ben`, top/bottom line split | Empty |
| Tesseract, 600 dpi | Worse. Grid detection also degraded (15 cols vs 18) |
| Tesseract, full-strip `image_to_data` | 1 usable label out of 12 |
| EasyOCR `bn`, per cell | Confidence 0.01-0.11, garbage |
| EasyOCR `bn`, full strip, 2 scales | Confidence 0.01-0.21, garbage |

Preprocessing tried across the above: Otsu, adaptive threshold, bilateral
filter, non-local-means denoise, 2x to 8x Lanczos upscaling, generous quiet
borders, PSM 4/6/7/8/11/13.

**One useful partial result:** EasyOCR's text *detector* works even though its
recogniser does not. On Azimpur it found exactly 10 text clusters across the
hour region, matching the 10 labelled columns. Detection is not the problem;
Bengali recognition at this image quality is.

## Why the remaining nine fail, specifically

Three distinct causes, not one:

| Zones | Cause |
|---|---|
| azimpur, banglabazar, bangshal, fatulla, kamrangirchar, rajarbag, khilgaon | Grid recovered; hour labels unreadable |
| bashaboo | Grid detection collapses: 2 rows, 3 columns found. Very faint scan |
| paribag | Grid detection collapses: 1 row. Two-page document |

`khilgaon` finds a 15x14 grid but the widest-column heuristic picks the wrong
column, so the hour region is misidentified as 3 columns.

## The guard that keeps this honest

`scan_grid_v1.parse()` raises rather than publishing when it cannot read at
least 6 hour labels, and the geometric fallback only fires when the column
count matches a known grid exactly. It was offered the Motijheel 12-hour
template for Azimpur and **correctly refused**, because Azimpur has 10 hour
columns, not 12. Its grid genuinely differs from Motijheel's, so a shared
template would have shifted every window.

Nine zones showing "open the original PDF" is the correct output here. Nine
zones showing confidently wrong hours would not be.

## What would actually close the gap

In rough order of cost:

1. **A cloud OCR with real Bengali strength** (Google Cloud Vision, Azure Read).
   1,000 free units/month covers 9 zones daily several times over. Cost is not
   money, it is that the account needs a card on file, which breaks this
   project's "never asked for a card" property.
2. **Per-zone hour-grid config**, transcribed once by a human and validated
   against the automated blank-column detection. The grid is sheet layout, not
   daily data, so it would be read once and reused. Blocked today only because
   2-3 cells are genuinely ambiguous at this scan quality; those zones should
   stay unread rather than be guessed.
3. **Fix grid detection** for bashaboo and paribag independently. Separate
   problem from OCR, worth doing regardless.
4. **Ask DPDC for the digital originals.** They clearly have them; 19 zones
   already publish as real PDFs. The cheapest fix is social, not technical.

## Do not

- Do not add `easyocr` to `workers/requirements.txt`. It pulls ~2 GB of PyTorch
  into CI and, as measured above, does not read these images.
- Do not reuse one zone's hour grid for another. Azimpur and Motijheel differ.
