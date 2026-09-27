"""SQLite storage, flags and stats (WO-002a §3.5, §3.6)."""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from pathlib import Path

from .merge import Entry
from .ocr import episode_number

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS episodes (
  episode INTEGER PRIMARY KEY,
  file TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  duration REAL,
  content_start REAL,          -- reference boundary, only used for the BOUNDARY_REGION flag
  content_end REAL,
  boundary_source TEXT,
  n_frames INTEGER,
  n_entries INTEGER,
  decode_s REAL,
  ocr_s REAL,
  ocr_params TEXT,
  merge_version INTEGER,
  built_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS entries (
  id INTEGER PRIMARY KEY,
  episode INTEGER NOT NULL,
  t_start REAL NOT NULL,       -- seconds from the start of the episode file
  t_end REAL NOT NULL,
  text TEXT NOT NULL,          -- raw OCR spelling (most frequent, then most confident)
  norm TEXT NOT NULL,          -- Han characters, digits and letters only
  conf REAL,
  n_frames INTEGER,
  n_agree INTEGER,
  variants TEXT,               -- JSON {spelling: frames}
  flags TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS entries_episode ON entries(episode, t_start);
CREATE INDEX IF NOT EXISTS entries_norm ON entries(norm);
"""

FLAGS = ("OPENING_ENDING", "RECAP", "BOUNDARY_REGION", "SHORT", "LYRIC")
SHORT_LEN = 4                # fewer than 4 characters -> SHORT
OPENING_ENDING_MIN_EPS = 3   # same text in >= 3 episodes -> OPENING_ENDING

# LYRIC: sung or chanted voice-over that uploaders reuse as background audio, so a text match does
# not show the episode's picture. Marked from a reviewed list of time ranges per work
# (pilot/textindex/lyrics/<work>.json). Text-only rules were tried and rejected on this drama: lines
# repeated in >= 3 episodes are stock dialogue ("既然如此", "你说什么"), runs of verse-like short lines
# are mostly narration and formal dialogue, and 4-line blocks repeated in >= 3 episodes are flashbacks.
LYRICS_DIR = Path(__file__).with_name("lyrics")


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def episode_rows(con: sqlite3.Connection) -> dict[int, sqlite3.Row]:
    return {r["episode"]: r for r in con.execute("SELECT * FROM episodes")}


def replace_episode(con: sqlite3.Connection, header: dict, file_name: str, bounds: tuple[float, float, str],
                    entries: list[Entry], merge_version: int) -> None:
    ep = header["episode"]
    with con:
        con.execute("DELETE FROM entries WHERE episode = ?", (ep,))
        con.executemany(
            "INSERT INTO entries (episode, t_start, t_end, text, norm, conf, n_frames, n_agree, variants)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(e.episode, e.t_start, e.t_end, e.text, e.norm, e.conf, e.n_frames, e.n_agree,
              json.dumps(e.variants, ensure_ascii=False)) for e in entries])
        con.execute(
            "INSERT OR REPLACE INTO episodes (episode, file, fingerprint, duration, content_start, content_end,"
            " boundary_source, n_frames, n_entries, decode_s, ocr_s, ocr_params, merge_version)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ep, file_name, header["fingerprint"], header["duration"], bounds[0], bounds[1], bounds[2],
             header["n_frames"], len(entries), header.get("decode_s"), header.get("ocr_s"),
             json.dumps(header["params"], sort_keys=True), merge_version))


def load_boundaries(path: Path | None) -> tuple[dict, float, float]:
    """Reference boundaries file -> ({episode: (start, end)}, content_start, end offset from duration).

    Episodes are keyed by number (the file lists EP01.ts ...; the episodes may have been renamed since).
    Episodes not in the file get the file's conservative rule (default 140 s / duration - 200 s).
    """
    if path is None or not path.exists():
        return {}, 140.0, 200.0
    data = json.loads(path.read_text(encoding="utf-8"))
    rule = data.get("conservative_rule", {})
    per_ep = {}
    for name, b in data.get("episodes", {}).items():
        ep = episode_number(name)
        if ep is not None:
            per_ep[ep] = (b["content_start_s"], b["content_end_s"])
    return per_ep, rule.get("content_start_s", 140.0), rule.get("content_end_offset_from_duration_s", 200.0)


def episode_bounds(boundaries: tuple[dict, float, float], episode: int, duration: float) -> tuple[float, float, str]:
    per_ep, start, end_offset = boundaries
    if episode in per_ep:
        return (*per_ep[episode], "file")
    return start, duration - end_offset, "rule"


def load_lyric_ranges(path: Path | None) -> list[dict]:
    """[{"episode", "start", "end", "note"}, ...]; an absent file means no LYRIC ranges."""
    if path is None or not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def recompute_flags(con: sqlite3.Connection, lyric_ranges: list[dict] = ()) -> None:
    """Flags only down-weight entries; nothing is deleted. Recomputed over all episodes after every build."""
    bounds = {r["episode"]: (r["content_start"], r["content_end"]) for r in con.execute("SELECT * FROM episodes")}
    rows = con.execute("SELECT id, episode, t_start, t_end, text, norm FROM entries ORDER BY episode, t_start").fetchall()
    eps_by_norm: dict[str, set[int]] = defaultdict(set)
    for r in rows:
        eps_by_norm[r["norm"]].add(r["episode"])
    lyric = {r["id"] for r in rows for g in lyric_ranges
             if r["episode"] == g["episode"] and r["t_start"] < g["end"] and r["t_end"] > g["start"]}
    updates = []
    for r in rows:
        eps = eps_by_norm[r["norm"]]
        flags = []
        if len(eps) >= OPENING_ENDING_MIN_EPS:
            flags.append("OPENING_ENDING")
        if r["episode"] - 1 in eps or r["episode"] + 1 in eps:
            flags.append("RECAP")
        lo, hi = bounds.get(r["episode"], (None, None))
        mid = (r["t_start"] + r["t_end"]) / 2
        if lo is not None and (mid < lo or mid > hi):
            flags.append("BOUNDARY_REGION")
        if len(r["norm"]) < SHORT_LEN:
            flags.append("SHORT")
        if r["id"] in lyric:
            flags.append("LYRIC")
        updates.append((",".join(flags), r["id"]))
    with con:
        con.executemany("UPDATE entries SET flags = ? WHERE id = ?", updates)


def compute_stats(con: sqlite3.Connection) -> dict:
    eps = con.execute("SELECT * FROM episodes ORDER BY episode").fetchall()
    rows = con.execute("SELECT episode, norm, flags FROM entries").fetchall()

    def flag_summary(rs) -> dict:
        """Share of entries carrying each flag, over all entries and over entries with >= 4 characters."""
        long_rs = [r for r in rs if len(r["norm"]) >= SHORT_LEN]

        def share(subset, name):
            hits = sum(1 for r in subset if (bool(r["flags"]) if name == "ANY" else name in r["flags"].split(",")))
            return hits, round(hits / len(subset), 4) if subset else 0.0

        out = {}
        for name in FLAGS + ("ANY",):
            n, ratio = share(rs, name)
            out[name] = {"n": n, "ratio": ratio, "ratio_ge4": share(long_rs, name)[1]}
        return out

    by_ep = defaultdict(list)
    for r in rows:
        by_ep[r["episode"]].append(r)
    eps_by_norm: dict[str, set[int]] = defaultdict(set)
    for r in rows:
        eps_by_norm[r["norm"]].add(r["episode"])
    long_norms = [n for n in eps_by_norm if len(n) >= SHORT_LEN]
    multi_all = sum(1 for n in eps_by_norm if len(eps_by_norm[n]) >= 2)
    multi_long = sum(1 for n in long_norms if len(eps_by_norm[n]) >= 2)
    entries_multi = sum(1 for r in rows if len(eps_by_norm[r["norm"]]) >= 2)
    return {
        "episodes": len(eps),
        "entries": len(rows),
        "frames": sum(e["n_frames"] or 0 for e in eps),
        "flags": flag_summary(rows),
        "multi_episode": {
            "distinct_texts": len(eps_by_norm),
            "distinct_texts_in_2plus_episodes": multi_all,
            "ratio": round(multi_all / len(eps_by_norm), 4) if eps_by_norm else 0.0,
            "distinct_texts_ge4": len(long_norms),
            "distinct_texts_ge4_in_2plus_episodes": multi_long,
            "ratio_ge4": round(multi_long / len(long_norms), 4) if long_norms else 0.0,
            "entries_in_2plus_episodes_ratio": round(entries_multi / len(rows), 4) if rows else 0.0,
        },
        "per_episode": [
            {"episode": e["episode"], "file": e["file"], "duration_s": round(e["duration"], 1),
             "frames": e["n_frames"], "entries": e["n_entries"],
             "content_region": [e["content_start"], round(e["content_end"], 1), e["boundary_source"]],
             "decode_s": e["decode_s"], "ocr_s": e["ocr_s"],
             "flag_ratio": {f: v["ratio"] for f, v in flag_summary(by_ep[e["episode"]]).items()}}
            for e in eps],
    }
