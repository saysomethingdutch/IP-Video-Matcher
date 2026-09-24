"""WO-002a §4 regression cases, run against the built index (skipped if it has not been built yet).

Index: $TEXTINDEX_ROOT (default workspace/pilot/textindex) / $TEXTINDEX_WORK (default qsjx) / index.sqlite.
Inputs are the recording OCR lines from the audit, as text; see data/*.tsv.
"""
import os
import unittest
from pathlib import Path

from pilot.textindex.cli import DEFAULT_ROOT, read_rows
from pilot.textindex.search import Index
from pilot.textindex.sequence import LINKED, NO_MATCH, match_sequence

DATA = Path(__file__).with_name("data")
DB = Path(os.environ.get("TEXTINDEX_ROOT", DEFAULT_ROOT)) / os.environ.get("TEXTINDEX_WORK", "qsjx") / "index.sqlite"
TOL = 2.0

# Positive 1: recording second -> expected EP11 second.
POSITIVE1 = {1398: 265, 1403: 275, 1404: 277, 1405: 279, 1408: 286, 1409: 465, 1410: 467, 1416: 478,
             1418: 482, 1420: 488, 1422: 533, 1425: 539, 1427: 543, 1428: 545, 1439: 1409}
# Positive 2: (episode, recording range, source range); only the anchor lines have an exact expected second.
POSITIVE2_SEGMENTS = [(3, 2322, 2353, 2169, 2273), (4, 2374, 2402, 923, 970)]
POSITIVE2_ANCHORS = {2322: 2169, 2374: 923, 2402: 970}


def expected(t: float):
    """(episode, exact second or None, allowed source range) for a positive line."""
    if t in POSITIVE1:
        return 11, POSITIVE1[t], (POSITIVE1[t] - TOL, POSITIVE1[t] + TOL)
    for ep, q0, q1, s0, s1 in POSITIVE2_SEGMENTS:
        if q0 <= t <= q1:
            return ep, POSITIVE2_ANCHORS.get(t), (s0 - TOL, s1 + TOL)
    raise KeyError(t)


def correct(link: dict) -> bool:
    ep, exact, (lo, hi) = expected(link["t_query"])
    if link["status"] != LINKED or link["episode"] != ep or not lo <= link["t_source"] <= hi:
        return False
    return exact is None or abs(link["t_source"] - exact) <= TOL


@unittest.skipUnless(DB.exists(), f"index not built: {DB}")
class RegressionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = Index.load(DB)
        cls.pos1 = read_rows(str(DATA / "positive1.tsv"))
        cls.pos2 = read_rows(str(DATA / "positive2.tsv"))
        cls.neg = read_rows(str(DATA / "negatives.tsv"))

    def test_positive_1_and_2(self):
        links = match_sequence(self.index, self.pos1) + match_sequence(self.index, self.pos2)
        ok = [correct(r) for r in links]
        self.assertGreaterEqual(sum(ok) / len(ok), 0.9, [r["text"] for r, good in zip(links, ok) if not good])
        wrong = [r["text"] for r, good in zip(links, ok) if r["status"] == LINKED and not good]
        self.assertEqual(wrong, [], "LINKED to a wrong place")
        # source time never runs backwards inside a segment
        for ep, q0, q1, _, _ in POSITIVE2_SEGMENTS:
            ts = [r["t_source"] for r in links if q0 <= r["t_query"] <= q1 and r["status"] == LINKED]
            self.assertEqual(ts, sorted(ts))

    def test_positive_3_single_line(self):
        text = "我这才过上几天潇洒日子呀"
        top = self.index.search(text)[0]
        self.assertEqual(top.entry.episode, 3)
        self.assertLessEqual(abs(top.entry.t_start - 618), TOL)
        (link,) = match_sequence(self.index, [(0, text)])
        self.assertEqual((link["status"], link["episode"]), (LINKED, 3))
        self.assertLessEqual(abs(link["t_source"] - 618), TOL)

    def test_negatives_are_no_match(self):
        self.assertEqual([r["status"] for r in match_sequence(self.index, self.neg)], [NO_MATCH] * len(self.neg))
        for row in self.neg:
            self.assertEqual(match_sequence(self.index, [row])[0]["status"], NO_MATCH, row[1])

    def test_negatives_inside_a_linked_chain(self):
        rows = []
        for n, row in enumerate(self.pos1):
            rows.append(row)
            if n in (2, 7, 12):
                rows.append((row[0] + 0.5, self.neg[(2, 7, 12).index(n)][1]))
        links = match_sequence(self.index, rows)
        negs = {text for _, text in self.neg}
        self.assertEqual([r["status"] for r in links if r["text"] in negs], [NO_MATCH] * 3)
        self.assertTrue(all(correct(r) for r in links if r["text"] not in negs))


if __name__ == "__main__":
    unittest.main()
