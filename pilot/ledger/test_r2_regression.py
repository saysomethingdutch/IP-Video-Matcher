"""Owner R2 examples and truncated readout rules."""
import glob
import json
from pathlib import Path
import unittest

from .parse import parse_items, parse_reading
from .pipeline import (build_sessions, match_sessions, observations, playback,
                       resolve_nearby_identity_readings, resolve_truncated_readings)
from .workbook import read_qsjx


class R2RegressionTest(unittest.TestCase):
    def test_truncated_total_requires_unique_minute_or_identity(self):
        self.assertEqual(parse_reading("04:56/18：", {1094})["position"], 296)
        self.assertIsNone(parse_reading("04:56/18", {1094, 1088}))
        self.assertEqual(parse_reading("04:56/18", {1094, 1088}, 1094)["total"], 1094)

    def test_ocr_fragments_and_speed_menu(self):
        items = [{"text": "04:56/18", "x": .10, "y": .64, "w": .10},
                 {"text": ":14", "x": .205, "y": .64, "w": .04}]
        parsed = parse_items(items, {1094, 1088})
        self.assertEqual(parsed["readings"][0]["total"], 1094)
        menu = parse_items([{"text": text, "x": .8, "y": .4 + i * .1} for i, text in
                            enumerate(("1.25X", "1.0X", "0.75X", "0.5X"))])
        self.assertTrue(menu["speed_menu"])
        self.assertIsNone(menu["speed"])
        selected = parse_items([{"text": "1.5×", "x": .48, "y": .47}])
        self.assertEqual(selected["speed"], 1.5)
        self.assertEqual(selected["speed_cue"], "central_confirmation")

    def test_owner_checked_row_1101(self):
        config = json.loads(Path("pilot/cases/qsjx_e18.yaml").read_text())
        workbook = Path(config["workbook"])
        cache = glob.glob("workspace/pilot/qsjx_e18/ocr_cache/MVI_0003-*.jsonl")
        if not workbook.exists() or not cache:
            self.skipTest("Owner workbook or retained OCR cache is unavailable")
        rows = [row for row in read_qsjx(workbook) if row["video"] == "MVI_0003"]
        records = [json.loads(line) for line in Path(cache[0]).read_text().splitlines()]
        obs = observations(records, {row["duration"] for row in rows})
        sessions = build_sessions(obs, 1, "MVI_0003")
        for _ in range(2):
            linked, _ = match_sessions(sessions, rows)
            resolve_truncated_readings(obs, rows, linked)
            sessions = build_sessions(obs, 1, "MVI_0003")
        linked, _ = match_sessions(sessions, rows)
        session = linked["1101"][0]
        result = playback(session["readings"], session["total"])
        self.assertLessEqual(abs(result["capture_seconds"] - 196), 10)
        self.assertEqual(result["speed"], 1.5)
        self.assertFalse(result["complete"])
        self.assertEqual(len(result["seeks"]), 3)
        for actual, expected in zip(result["seeks"], ((64, 296), (332, 576), (629, 876))):
            self.assertLessEqual(abs(actual["from"] - expected[0]), 3)
            self.assertLessEqual(abs(actual["to"] - expected[1]), 3)

    def test_same_bv_two_parts_keep_separate_sessions(self):
        config = json.loads(Path("pilot/cases/qsjx_e18.yaml").read_text())
        workbook = Path(config["workbook"])
        cache = glob.glob("workspace/pilot/qsjx_e18/ocr_cache/MVI_0001-*.jsonl")
        if not workbook.exists() or not cache:
            self.skipTest("Owner workbook or retained OCR cache is unavailable")
        rows = [row for row in read_qsjx(workbook) if row["video"] == "MVI_0001"]
        records = [json.loads(line) for line in Path(cache[0]).read_text().splitlines()]
        obs = observations(records, {row["duration"] for row in rows})
        resolve_nearby_identity_readings(obs, rows)
        sessions = build_sessions(obs, 1, "MVI_0001")
        for _ in range(2):
            linked, _ = match_sessions(sessions, rows)
            resolve_truncated_readings(obs, rows, linked)
            sessions = build_sessions(obs, 1, "MVI_0001")
        linked, _ = match_sessions(sessions, rows)
        self.assertNotEqual(linked["1057"][0]["id"], linked["1058"][0]["id"])
        self.assertEqual(linked["1058"][0]["total"], 1021)
        self.assertEqual(linked["1058"][0]["readings"][0]["total"], 1021)


if __name__ == "__main__":
    unittest.main()
