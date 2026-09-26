"""Owner-checked MVI_0006 regressions against the retained OCR cache."""
import glob
import json
from pathlib import Path
import unittest

from .parse import parse_reading
from .pipeline import build_sessions, match_sessions, observations, playback
from .workbook import read_qsjx


EXPECTED = {
    "1143": ([(115, 185)], 147, 1.5, False),
    "1144": ([(52, 115), (150, 211)], None, None, None),
    "1146": ([(68, 154), (189, 275)], None, None, None),
    "1149": ([(54, 131), (148, 211)], None, None, None),
    "1152": ([(73, 138)], None, None, None),
    "1159": ([(38, 132), (188, 235)], None, None, None),
    "1165": ([(79, 130), (156, 205)], None, None, None),
}


class R1RegressionTest(unittest.TestCase):
    def test_closed_set_malformed_readings(self):
        for raw, position in [("01:53/03.37", 113), ("03:05/03 37", 185),
                              ("03:03703 37", 183), ("03:08703 ③7", 188)]:
            with self.subTest(raw=raw):
                self.assertEqual(parse_reading(raw, {217})["position"], position)
        self.assertIsNone(parse_reading("03:10/03-57", {217}))

    def test_owner_checked_mvi_0006(self):
        config = json.loads(Path("pilot/cases/qsjx_e18.yaml").read_text())
        workbook = Path(config["workbook"])
        cache = glob.glob("workspace/pilot/qsjx_e18/ocr_cache/MVI_0006-*.jsonl")
        if not workbook.exists() or not cache:
            self.skipTest("owner source workbook or retained OCR cache is unavailable")
        rows = [row for row in read_qsjx(workbook) if row["video"] == "MVI_0006"]
        records = [json.loads(line) for line in Path(cache[0]).read_text().splitlines()]
        obs = observations(records, {row["duration"] for row in rows})
        linked, _ = match_sessions(build_sessions(obs, 1, "MVI_0006"), rows)
        for row_id, (seeks, capture, speed, complete) in EXPECTED.items():
            with self.subTest(row_id=row_id):
                session = linked[row_id][0]
                result = playback(session["readings"], session["total"])
                for before, after in seeks:
                    self.assertTrue(any(abs(item["from"] - before) <= 3 and
                                        abs(item["to"] - after) <= 3 for item in result["seeks"]),
                                    result["seeks"])
                if capture is not None:
                    self.assertLessEqual(abs(result["capture_seconds"] - capture), 10)
                if speed is not None:
                    self.assertEqual(result["speed"], speed)
                if complete is not None:
                    self.assertEqual(result["complete"], complete)


if __name__ == "__main__":
    unittest.main()
