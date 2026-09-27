"""WO-004b-R3 §1.2: no "否" where the claimed episodes are missing locally, unless the evidence is strong."""
import unittest

from pilot.content import evidence as ev

MISSING = {20, 21, 22, 26, 29, 34}


def line(ep, t, n):
    return {"status": ev.LINKED, "episode": ep, "t_source": t, "entry_id": f"{ep}-{n}", "source_text": f"台词{n}",
            "suspected": False, "flags": ()}


class ConservativeTest(unittest.TestCase):
    def test_narrower_becomes_partly_unverified(self):
        result, gap = ev.conservative(ev.NARROWER, [{"episode": 15}], [], set(range(15, 23)), MISSING)
        self.assertEqual((result, gap["missing"]), (ev.PARTLY_UNVERIFIED, [20, 21, 22]))

    def test_weak_contradiction_is_not_kept(self):
        links = [line(30, 2068.5, 1), line(30, 2077.0, 2)]                          # 1681: claim 29, EP30 two lines
        result, gap = ev.conservative(ev.CONTRADICTED, [{"episode": 30}], links, {29}, MISSING)
        self.assertEqual((result, gap["other_lines"]), (ev.MISSING_UNVERIFIED, 2))

    def test_short_stretch_or_recap_does_not_count(self):
        packed = [line(23, 469 + i * 5, i) for i in range(6)]                       # six lines within 25 s
        self.assertEqual(ev.conservative(ev.CONTRADICTED, [{"episode": 23}], packed, {26}, MISSING)[0], ev.MISSING_UNVERIFIED)
        recap = [line(27, 105 + i * 10, i) for i in range(6)]                       # all in the recap window
        self.assertEqual(ev.conservative(ev.CONTRADICTED, [{"episode": 27}], recap, {26}, MISSING)[0], ev.MISSING_UNVERIFIED)

    def test_strong_contradiction_is_kept(self):
        spread = [line(25, 700 + i * 120, i) for i in range(6)]                     # six lines over ten minutes
        result, gap = ev.conservative(ev.CONTRADICTED, [{"episode": 25}], spread, {34}, MISSING)
        self.assertEqual((result, gap.get("kept"), gap["reliable_lines"]), (ev.CONTRADICTED, True, 6))

    def test_claims_on_local_episodes_are_untouched(self):
        self.assertEqual(ev.conservative(ev.CONTRADICTED, [{"episode": 3}], [line(3, 500, 1)], {5}, MISSING),
                         (ev.CONTRADICTED, {}))
        self.assertEqual(ev.conservative(ev.NARROWER, [{"episode": 3}], [], {3, 4}, MISSING), (ev.NARROWER, {}))


if __name__ == "__main__":
    unittest.main()
