"""Tests for the metadata reader on scanned DPDC sheets.

These pin the judgement calls, not the OCR engine. The engine's accuracy is
measured against real sheets in docs/SCAN_OCR_FINDINGS.md; what matters here is
that the code around it refuses to invent things, and that the column classifier
survives the layout differences between zones that broke it once already.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workers.parsers import scan_text_v1 as T  # noqa: E402


# ------------------------------------------------------------ transliteration

def test_nukta_letters_survive_decomposition():
    """Tesseract emits 'ya' decomposed as 'ja' + nukta.

    Without NFC composition it transliterates to 'j', turning "noyamati" into
    "njamati" and breaking every search for the area.
    """
    composed = "য়"       # ya, as one codepoint after NFC
    decomposed = "য়"     # what OCR actually returns
    assert T.transliterate(composed) == T.transliterate(decomposed)
    assert T.transliterate("নয়া") == "nya"


def test_transliteration_is_latin_only():
    out = T.transliterate("নয়ামাটি, আনন্দ হোটেল")
    assert out.isascii()
    assert "," in out


def test_search_text_drops_the_kv_prefix_and_deduplicates():
    """Every feeder is "11 kV something", so the prefix matches everything."""
    got = T.search_text("১১ কেভি নয়ামাটি", ["নয়ামাটি", "আনন্দ হোটেল"])
    assert "kebhi" not in got
    assert got.count("nyamati") == 1


# -------------------------------------------------------------- area splitting

#: "Noyamati" with YYA as the single composed codepoint U+09DF. Spelled out
#: because the composed and decomposed forms look identical in an editor, and
#: split_areas returns the composed one.
NOYAMATI = "নয়ামাটি"


def test_split_areas_drops_noise_fragments_not_real_ones():
    areas = T.split_areas("নয়ামাটি, ., আনন্দ হোটেল, x")
    assert areas == [NOYAMATI, "আনন্দ হোটেল"]


def test_split_areas_keeps_latin_place_names():
    assert T.split_areas("B.B Road, নয়ামাটি") == ["B.B Road", NOYAMATI]


def test_clean_never_rewrites_a_word():
    """OCR misreads are shown as-is next to their source scan.

    Dictionary-correcting place names is how you publish an area that does not
    exist, so cleaning is limited to characters that carry no meaning.
    """
    misread = "হটেল"          # sheet says হোটেল; we must not "fix" it
    assert T.clean(misread) == misread


# ------------------------------------------------------------------ classifier

class _Cell(tuple):
    """Stands in for an image crop: hashable as (row, column), has a shape."""
    shape = (40, 200)


class _FakeOCR:
    """Returns canned text per (row, column), ignoring pixels."""

    def __init__(self, cells, ascii_cells=None):
        self.cells = cells
        self.ascii_cells = ascii_cells or {}

    def __call__(self, img, allow, psm, lang):
        key = img  # _crop is stubbed to return the (ri, ci) tuple itself
        table = self.ascii_cells if lang == "eng" else self.cells
        return table.get(key, "")


def _classify(monkey_cells, ascii_cells, widths, rows=8):
    """Run classify() against canned cell text instead of an image."""
    cols = [0]
    for w in widths:
        cols.append(cols[-1] + w)
    row_edges = list(range(0, (rows + 1) * 40, 40))

    ocr = _FakeOCR(monkey_cells, ascii_cells)

    # Stub the image plumbing: _crop hands back its own coordinates, _prep is a
    # no-op, so the fake OCR can key off (row, column).
    orig_crop, orig_prep = T._crop, T._prep
    T._crop = lambda gray, r, c, ri, ci, pad=4: _Cell((ri, ci))
    T._prep = lambda cell, scale=3.0: cell
    try:
        return T.classify(ocr, None, row_edges, cols, 0, len(widths))
    finally:
        T._crop, T._prep = orig_crop, orig_prep


def test_classifier_finds_name_by_the_kv_marker():
    """Narayanganj's layout: serial, code, blank, name, load, areas."""
    bn = {}
    en = {}
    for ri in range(1, 7):
        bn[(ri, 3)] = "১১ কেভি নয়ামাটি"
        bn[(ri, 5)] = "নয়ামাটি, আনন্দ হোটেল, প্লাস্টিক ম্যান"
        en[(ri, 1)] = "B722J"
        en[(ri, 4)] = "4.2"
    roles = _classify(bn, en, [147, 223, 245, 546, 305, 575])
    assert roles["name"] == 3
    assert roles["area"] == 5
    assert roles["code"] == 1
    assert roles["load"] == 4


def test_classifier_finds_name_without_a_kv_marker():
    """Khilgaon names feeders "পূর্ব রামপুরা" with no prefix at all.

    Requiring the kV marker found a name on only 2 of 17 zones.
    """
    bn, en = {}, {}
    names = ["পূর্ব রামপুরা", "বিটিভি", "চৌধুরী পাড়া", "বনশ্রী", "মেরাদিয়া", "নন্দীপাড়া"]
    for ri in range(1, 7):
        bn[(ri, 3)] = names[ri - 1]
        bn[(ri, 5)] = names[ri - 1] + ", হাই স্কুল রোড, আবাসিক এলাকা"
        en[(ri, 1)] = "A229B"
    roles = _classify(bn, en, [108, 173, 306, 289, 142, 626])
    assert roles["name"] == 3
    assert roles["area"] == 5


def test_a_column_repeating_one_value_is_not_a_feeder_name():
    """Fatulla's short text column says "ফতুল্লা" on all 33 rows.

    That is the thana, not an identifier, and publishing it as a feeder name
    would make every row look identical.
    """
    bn = {}
    for ri in range(1, 7):
        bn[(ri, 1)] = "ফতুল্লা"
        bn[(ri, 2)] = "নন্দলালপুর রোড সংলগ্ন এলাকা, ফতুল্লা"
    roles = _classify(bn, {}, [185, 166, 791])
    assert roles["area"] == 2
    assert "name" not in roles


def test_single_text_column_is_an_area_not_a_name():
    """Dhanmondi carries two metadata columns: a serial and one text column."""
    bn = {(ri, 1): "লালমাটিয়া" for ri in range(1, 7)}
    roles = _classify(bn, {}, [140, 240])
    assert roles["area"] == 1
    assert "name" not in roles


# ------------------------------------------------------------------- load band

def _read_load(text, lang_ascii=True):
    ocr = _FakeOCR({(1, 0): "" if lang_ascii else text},
                   {(1, 0): text if lang_ascii else ""})
    orig_crop, orig_prep = T._crop, T._prep
    T._crop = lambda gray, r, c, ri, ci, pad=4: _Cell((ri, ci))
    T._prep = lambda cell, scale=3.0: cell
    try:
        return T.read_row(ocr, None, [0, 40, 80], [0, 100], 1, {"load": 0})["load_mw"]
    finally:
        T._crop, T._prep = orig_crop, orig_prep


def test_load_accepts_a_plausible_feeder_load():
    assert _read_load("4.2") == 4.2


def test_load_rejects_an_implausible_reading():
    """An 11kV feeder does not carry 35 MW; that is a misplaced decimal.

    A wrong load is worse than no load, so the band is enforced rather than
    published with a caveat.
    """
    assert _read_load("35.0") is None


def test_load_requires_a_decimal_point():
    """Serial numbers read as integers. Only a decimal marks a real load."""
    assert _read_load("183") is None


def test_overhead_marker_identifies_the_name_column():
    """Tejgaon names feeders "<place> ওভারহেড", never "<voltage> কেভি".

    Keying only on the kV marker threw away 11 real feeder names here.
    """
    bn, en = {}, {}
    names = ["মগবাজার ওভারহেড", "জিগাতলা ওভারহেড", "নাখালপাড়া ওভারহেড",
             "তেজকুনী ওভারহেড", "ফার্মগেট ওভারহেড", "কারওয়ান ওভারহেড"]
    areas = ["চ্যানেল ২৪ গলি", "পুরাতন এফডিসি", "পূর্ব নাখালপাড়া আবাসিক",
             "তেজকুনী পাড়া", "ফার্মগেট এলাকা", "কারওয়ান বাজার"]
    for ri in range(1, 7):
        bn[(ri, 1)] = names[ri - 1]
        bn[(ri, 4)] = areas[ri - 1] + ", তেজগাঁও"
    roles = _classify(bn, en, [307, 572, 210, 237, 782])
    assert roles["name"] == 1
    assert roles["area"] == 4


def test_a_column_matching_only_the_trailing_area_is_not_a_name():
    """Fatulla lists areas as "<street> সংলগ্ন এলাকা, ফতুল্লা".

    A column holding the grid substation (ফতুল্লা, শ্যামপুর, মাতুয়াইল) matched
    on that trailing thana and was published as the feeder name. A feeder named
    for a place leads its area list with that place, so only the first area
    counts.
    """
    bn = {}
    subs = ["ফতুল্লা", "ফতুল্লা", "শ্যামপুর", "শ্যামপুর", "মাতুয়াইল", "ফতুল্লা"]
    streets = ["নন্দলালপুর রোড", "পঞ্চবটি রোড", "ডিএন রোড",
               "চাষাড়া রোড", "পাগলা রোড", "ইসদাইর রোড"]
    for ri in range(1, 7):
        bn[(ri, 1)] = subs[ri - 1]
        bn[(ri, 2)] = streets[ri - 1] + " সংলগ্ন এলাকা, " + subs[ri - 1]
    roles = _classify(bn, {}, [185, 166, 791])
    assert roles["area"] == 2
    assert "name" not in roles


def test_first_area_is_the_head_of_the_list():
    assert T._first_area("পূর্ব রামপুরা, হাই স্কুল রোড") == "পূর্ব রামপুরা"
    assert T._first_area("") == ""
