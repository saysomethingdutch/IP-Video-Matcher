"""Stage 2: per-frame OCR items -> subtitle text per frame -> subtitle entries (WO-002a §3.3)."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .text import edit_distance, normalize

MERGE_VERSION = 1        # bump when frame_text/merge_frames change; re-derives entries from the OCR cache
MIN_ITEM_CONF = 0.3      # Vision "accurate" confidences are coarse (0.3 / 0.5 / 1.0)
MIN_ITEM_H = 0.12        # item height, as a fraction of the strip; subtitles are ~0.2-0.3, credits/ads smaller
ROW_Y_BAND = (0.4, 0.9)  # vertical centre of a subtitle row within the strip (observed ~0.55-0.75)
MAX_CENTER_OFFSET = 0.12  # subtitles are horizontally centred; keep rows whose centre is within 0.5 +- this
MAX_ITEM_GAP = 0.08      # a wider horizontal gap splits a row (subtitle spaces are <= ~0.05 of the width)
MAX_GAP_S = 1.0          # bridge one missed frame at 2 fps
FUZZY_MIN_LEN = 3        # edit distance 1 only counts as "same line" when both texts have >= 3 chars


@dataclass
class Entry:
    episode: int
    t_start: float
    t_end: float
    text: str
    norm: str
    conf: float
    n_frames: int
    n_agree: int          # frames whose normalized text equals `norm`
    variants: dict        # raw spelling -> frame count


def _centred(items: list) -> bool:
    left = min(o[2] for o in items)
    right = max(o[2] + o[4] for o in items)
    return abs((left + right) / 2 - 0.5) <= MAX_CENTER_OFFSET


def _rows(items: list) -> list[list]:
    """Group items into rows: an item joins the previous row when its vertical centre falls inside it."""
    rows: list[list] = []
    for it in sorted(items, key=lambda o: o[3] + o[5] / 2):
        cy = it[3] + it[5] / 2
        if rows and min(o[3] for o in rows[-1]) <= cy <= max(o[3] + o[5] for o in rows[-1]):
            rows[-1].append(it)
        else:
            rows.append([it])
    return rows


def _subtitle_items(row: list) -> list:
    """Items of a row that form the centred subtitle; the row is split where items are far apart."""
    row = sorted(row, key=lambda o: o[2])
    pieces = [[row[0]]]
    for o in row[1:]:
        if o[2] - max(p[2] + p[4] for p in pieces[-1]) > MAX_ITEM_GAP:
            pieces.append([o])
        else:
            pieces[-1].append(o)
    kept = [o for piece in pieces if _centred(piece) for o in piece]
    return kept or (row if _centred(row) else [])   # fallback: a subtitle with a very wide space


def frame_text(items: list) -> tuple[str, float] | None:
    """Pick the subtitle line(s) out of one frame's OCR items [[text, conf, x, y, w, h], ...].

    Keeps horizontally centred text in rows sitting in the subtitle band; this drops opening credits,
    ad banners and background text beside the subtitle. Kept items are joined left to right with a
    space, rows top to bottom. Returns (raw text, confidence weighted by text length) or None.
    """
    good = [it for it in items if it[1] >= MIN_ITEM_CONF and it[5] >= MIN_ITEM_H and normalize(it[0])]
    texts, weight, total = [], 0.0, 0
    for row in _rows(good):
        cy = (min(o[3] for o in row) + max(o[3] + o[5] for o in row)) / 2
        kept = _subtitle_items(row) if ROW_Y_BAND[0] <= cy <= ROW_Y_BAND[1] else []
        if not kept:
            continue
        texts.append(" ".join(o[0].strip() for o in kept))
        for o in kept:
            n = len(normalize(o[0]))
            weight += o[1] * n
            total += n
    if not texts:
        return None
    return " ".join(texts), weight / total


def same_line(a: str, b: str) -> bool:
    if a == b:
        return True
    if min(len(a), len(b)) < FUZZY_MIN_LEN or abs(len(a) - len(b)) > 1:
        return False
    return edit_distance(a, b) <= 1


def _finish(episode: int, run: list[tuple[float, str, str, float]], frame_s: float) -> Entry:
    counts: dict[str, list[float]] = defaultdict(list)
    for _, raw, _, conf in run:
        counts[raw].append(conf)
    # most frequent spelling, then highest confidence, then the longest (fade-in frames lose characters)
    raw = max(counts, key=lambda r: (len(counts[r]), sum(counts[r]) / len(counts[r]), len(normalize(r))))
    norm = normalize(raw)
    return Entry(
        episode=episode, t_start=run[0][0], t_end=run[-1][0] + frame_s, text=raw, norm=norm,
        conf=round(sum(counts[raw]) / len(counts[raw]), 3), n_frames=len(run),
        n_agree=sum(1 for _, _, n, _ in run if n == norm), variants={r: len(c) for r, c in counts.items()})


def merge_frames(episode: int, frames: list[dict], fps: float) -> list[Entry]:
    """Merge consecutive frames showing the same (or 1-edit-apart) normalized text into entries."""
    frame_s = 1.0 / fps
    entries: list[Entry] = []
    run: list[tuple[float, str, str, float]] = []
    for fr in sorted(frames, key=lambda f: f["t"]):
        picked = frame_text(fr["items"])
        if picked is None:
            continue
        raw, conf = picked
        norm = normalize(raw)
        t = fr["t"]
        if run and (t - run[-1][0] > MAX_GAP_S + 1e-6 or not same_line(norm, run[-1][2])):
            entries.append(_finish(episode, run, frame_s))
            run = []
        run.append((t, raw, norm, conf))
    if run:
        entries.append(_finish(episode, run, frame_s))
    return entries
