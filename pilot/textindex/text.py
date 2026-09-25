"""Text normalization and string similarity (WO-002a §3.3, §3.4, §3.7)."""
from __future__ import annotations

import re
import unicodedata

from .t2s import T2S

# Han ideographs (incl. ext. A/B-G and U+3007 "〇"), ASCII digits and letters. Everything else is dropped.
_KEEP = re.compile(r"[0-9a-z〇㐀-䶿一-鿿豈-﫿\U00020000-\U0003134f]+")


def normalize(text: str) -> str:
    """Keep only Han characters, digits and Latin letters; drop punctuation and spaces.

    NFKC folds full-width digits/letters to ASCII, traditional characters become simplified
    (subtitles re-typed by uploaders are often traditional), letters are lower-cased.
    """
    return "".join(_KEEP.findall(unicodedata.normalize("NFKC", text).translate(T2S).lower()))


def edit_distance(a: str, b: str) -> int:
    """Levenshtein distance."""
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def lcs_len(a: str, b: str) -> int:
    """Length of the longest common subsequence."""
    if len(a) < len(b):
        a, b = b, a
    prev = [0] * (len(b) + 1)
    for ca in a:
        cur = [0]
        for j, cb in enumerate(b, 1):
            cur.append(prev[j - 1] + 1 if ca == cb else max(prev[j], cur[j - 1]))
        prev = cur
    return prev[-1]


def similarity(a: str, b: str) -> float:
    """Normalized insert/delete similarity 2*LCS/(|a|+|b|) in [0, 1] (same measure as rapidfuzz fuzz.ratio)."""
    if not a and not b:
        return 1.0
    return 2.0 * lcs_len(a, b) / (len(a) + len(b))


def grams(s: str) -> set[str]:
    """Character 2-grams used by the inverted index; a 1-char string is its own gram."""
    if len(s) < 2:
        return {s} if s else set()
    return {s[i:i + 2] for i in range(len(s) - 1)}
