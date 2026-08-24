"""Read the metadata columns of a scanned DPDC sheet: code, name, load, areas.

Every scanned zone used to publish `area_text: ""` and, for 159 of 265 rows, a
feeder called `row-04`. That is a schedule nobody can act on: you cannot tell
whether `row-04` is your street.

The columns were always there. The earlier conclusion that Bengali OCR is
unusable came from testing it on the one place it genuinely fails - the hour
headers, where two numerals are stacked in a ~20px cell - and never testing it
on the area column, where the same engine reads 575px of running text well:

    OCR  : নয়ামাটি, আনন্দ হটেল, প্লাস্টিক য্যান
    sheet: নয়ামাটি, আনন্দ হোটেল, প্লাস্টিক ম্যান

Two diacritics wrong in forty characters. Wrong for a transcript, fine for
"is my area on this list", which is the only question being asked.

Three rules follow from that accuracy profile:

1. **Never correct a word.** Dictionary-fixing OCR output on place names is how
   you invent an area that does not exist. Normalisation here is limited to
   things that carry no meaning: zero-width joiners, stray whitespace, dangling
   punctuation. A misread stays misread and is shown next to its source scan.

2. **Bengali is what we show; Latin is what we search.** The transliteration is
   deliberately lossy and is never displayed. It exists so a user typing
   "noyamati" matches the Bengali cell, and so DPDC zones finally give the
   ranker text evidence instead of distance alone.

3. **Roles come from content, not position.** Sheets carry 4 to 6 metadata
   columns in varying order. The feeder-name column is found by the literal
   "kV" in Bengali that every DPDC feeder name contains - a far stronger signal
   than "third from the left".
"""
from __future__ import annotations

import re
import unicodedata

#: Words that appear in a DPDC feeder name and nowhere else on the row. Most
#: zones name feeders "<voltage> kV <place>"; Tejgaon uses "<place> overhead".
#: A column carrying one of these is the feeder-name column, whatever its
#: position or width.
KV_MARKER = "কেভি"
NAME_MARKERS = (KV_MARKER, "ওভারহেড", "আন্ডারগ্রাউন্ড", "ফিডার")

#: A cell must be at least this wide to hold readable Bengali words.
MIN_TEXT_WIDTH = 90

#: Rows sampled when deciding what a column contains.
CLASSIFY_ROWS = 6

#: Plausible load band for an 11kV distribution feeder, in MW.
MIN_LOAD_MW, MAX_LOAD_MW = 0.2, 15.0

#: Segmentation modes tried on the short fixed-shape billing code.
CODE_PSMS = (7, 6, 8, 13)

#: Bengali digits to ASCII, for zones that print loads in Bengali numerals.
_BN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")

_CODE_RE = re.compile(r"\b([A-Z]{1,3}[- ]?\d{2,4}[A-Z]?)\b")
#: A load is written with a decimal point; a serial number is not.
_DECIMAL_RE = re.compile(r"\d{1,3}[.,]\d{1,2}")
_ZERO_WIDTH = re.compile(r"[​‌‍﻿]")
_BENGALI = re.compile(r"[ঀ-৿]")

#: Bengali to Latin. Lossy on purpose: the web gazetteer folds all vowels to
#: 'a' before comparing, so vowel precision buys nothing and costs correctness.
_TRANSLIT = {
    "অ": "a", "আ": "a", "ই": "i", "ঈ": "i",
    "উ": "u", "ঊ": "u", "ঋ": "ri",
    "এ": "e", "ঐ": "oi", "ও": "o", "ঔ": "ou",
    "া": "a", "ি": "i", "ী": "i", "ু": "u",
    "ূ": "u", "ৃ": "ri",
    "ে": "e", "ৈ": "oi", "ো": "o", "ৌ": "ou",
    "ক": "k", "খ": "kh", "গ": "g", "ঘ": "gh", "ঙ": "ng",
    "চ": "ch", "ছ": "chh", "জ": "j", "ঝ": "jh", "ঞ": "n",
    "ট": "t", "ঠ": "th", "ড": "d", "ঢ": "dh", "ণ": "n",
    "ত": "t", "থ": "th", "দ": "d", "ধ": "dh", "ন": "n",
    "প": "p", "ফ": "ph", "ব": "b", "ভ": "bh", "ম": "m",
    "য": "j", "র": "r", "ল": "l",
    "শ": "sh", "ষ": "sh", "স": "s", "হ": "h",
    "ড়": "r", "ঢ়": "rh", "য়": "y", "ৎ": "t",
    "ং": "ng", "ঃ": "h", "ঁ": "n", "্": "",
    "০": "0", "১": "1", "২": "2", "৩": "3", "৪": "4",
    "৫": "5", "৬": "6", "৭": "7", "৮": "8", "৯": "9",
}


def _compose_nukta(text: str) -> str:
    """Join the three letters written as base + nukta.

    NFC will not do this: BENGALI LETTER YYA, RRA and RHA are Unicode
    composition exclusions, so normalize() leaves the decomposed pair alone.
    Tesseract emits the decomposed form, and without this the base letter is
    what gets looked up - "ya" reads as "ja", so "noyamati" transliterates to
    "njamati" and never matches what a user types.
    """
    for base, joined in (("য", "য়"),   # ya
                         ("ড", "ড়"),   # rra
                         ("ঢ", "ঢ়")):  # rha
        text = text.replace(base + "়", joined)
    return text


def transliterate(text: str) -> str:
    """Bengali to searchable Latin. Never shown to a user."""
    text = _compose_nukta(unicodedata.normalize("NFC", text))
    out = []
    for ch in text:
        if ch in _TRANSLIT:
            out.append(_TRANSLIT[ch])
        elif ch.isascii() and ch.isalnum():
            out.append(ch.lower())
        elif ch in " ,.-()/":
            out.append(ch)
    return re.sub(r"\s+", " ", "".join(out)).strip()


def clean(text: str) -> str:
    """Strip what carries no meaning. Never changes a word."""
    text = _compose_nukta(unicodedata.normalize("NFC", _ZERO_WIDTH.sub("", text)))
    text = text.replace("|", " ").replace("৷", ",")
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s*,\s*", ", ", text)
    text = re.sub(r"(,\s*)+", ", ", text)
    return text.strip(" ,.-।")


def split_areas(text: str) -> list[str]:
    """The area cell is a comma-separated list of places. Return them."""
    parts = [clean(p) for p in re.split(r"[,،৷/]", text)]
    seen, out = set(), []
    for p in parts:
        # A fragment with no Bengali and no ASCII letters is OCR noise.
        if len(p) < 2 or not (_BENGALI.search(p) or re.search(r"[A-Za-z]", p)):
            continue
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


#: Bengali letters, excluding the digits and the combining marks.
_BN_LETTER = re.compile(r"[অ-হৎড়-য়]")


def looks_like_a_name(text: str) -> bool:
    """True if this reads as a place name rather than a number or a smudge.

    Azimpur's sheet has a numeric column wide enough to look like text, and it
    was being published as feeder names: "৯২৫", "০০", "শপ ০৮". A number is not
    an identifier a resident can recognise, and printing one in the name field
    is worse than leaving it empty.
    """
    return len(_BN_LETTER.findall(text)) >= 3


def _words(text: str) -> set[str]:
    """Transliterated words long enough to be worth comparing."""
    return {w for w in transliterate(text).replace(",", " ").split() if len(w) >= 3}


def _first_area(text: str) -> str:
    """The first place in a comma-separated area list.

    Matching a candidate name against the *whole* list is too generous. Fatulla
    writes its areas as "<street> সংলগ্ন এলাকা, ফতুল্লা", so a column holding
    the grid substation name matched on the trailing thana and was published as
    the feeder name. A feeder named for a place leads with it.
    """
    return re.split(r"[,،৷/]", text, maxsplit=1)[0] if text else ""


def _shares_a_word(a: str, b: str) -> bool:
    """Do these two cells name the same place?

    Compared after transliteration so that two different misreadings of the
    same Bengali word still match.
    """
    if not a or not b:
        return False
    return bool(_words(a) & _words(b))


def bengali_ratio(text: str) -> float:
    letters = [c for c in text if not c.isspace()]
    if not letters:
        return 0.0
    return len(_BENGALI.findall(text)) / len(letters)


def _crop(gray, rows, cols, ri, ci, pad=4):
    y0, y1 = rows[ri] + pad, rows[ri + 1] - pad
    x0, x1 = cols[ci] + pad, cols[ci + 1] - pad
    if y1 <= y0 or x1 <= x0:
        return None
    cell = gray[y0:y1, x0:x1]
    return cell if cell.size else None


def _prep(cell, scale=3.0):
    """Upscale and pad. Tesseract needs both: it is trained near 300dpi text and
    it clips glyphs that touch the edge of the image."""
    import cv2
    up = cv2.resize(cell, None, fx=scale, fy=scale,
                    interpolation=cv2.INTER_LANCZOS4)
    return cv2.copyMakeBorder(up, 25, 25, 25, 25, cv2.BORDER_CONSTANT, value=255)


def read_cell(ocr, gray, rows, cols, ri, ci, lang="ben", psm=6, allow=""):
    cell = _crop(gray, rows, cols, ri, ci)
    if cell is None or cell.shape[1] < 12:
        return ""
    return clean(ocr(_prep(cell), allow, psm, lang))


#: Page-segmentation modes tried on free-text cells, in order. 6 treats the cell
#: as a uniform block, 4 as columns of variable-width text, 11 as sparse text.
TEXT_PSMS = (6, 4, 11)


def read_text_cell(ocr, gray, rows, cols, ri, ci, lang="ben"):
    """Read a free-text cell under several layout assumptions, keep the fullest.

    Area cells wrap to two lines at unpredictable points. A single psm drops a
    line often enough to matter: on one Narayanganj row psm 6 returned only the
    trailing word of a three-place list. Trying a few and keeping the longest
    recovers those without inventing anything, because every candidate is a
    genuine reading of the same pixels.
    """
    cell = _crop(gray, rows, cols, ri, ci)
    if cell is None or cell.shape[1] < 12:
        return ""
    img = _prep(cell)
    primary = clean(ocr(img, "", TEXT_PSMS[0], lang))
    best = primary
    for psm in TEXT_PSMS[1:]:
        got = clean(ocr(img, "", psm, lang))
        # Only override the primary mode when the alternative found a whole
        # line the primary missed. A marginally longer string is usually a
        # marginally worse one - more noise glyphs, not more places.
        if len(got) > max(len(best), len(primary) * 1.5):
            best = got
    return best


def classify(ocr, gray, rows, cols, header_row, meta_cols):
    """Decide what each metadata column holds. Returns {role: column index}.

    Roles: 'code', 'name', 'area', 'load'. Any may be absent.
    """
    if meta_cols <= 0:
        return {}

    stop = min(len(rows) - 1, header_row + 1 + CLASSIFY_ROWS)
    sample_rows = list(range(header_row + 1, stop))
    if not sample_rows:
        return {}

    profile = []
    for ci in range(min(meta_cols, len(cols) - 1)):
        width = cols[ci + 1] - cols[ci]
        texts_bn, texts_en = [], []
        for ri in sample_rows:
            if width >= MIN_TEXT_WIDTH:
                texts_bn.append(read_cell(ocr, gray, rows, cols, ri, ci, "ben", 6))
            texts_en.append(read_cell(
                ocr, gray, rows, cols, ri, ci, "eng", 7,
                "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-"))
        bn = " ".join(t for t in texts_bn if t)
        en = " ".join(t for t in texts_en if t)
        profile.append({
            "ci": ci,
            "width": width,
            "bn": texts_bn,
            "en": texts_en,
            "kv": sum(1 for t in texts_bn
                      if any(m in t for m in NAME_MARKERS)),
            "bn_ratio": bengali_ratio(bn),
            "codes": sum(1 for t in texts_en if _CODE_RE.search(t.upper())),
            "len": max((len(t) for t in texts_bn), default=0),
        })

    roles: dict[str, int] = {}

    # Text columns, longest content first. The area list is always the longest
    # free text on the row; a feeder name is a word or two.
    text_cols = [p for p in profile
                 if p["width"] >= MIN_TEXT_WIDTH and p["bn_ratio"] > 0.4]
    text_cols.sort(key=lambda p: (p["len"], p["width"]), reverse=True)

    if text_cols:
        roles["area"] = text_cols[0]["ci"]

    # Feeder name. A type marker settles it when the sheet uses one, but many
    # zones do not: Khilgaon names its feeders "পূর্ব রামপুরা" with no prefix at
    # all. So fall back to matching the first area the feeder serves.
    kv = [p for p in profile if p["kv"] >= 2]
    if kv:
        roles["name"] = max(kv, key=lambda p: p["kv"])["ci"]
        if roles.get("area") == roles["name"] and len(text_cols) > 1:
            roles["area"] = text_cols[1]["ci"]
    else:
        # No marker, so use the sheet's own redundancy: a feeder named
        # "পূর্ব রামপুরা" leads its area list with পূর্ব রামপুরা. Scoring
        # candidates by that beats every positional tie-break - picking the
        # wider column instead handed Khilgaon a column of smudges that happened
        # to be 17px wider than its real name column.
        area_texts = next((p["bn"] for p in profile
                           if p["ci"] == roles.get("area")), [])
        best = None
        for p in text_cols[1:]:
            if "area" in roles and p["ci"] > roles["area"]:
                continue
            # A column repeating one value on every row is a zone label, not a
            # feeder name: Fatulla's says "ফতুল্লা" 33 times.
            distinct = {t for t in p["bn"] if t}
            if len(distinct) < 3:
                continue
            # ...and it has to read as words, not as a number column that
            # happens to be wide enough to look like text.
            named = [t for t in distinct if looks_like_a_name(t)]
            if len(named) * 2 < len(distinct):
                continue
            overlap = sum(1 for t, a in zip(p["bn"], area_texts)
                          if _shares_a_word(t, _first_area(a)))
            if overlap >= 2 and (best is None or overlap > best[0]):
                best = (overlap, p["ci"])
        # No overlap anywhere means we cannot tell which column is the name.
        # Publishing nothing beats publishing the wrong column.
        if best:
            roles["name"] = best[1]

    # Feeder code: most rows parse as a billing code, and it is not text.
    taken = set(roles.values())
    codes = [p for p in profile if p["codes"] >= 2 and p["ci"] not in taken]
    if codes:
        roles["code"] = min(codes, key=lambda p: p["ci"])["ci"]

    # Load MW: whatever is left that reads as a decimal number. Width is the
    # wrong discriminator - Narayanganj's load column is 305px while the empty
    # column beside it is 245px - so require the decimal point instead, which a
    # serial number never has.
    taken = set(roles.values())
    best = None
    for p in profile:
        if p["ci"] in taken:
            continue
        decimals = [m for m in (_DECIMAL_RE.search(t) for t in p["en"]) if m]
        if len(decimals) >= 2 and (best is None or len(decimals) > best[0]):
            best = (len(decimals), p["ci"])
    if best:
        roles["load"] = best[1]

    return roles


def read_row(ocr, gray, rows, cols, ri, roles):
    """Everything the metadata columns say about one feeder."""
    out: dict = {"feeder_name": "", "area_text": "", "areas": [],
                 "load_mw": None, "billing_code": ""}

    if "name" in roles:
        name = read_text_cell(ocr, gray, rows, cols, ri, roles["name"])
        # Per row as well as per column: a good name column still has the odd
        # cell that comes back as a stray mark or a number.
        out["feeder_name"] = name if looks_like_a_name(name) else ""

    if "area" in roles:
        raw = read_text_cell(ocr, gray, rows, cols, ri, roles["area"])
        out["areas"] = split_areas(raw)
        out["area_text"] = ", ".join(out["areas"])

    if "code" in roles:
        # One psm missed a quarter of the codes on its own. These are short
        # fixed-shape tokens, so the first mode that yields something matching
        # the code pattern is the answer; there is nothing to choose between.
        for psm in CODE_PSMS:
            raw = read_cell(ocr, gray, rows, cols, ri, roles["code"], "eng", psm,
                            "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-")
            m = _CODE_RE.search(raw.upper().replace(" ", ""))
            if m:
                out["billing_code"] = m.group(1)
                break

    if "load" in roles:
        raw = read_cell(ocr, gray, rows, cols, ri, roles["load"], "eng", 7,
                        "0123456789.,")
        if not _DECIMAL_RE.search(raw):
            # Several zones print the load in Bengali numerals.
            raw = read_cell(ocr, gray, rows, cols, ri, roles["load"], "ben", 7)
            raw = raw.translate(_BN_DIGITS)
        m = _DECIMAL_RE.search(raw)
        if m:
            try:
                val = float(m.group(0).replace(",", "."))
                # An 11kV distribution feeder carries roughly 0.5-15 MW. A
                # reading outside that band is a misplaced decimal or a serial
                # number, and a wrong load is worse than no load.
                if MIN_LOAD_MW <= val <= MAX_LOAD_MW:
                    out["load_mw"] = round(val, 2)
            except ValueError:
                pass

    return out


def search_text(feeder_name: str, areas: list[str]) -> str:
    """Latin text handed to the confidence ranker."""
    parts = [transliterate(a) for a in areas]
    if feeder_name:
        # Drop the "11 kV" prefix; it is on every feeder and matches nothing.
        name = feeder_name.replace(KV_MARKER, " ")
        name = re.sub(r"[০-৯0-9]+", " ", name)
        parts.insert(0, transliterate(name))
    seen, out = set(), []
    for p in parts:
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return ", ".join(out)
