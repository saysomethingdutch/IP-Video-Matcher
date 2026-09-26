"""WO-008 association rules without reading held-out workbook outputs."""
from collections import Counter
import unittest

from .parse import part_identifier
from .pipeline import match_sessions


class CrossVideoTest(unittest.TestCase):
    def test_part_identifiers(self):
        self.assertEqual(part_identifier("七时16-4"), (16, 4))
        self.assertEqual(part_identifier("标题 P 3"), (None, 3))
        self.assertEqual(part_identifier("标题（2）"), (None, 2))
        self.assertEqual(part_identifier("标题 1/4"), (None, 1))

    def test_claimed_recording_is_not_a_filter_and_parts_are_distinct(self):
        rows = [{"row_id": str(i), "video": "wrong", "bv": "BV1x4SHYBEmD",
                 "title": f"七时16-{part}", "duration": 300, "uploader": ""}
                for i, part in ((1, 1), (2, 2))]
        sessions = [{"id": f"actual-{part}", "video": "actual", "total": 300,
                     "bvs": Counter(), "titles": Counter(),
                     "player_parts": Counter({(16, part): 4})}
                    for part in (1, 2)]
        linked, unmatched = match_sessions(sessions, rows, cross_video=True)
        self.assertFalse(unmatched)
        self.assertEqual(linked["1"][0]["id"], "actual-1")
        self.assertEqual(linked["2"][0]["id"], "actual-2")


if __name__ == "__main__":
    unittest.main()
