"""Legacy Bijoy / SutonnyMJ ASCII -> Unicode Bangla.

Bijoy predates Unicode. It maps Bangla glyphs onto ASCII codepoints, so a PDF set
in SutonnyMJ extracts as `jvWkwWs wkwWDj` while rendering correctly as
"লোডশেডিং শিডিউল". This module reverses that mapping.

The conversion is not a byte-for-byte table, because Bijoy stores glyphs in
VISUAL order while Unicode stores them in LOGICAL order:

  * the i-kar `ি` and e-kar `ে`/`ৈ` are typed BEFORE their consonant but belong
    after it in Unicode
  * reph `র্` is typed after the consonant it rides on, but belongs before it
  * conjuncts are multi-byte sequences and must be matched longest-first

Verified against rendered pages of DPDC's own zone sheets, where the correct
Bangla is visible next to the ASCII the text layer yields:

    jvWkwWs wkwWDj  ->  লোডশেডিং শিডিউল   (probable load-shedding schedule)
    gwZwSj          ->  মতিঝিল             (Motijheel)
    wdWvi           ->  ফিডার               (feeder)
    GjvKvi bvg      ->  এলাকার নাম          (area name)

HONESTY NOTE
Decoding is best effort. `looks_like_bijoy` gates it, and callers must keep the
raw string when `decode_bijoy` returns something that still fails
`is_plausible_bangla`. A confidently wrong Bangla string is worse than visible
mojibake, because it reads as authoritative.
"""
from __future__ import annotations

import re

# Longest-first conjunct and compound sequences.
_CONJUNCTS: list[tuple[str, str]] = [
    ("÷", "্র"), ("ª", "্র"), ("¡", "্ব"), ("¬", "্ল"), ("Ë", "ত্ত"),
    ("š^", "ন্ব"), ("Ø", "ষ্ট"), ("®Í", "স্ত"), ("¯Í", "স্ত"), ("¯^", "স্ব"),
    ("š—", "ন্ত"), ("Ç", "ণ্ট"), ("×", "দ্ধ"), ("Ø¡", "ষ্ট্ব"),
    ("°", "ক্ক"), ("³", "ক্ত"), ("¯", "স"), ("©", "র্"),
    ("ˆ", "ৈ"), ("Š", "ৌ"), ("‹", "ঞ"),
    ("„", "ৃ"), ("…", "ৃ"), ("†", "ে"), ("‡", "ে"),
    ("ª", "্র"), ("¨", "্য"), ("«", "্র"),
]

# Single-character map. Derived from the SutonnyMJ keyboard layout and checked
# against the rendered DPDC pages.
_SINGLE: dict[str, str] = {
    "A": "অ", "B": "ই", "C": "ঈ", "D": "উ", "E": "ঊ", "F": "ঋ",
    "G": "এ", "H": "ঐ", "I": "ও", "J": "ঔ",
    "K": "ক", "L": "খ", "M": "গ", "N": "ঘ", "O": "ঙ",
    "P": "চ", "Q": "ছ", "R": "জ", "S": "ঝ", "T": "ঞ",
    "U": "ট", "V": "ঠ", "W": "ড", "X": "ঢ", "Y": "ণ",
    "Z": "ত", "_": "থ", "`": "দ", "a": "ধ", "b": "ন",
    "c": "প", "d": "ফ", "e": "ব", "f": "ভ", "g": "ম",
    "h": "য", "i": "র", "j": "ল", "k": "শ", "l": "ষ",
    "m": "স", "n": "হ", "o": "ড়", "p": "ঢ়", "q": "য়",
    "r": "ৎ", "s": "ং", "t": "ঃ", "u": "ঁ",
    "v": "া", "w": "ি", "x": "ী", "y": "ু", "z": "ূ",
    "„": "ৃ", "†": "ে", "‰": "ঐ", "‡": "ে", "ˆ": "ৈ",
    "•": "ৗ", "\\": "্",
    "0": "০", "1": "১", "2": "২", "3": "৩", "4": "৪",
    "5": "৫", "6": "৬", "7": "৭", "8": "৮", "9": "৯",
    "|": "।", "Ô": "‘", "Õ": "’",
}

#: Vowel signs that Bijoy stores before their consonant.
_PRE_BASE = {"ি", "ে", "ৈ"}
#: The two-part vowels Bijoy splits around the consonant.
_SPLIT_VOWELS = {("ে", "া"): "ো", ("ে", "ৗ"): "ৌ"}

_BANGLA = re.compile(r"[ঀ-৿]")
#: Bijoy text is Latin letters with a scattering of high-Latin glyph bytes.
_BIJOY_HINT = re.compile(r"[ -ÿ‘-”†-‰]")


def looks_like_bijoy(s: str) -> bool:
    """Cheap gate: mostly ASCII letters, no real Bangla, and not plain English."""
    if not s or _BANGLA.search(s):
        return False
    letters = [c for c in s if c.isalpha()]
    if len(letters) < 3:
        return False
    # Real English in these documents is short codes like "A133E" or "RMU".
    if re.fullmatch(r"[A-Z0-9 ./()-]+", s):
        return False
    weird = len(_BIJOY_HINT.findall(s))
    lower = sum(1 for c in letters if c.islower())
    return weird > 0 or lower / max(1, len(letters)) > 0.35


def decode_bijoy(s: str) -> str:
    """Convert one Bijoy/SutonnyMJ string to Unicode Bangla."""
    if not s:
        return ""

    # A leading ˆ/‰ before a consonant is the oi-kar, not the independent vowel.
    s = re.sub(r"[ˆ‰](?=[A-Za-z`_])", "ˆ", s)

    # 1. Longest-first replacement of conjunct sequences.
    out: list[str] = []
    i = 0
    conj = sorted(_CONJUNCTS, key=lambda kv: -len(kv[0]))
    while i < len(s):
        for src, dst in conj:
            if s.startswith(src, i):
                out.append(dst)
                i += len(src)
                break
        else:
            out.append(_SINGLE.get(s[i], s[i]))
            i += 1
    text = "".join(out)

    # 2. Move pre-base vowel signs after their consonant cluster.
    chars = list(text)
    result: list[str] = []
    pending: list[str] = []
    for ch in chars:
        if ch in _PRE_BASE:
            pending.append(ch)
            continue
        result.append(ch)
        if pending and _is_consonant(ch):
            # Consume any hasant-joined cluster before placing the vowel.
            result.extend(pending)
            pending = []
    result.extend(pending)
    text = "".join(result)

    # 3a. Bijoy writes আ as অ + া; Unicode has a single codepoint for it.
    text = text.replace("অা", "আ").replace("অা", "আ")

    # 3b. Recombine split vowels (ে + া -> ো).
    for (a, b), combined in _SPLIT_VOWELS.items():
        text = text.replace(a + b, combined)

    # 4. Reph: র্ typed after its consonant belongs before it.
    text = re.sub(r"([ক-হ])র্", r"র্\1", text)

    return text


def _is_consonant(ch: str) -> bool:
    return "ক" <= ch <= "হ" or ch in "ড়ঢ়য়"


def is_plausible_bangla(s: str) -> bool:
    """Did decoding actually produce Bangla, or just rearranged noise?"""
    if not s:
        return False
    bangla = len(_BANGLA.findall(s))
    return bangla >= 2 and bangla / max(1, len(s.replace(" ", ""))) > 0.6


def decode_if_confident(s: str) -> tuple[str, bool]:
    """Decode only when the result looks like real Bangla. Returns (text, decoded)."""
    if not looks_like_bijoy(s):
        return s, False
    out = decode_bijoy(s)
    if is_plausible_bangla(out):
        return out, True
    return s, False
