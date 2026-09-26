import unittest

from pilot.textindex.search import Index, SubEntry, adjacent_pairs
from pilot.textindex.sequence import AMBIGUOUS, LINKED, NO_MATCH, match_sequence, segments
from pilot.textindex.text import normalize

A, B, X, C = "我们今天就出发去京城", "路上一定要小心啊", "你别难过啊", "等我回来娶你"
D, E, F, G = "这门亲事我不同意", "除非我死了", "天亮了我们该走了", "再也不回来了"
NARRATION = "眼看着凡身一死就能回天界了"

# X ("你别难过啊") appears three times: EP1 106 s, EP1 900 s and EP2 500 s.
SOURCE = [(1, 100.0, A, ()), (1, 103.0, B, ()), (1, 106.0, X, ()), (1, 109.0, C, ()),
          (1, 112.0, "我知道", ("OPENING_ENDING", "SHORT")), (1, 116.5, "其实我知道", ()),
          (1, 895.0, F, ()), (1, 900.0, X, ()), (1, 903.0, G, ()), (2, 500.0, X, ()), (2, 510.0, D, ()),
          (2, 514.0, E, ())]


def make_index() -> Index:
    return Index([SubEntry(n, ep, t, t + 2.0, s, normalize(s), 1.0, flags)
                  for n, (ep, t, s, flags) in enumerate(SOURCE)])


class SequenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = make_index()

    def links(self, rows):
        return [(r["status"], r["episode"], r["t_source"]) for r in match_sequence(self.index, rows)]

    def test_chain_picks_the_occurrence_that_fits_the_neighbours(self):
        self.assertEqual(self.links([(0, A), (2, B), (4, X), (6, C)]),
                         [(LINKED, 1, 100.0), (LINKED, 1, 103.0), (LINKED, 1, 106.0), (LINKED, 1, 109.0)])
        # same text, but the neighbours sit around 900 s -> the later occurrence
        self.assertEqual(self.links([(0, F), (3, X), (6, G)]),
                         [(LINKED, 1, 895.0), (LINKED, 1, 900.0), (LINKED, 1, 903.0)])

    def test_chain_may_jump_to_another_episode(self):
        rows = [(0, A), (2, B), (10, D), (13, E)]
        self.assertEqual(self.links(rows), [(LINKED, 1, 100.0), (LINKED, 1, 103.0), (LINKED, 2, 510.0), (LINKED, 2, 514.0)])
        segs = segments(match_sequence(self.index, rows))
        self.assertEqual([(s["episode"], s["src_start"], s["src_end"], s["lines"]) for s in segs],
                         [(1, 100.0, 103.0, 2), (2, 510.0, 514.0, 2)])

    def test_repeated_line_between_two_segments_stays_ambiguous(self):
        # X fits right after B (EP1 106 s) and right before D (EP2 500 s) equally well
        r = match_sequence(self.index, [(0, A), (2, B), (5, X), (12, D), (15, E)])[2]
        self.assertEqual(r["status"], AMBIGUOUS)
        self.assertEqual({(c["episode"], c["t_start"]) for c in r["candidates"][:2]}, {(1, 106.0), (2, 500.0)})

    def test_isolated_repeated_line_is_ambiguous_and_lists_every_place(self):
        (r,) = match_sequence(self.index, [(0, X)])
        self.assertEqual(r["status"], AMBIGUOUS)
        self.assertEqual(sorted((c["episode"], c["t_start"]) for c in r["candidates"]),
                         [(1, 106.0), (1, 900.0), (2, 500.0)])

    def test_unique_line_links_without_context_but_with_lower_confidence(self):
        (solo,) = match_sequence(self.index, [(0, C)])
        chained = match_sequence(self.index, [(0, A), (2, B), (4, X), (6, C)])[-1]
        self.assertEqual((solo["status"], solo["episode"], solo["t_source"]), (LINKED, 1, 109.0))
        self.assertEqual(solo["support"], 0)
        self.assertLess(solo["conf"], chained["conf"])

    def test_narration_is_no_match_even_inside_a_chain(self):
        statuses = [r["status"] for r in match_sequence(self.index, [(0, A), (1, NARRATION), (2, B), (4, C)])]
        self.assertEqual(statuses, [LINKED, NO_MATCH, LINKED, LINKED])

    def test_generic_line_links_to_its_exact_match_not_a_nearby_partial_one(self):
        # "我知道" is flagged (generic, short); "其实我知道" 4.5 s later is not, and both fit the chain
        self.assertEqual(self.links([(0, A), (2, B), (4, X), (6, C), (10, "我知道")])[-1], (LINKED, 1, 112.0))

    def test_ocr_noise_still_links(self):
        rows = [(0, "我们今天就出发去京"), (2, "路上一定要小口啊"), (4, X), (6, "等我回来娶")]
        self.assertEqual([s for s, _, _ in self.links(rows)], [LINKED] * 4)

    def test_empty_input(self):
        self.assertEqual(match_sequence(self.index, []), [])


class PairTest(unittest.TestCase):
    def setUp(self):
        lines = [(16, 863.0, "该不是"), (16, 864.5, "要告自吧"), (16, 900.0, "第一计"), (17, 10.0, "别的集")]
        entries = [SubEntry(n + 1, ep, t, t + 1.0, s, normalize(s), 1.0, ()) for n, (ep, t, s) in enumerate(lines)]
        self.index = Index(entries + adjacent_pairs(entries))

    def test_caption_spanning_two_lines_matches_the_pair(self):
        best = self.index.search("该不是要告自吧")[0]
        self.assertEqual((best.entry.id, best.entry.parts, best.entry.t_start, best.sim), (-1, (1, 2), 863.0, 1.0))

    def test_pairs_only_join_neighbours_in_one_episode(self):
        pair_texts = [e.norm for e in self.index.entries if e.parts]
        self.assertEqual(pair_texts, ["该不是要告自吧"])     # 864.5 -> 900.0 too far apart; 17 is another episode

    def test_pair_is_hidden_when_one_of_its_lines_matches_better(self):
        self.assertEqual([c.entry.id for c in self.index.search("该不是")], [1])


if __name__ == "__main__":
    unittest.main()
