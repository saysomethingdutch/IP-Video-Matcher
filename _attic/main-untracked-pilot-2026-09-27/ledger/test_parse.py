import unittest

from .parse import normalize_bv, parse_clock, parse_items, parse_reading


class OCRParsingTest(unittest.TestCase):
    def test_player_reading_from_mvi_0005(self):
        self.assertEqual(parse_reading("00:34/13:13"),
                         {"position": 34, "total": 793, "raw": "00:34/13:13"})
        self.assertEqual(parse_reading("01;13 7 02;18")["position"], 73)
        self.assertEqual(parse_reading("01;13 7 02;18")["total"], 138)

    def test_bv_ocr_correction_from_audit(self):
        self.assertEqual(normalize_bv("8V18N4y1975y"), "BV18N4y1975y")
        self.assertEqual(normalize_bv("BV1Ru411M7F7"), "BV1Ru411M7F7")
        self.assertIsNone(normalize_bv("BV1OOOOOOOOO"))

    def test_phone_clock(self):
        self.assertEqual(parse_clock("13:44"), 13 * 60 + 44)
        self.assertEqual(parse_clock("下午1:43"), 13 * 60 + 43)
        self.assertEqual(parse_clock("上午12:03"), 3)

    def test_speed_menu_is_ignored(self):
        items = [{"text": "1.5X", "y": .75}, {"text": "2.0X", "y": .55}]
        self.assertIsNone(parse_items(items)["speed"])

    def test_detail_page_view_count(self):
        items = [{"text": "口2.6万 日3 2023年8月13日 19:53", "y": .47}]
        parsed = parse_items(items)
        self.assertIn("2.6万", parsed["views"])
        self.assertTrue(parsed["dates"])


if __name__ == "__main__":
    unittest.main()
