"""WO-008 association rules without reading held-out workbook outputs."""
from collections import Counter
import unittest

from .parse import part_identifier
from .pipeline import build_sessions, match_sessions


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

    def test_neighboring_session_and_bulk_clipboard_do_not_supply_player_identity(self):
        def frame(t, total=None, bvs=(), part=None):
            return {"t": float(t), "frame": f"frame_{t}.jpg", "page": "竖屏详情页",
                    "texts": [], "titles": [], "bvs": list(bvs), "views": [], "parts": [], "dates": [],
                    "items": ([{"text": part, "y": .2}] if part else []),
                    "readings": ([{"t": float(t), "position": t, "total": total,
                                   "raw": f"00:{t:02d}/05:29", "frame": f"frame_{t}.jpg"}] if total else []),
                    "speed": None, "speed_source_frame": None}
        obs = [frame(0, 329), frame(1, 329, ("BV-player",), "七时22-1"),
               frame(2, bvs=("BV-copy-1", "BV-copy-2", "BV-copy-3", "BV-copy-4")),
               frame(6, bvs=("BV-next",), part="七时22-2"),
               frame(8, 312), frame(9, 312, ("BV-next",), "七时22-2")]
        first, second = build_sessions(obs, 1, "actual")
        self.assertEqual(set(first["bvs"]), {"BV-player"})
        self.assertEqual(first["player_parts"], Counter({(22, 1): 1}))
        self.assertEqual(set(second["bvs"]), {"BV-next"})


if __name__ == "__main__":
    unittest.main()
