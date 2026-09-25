import sqlite3
import unittest

from pilot.textindex import store
from pilot.textindex.merge import Entry


class LyricRangeTest(unittest.TestCase):
    def test_reviewed_ranges_flag_lyric(self):
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        con.executescript(store.SCHEMA)
        header = {"episode": 9, "fingerprint": "x", "duration": 2600.0, "n_frames": 10, "params": {}}
        entries = [Entry(9, t, t + 2.0, s, s, 1.0, 4, 4, {s: 4})
                   for t, s in [(268.0, "人聚人散"), (280.0, "红尘一梦去无还"), (326.0, "你不是初空")]]
        store.replace_episode(con, header, "qsjx_ep09.ts", (140.0, 2400.0, "rule"), entries, 2)
        store.recompute_flags(con, [{"episode": 9, "start": 250.0, "end": 290.0}])
        flags = {r["text"]: r["flags"] for r in con.execute("SELECT text, flags FROM entries")}
        self.assertIn("LYRIC", flags["红尘一梦去无还"])
        self.assertIn("LYRIC", flags["人聚人散"])
        self.assertNotIn("LYRIC", flags["你不是初空"])
        self.assertEqual(store.load_lyric_ranges(None), [])


if __name__ == "__main__":
    unittest.main()
