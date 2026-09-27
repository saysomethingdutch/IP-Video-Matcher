"""Formatting-only normalization for blind opinions."""
import unittest

from .robustness import normalize_blind_opinion


class BlindFormatTest(unittest.TestCase):
    def test_introductory_sentence_follows_shared_first_label(self):
        source = ("该视频为跳播视频，未播部分无法推定。\n"
                  "【使用的方式和目的】作者发表评论。\n"
                  "【对原作品的使用比例】只取少量。\n"
                  "综上，该视频构成合理使用。")
        result = normalize_blind_opinion(source)
        self.assertTrue(result.startswith("【使用的性质和目的】该视频为跳播视频，未播部分无法推定。\n作者发表评论。"))
        self.assertIn("【使用的比例】只取少量。", result)
        self.assertTrue(result.endswith("【结论】该视频构成合理使用。"))

    def test_preserves_reasoning_and_result(self):
        source = ("【使用的方式和目的】评论其剪辑。\n"
                  "【对原作品的使用比例】只取少量。\n"
                  "【对原作品的替代可能性】观众无法据此看完整剧。\n"
                  "综上，该视频构成合理使用。")
        result = normalize_blind_opinion(source)
        self.assertIn("评论其剪辑。", result)
        self.assertIn("【使用的性质和目的】", result)
        self.assertIn("【使用的比例】", result)
        self.assertTrue(result.endswith("【结论】该视频构成合理使用。"))


if __name__ == "__main__":
    unittest.main()
