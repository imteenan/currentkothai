# Reading DPDC's scanned sheets: what works, what does not

Status: **27 of 36 DPDC zones are read.** 19 digital PDFs parsed directly, 8
scans read by `workers/parsers/scan_grid_v1.py`. Nine scans still refuse.

This note exists so nobody repeats the dead ends.

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
