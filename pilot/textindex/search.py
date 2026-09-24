"""Single-line lookup: 2-gram inverted index for recall, LCS similarity for scoring (WO-002a §3.7)."""
from __future__ import annotations

import math
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from .text import grams, normalize, similarity

# Down-weights used when ranking ties and in the sequence matcher; flags never remove an entry.
# OPENING_ENDING and SHORT describe the text (generic wherever it occurs), RECAP and BOUNDARY_REGION the place.
FLAG_WEIGHTS = {"OPENING_ENDING": 0.3, "RECAP": 0.8, "BOUNDARY_REGION": 0.7, "SHORT": 0.5}
TEXT_FLAGS = ("OPENING_ENDING", "SHORT")
MIN_SHARED_GRAMS = 0.3   # recall: an entry must share >= 30% of the query's 2-grams


@dataclass(frozen=True)
class SubEntry:
    id: int
    episode: int
    t_start: float
    t_end: float
    text: str
    norm: str
    conf: float
    flags: tuple[str, ...]

    @property
    def weight(self) -> float:
        return math.prod(FLAG_WEIGHTS[f] for f in self.flags)

    @property
    def text_weight(self) -> float:
        return math.prod(FLAG_WEIGHTS[f] for f in self.flags if f in TEXT_FLAGS)

    @property
    def place_weight(self) -> float:
        return math.prod(FLAG_WEIGHTS[f] for f in self.flags if f not in TEXT_FLAGS)


@dataclass(frozen=True)
class Candidate:
    entry: SubEntry
    sim: float

    def as_dict(self) -> dict:
        e = self.entry
        return {"episode": e.episode, "t_start": e.t_start, "t_end": e.t_end, "sim": round(self.sim, 3),
                "text": e.text, "flags": list(e.flags), "entry_id": e.id}


class Index:
    def __init__(self, entries: list[SubEntry]):
        self.entries = entries
        self.postings: dict[str, list[int]] = defaultdict(list)
        for i, e in enumerate(entries):
            for g in grams(e.norm):
                self.postings[g].append(i)

    @classmethod
    def load(cls, db_path: Path) -> "Index":
        if not db_path.exists():
            raise FileNotFoundError(f"index not found: {db_path} (run the build command first)")
        con = sqlite3.connect(db_path)
        rows = con.execute(
            "SELECT id, episode, t_start, t_end, text, norm, conf, flags FROM entries ORDER BY episode, t_start").fetchall()
        con.close()
        return cls([SubEntry(r[0], r[1], r[2], r[3], r[4], r[5], r[6], tuple(f for f in r[7].split(",") if f))
                    for r in rows])

    def search(self, text: str, min_sim: float = 0.5, top: int | None = 10, keep_above: float = 0.9) -> list[Candidate]:
        """Candidates sorted by similarity (then flag weight, episode, time).

        Returns the `top` best, plus every other candidate with sim >= `keep_above`, so a line that
        matches in several episodes is always reported in full. top=None returns everything >= min_sim.
        """
        q = normalize(text)
        if not q:
            return []
        qg = grams(q)
        shared = Counter(i for g in qg for i in self.postings.get(g, ()))
        need = max(1, round(MIN_SHARED_GRAMS * len(qg)))
        cands = []
        for i, n in shared.items():
            if n < need:
                continue
            e = self.entries[i]
            s = similarity(q, e.norm)
            if s >= min_sim:
                cands.append(Candidate(e, s))
        cands.sort(key=lambda c: (-c.sim, -c.entry.weight, c.entry.episode, c.entry.t_start))
        if top is None:
            return cands
        return cands[:top] + [c for c in cands[top:] if c.sim >= keep_above]
