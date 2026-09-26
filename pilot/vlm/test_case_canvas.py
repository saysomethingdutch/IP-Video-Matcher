"""New camera layouts and missing playback remain explicit in WO-007."""
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from pilot.vlm.canvas import player_box
from pilot.vlm import r3


class CaseCanvasTest(unittest.TestCase):
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
