"""New camera layouts and missing playback remain explicit in WO-007."""
import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from PIL import Image, ImageDraw

from pilot.vlm.canvas import player_box
from pilot.vlm import r3


class CaseCanvasTest(unittest.TestCase):
    def test_one_second_playback_stays_inside_recorded_interval(self):
        row = {"playback_map": [{"event": "play", "video_start": 838, "video_end": 839}]}
        with mock.patch.object(r3, "candidates", side_effect=ValueError("播放区间中少于 4 个避开跳转和倍速菜单的关键帧")):
            times = r3.sixteen(row, {})
        self.assertEqual(len(times), len(set(times)))
        self.assertTrue(all(838 <= t < 839 for t in times))

    def test_prepared_canvas_version_three_is_reused(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "r3" / "1663"
            target.mkdir(parents=True)
            collage = target / "collage_1.jpg"
            collage.touch()
            meta = {"collage_path": str(collage), "page_mode": "landscape", "canvas_version": 3}
            (target / "meta.json").write_text(json.dumps(meta))
            with mock.patch.object(r3, "R3", Path(folder) / "r3"):
                self.assertEqual(r3.prepare_one({"row_id": "1663"}, Path(folder), Path(folder), {}, {}), meta)

    def test_case_box_follows_phone_orientation(self):
        preset = {"landscape": [250, 60, 1080, 500], "portrait": [465, 60, 755, 225],
                  "rotated_portrait": [195, 100, 470, 570]}
        upright = Image.new("RGB", (1280, 720), (85, 45, 28))
        ImageDraw.Draw(upright).rectangle((450, 40, 770, 675), fill=(30, 30, 30))
        self.assertEqual(player_box(upright, "MAH00734", 10, {}, preset)[:2],
                         ("portrait", tuple(preset["portrait"])))
        horizontal = Image.new("RGB", (1280, 720), (25, 25, 25))
        self.assertEqual(player_box(horizontal, "MAH00734", 10, {}, preset)[:2],
                         ("landscape", tuple(preset["landscape"])))
        ocr = {10: [{"x": .5, "y": .4, "text": "sideways"}] * 12}
        self.assertEqual(player_box(horizontal, "MAH00734", 10, ocr, preset)[:2],
                         ("rotated_portrait", tuple(preset["rotated_portrait"])))

    def test_unavailable_canvas_skips_both_cloud_calls(self):
        with tempfile.TemporaryDirectory() as folder:
            case = Path(folder) / "case"; case.mkdir()
            output = Path(folder) / "vlm"; target = output / "r3/469"; target.mkdir(parents=True)
            (case / "ledger.jsonl").write_text(json.dumps({"row_id": "469", "video": "MAH00734",
                "plaintiff": {"video_type": "混剪"}}) + "\n")
            meta = {"row_id": "469", "video": "MAH00734", "source": "unavailable",
                    "page_mode": "unavailable", "collage_path": "", "ui_status": "台账无已播放区间",
                    "geometry": None}
            (target / "meta.json").write_text(json.dumps(meta))
            with mock.patch.object(r3, "OUTPUT", output), mock.patch.object(r3, "R3", output / "r3"), \
                 mock.patch.object(r3, "CASE_ROWS", {"469"}), \
                 mock.patch.dict("os.environ", {"ZHIPU_API_KEY": "test", "DEEPSEEK_API_KEY": "test"}), \
                 mock.patch.object(r3, "cloud_one", side_effect=AssertionError("cloud must not run")):
                r3.cloud(case)
                r3.finalize(case)
            for name in ("glm-5.3-flash", "deepseek-flash", "consensus"):
                result = json.loads((target / f"{name}.json").read_text())
                self.assertEqual(result["status"], "skipped")

    def test_no_playback_without_usable_screenshot_is_marked_unavailable(self):
        with tempfile.TemporaryDirectory() as folder:
            old = r3.R3
            try:
                r3.R3 = Path(folder) / "r3"
                row = {"row_id": "469", "video": "MAH00734", "playback_map": [], "screenshot": None}
                meta = r3.prepare_one(row, Path(folder), Path(folder), {}, {})
                self.assertEqual(meta["source"], "unavailable")
                self.assertIn("无已播放区间", meta["ui_status"])
                self.assertTrue((r3.R3 / "469" / "meta.json").is_file())
            finally:
                r3.R3 = old


if __name__ == "__main__":
    unittest.main()
