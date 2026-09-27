import unittest

from pilot.textindex.merge import frame_text, merge_frames, same_line
from pilot.textindex.text import edit_distance, grams, normalize, similarity


def item(text, conf=1.0, x=None, y=0.52, w=None, h=0.25):
    """OCR item [text, conf, x, y, w, h] centred horizontally unless x is given."""
    w = w if w is not None else 0.03 * len(text)
    x = x if x is not None else 0.5 - w / 2
    return [text, conf, x, y, w, h]


class NormalizeTest(unittest.TestCase):
    def test_keeps_han_digits_letters_only(self):
        self.assertEqual(normalize("况且 她还是你恩公"), "况且她还是你恩公")
        self.assertEqual(normalize("好. 恩公！"), "好恩公")
        self.assertEqual(normalize("《七时吉祥》第１２集，ＯＫ?"), "七时吉祥第12集ok")
        self.assertEqual(normalize("  ——…… "), "")

    def test_traditional_characters_become_simplified(self):
        self.assertEqual(normalize("你不應該來的"), "你不应该来的")
        self.assertEqual(normalize("至少伱讓我知道"), "至少你让我知道")      # 伱: an OCR variant form
        self.assertEqual(normalize("古靈精怪 活泼好動"), normalize("古灵精怪 活泼好动"))

    def test_edit_distance_and_similarity(self):
        self.assertEqual(edit_distance("宁王他不会放过你们的", "王他不会放过你们的"), 1)
        self.assertEqual(edit_distance("潇洒", "潇酒"), 1)
        self.assertEqual(similarity("现在要怎么办呀", "现在要怎么办呀"), 1.0)
        self.assertAlmostEqual(similarity("天气凉了", "天凉了"), 6 / 7)
        self.assertEqual(similarity("abc", "xyz"), 0.0)

    def test_grams(self):
        self.assertEqual(grams("天气凉了"), {"天气", "气凉", "凉了"})
        self.assertEqual(grams("好"), {"好"})
        self.assertEqual(grams(""), set())


class FrameTextTest(unittest.TestCase):
    def test_single_centred_line(self):
        self.assertEqual(frame_text([item("现在要怎么办呀")]), ("现在要怎么办呀", 1.0))

    def test_row_parts_joined_left_to_right(self):
        text, _ = frame_text([item("中", x=0.57, w=0.02), item("本王在宫", x=0.46, w=0.11)])
        self.assertEqual(text, "本王在宫 中")

    def test_drops_credits_ads_and_corner_text(self):
        items = [item("流", y=0.0, h=0.3),                    # opening credits at the top of the strip
                 item("广告", x=0.97, y=0.86, w=0.03, h=0.11),  # ad badge, tiny and in the corner
                 item("数年后", x=0.12, w=0.12),                 # scene caption on the left
                 item("翻得了围墙 打得过流氓", conf=0.5)]
        self.assertEqual(frame_text(items), ("翻得了围墙 打得过流氓", 0.5))
        self.assertIsNone(frame_text([item("...")]))
        self.assertIsNone(frame_text([]))


class MergeTest(unittest.TestCase):
    def frames(self, texts, t0=100.0):
        return [{"i": n, "t": t0 + n * 0.5, "items": [item(s)] if s else []} for n, s in enumerate(texts)]

    def test_same_line_rule(self):
        self.assertTrue(same_line("王他不会放过你们的", "宁王他不会放过你们的"))
        self.assertFalse(same_line("怎么", "什么"))           # 1 edit, but too short to be the same line
        self.assertFalse(same_line("我若是现在走了", "宁王他不会放过你们的"))

    def test_merges_adjacent_frames_and_picks_majority_spelling(self):
        texts = ["", "我这才过上几天潇洒日子呀", "我这才过上几天潇酒日子呀", "我这才过上几天潇洒日子呀", "",
                 "王他不会放过你们的", "宁王他不会放过你们的", "宁王他不会放过你们的", "怎么", "什么"]
        entries = merge_frames(11, self.frames(texts), fps=2.0)
        self.assertEqual([e.norm for e in entries], ["我这才过上几天潇洒日子呀", "宁王他不会放过你们的", "怎么", "什么"])
        first, second = entries[0], entries[1]
        self.assertEqual((first.t_start, first.t_end, first.n_frames, first.n_agree), (100.5, 102.0, 3, 2))
        self.assertEqual(first.variants, {"我这才过上几天潇洒日子呀": 2, "我这才过上几天潇酒日子呀": 1})
        self.assertEqual((second.t_start, second.t_end), (102.5, 104.0))

    def test_bridges_one_missed_frame_but_not_two(self):
        entries = merge_frames(1, self.frames(["天气凉了", "", "天气凉了"]), fps=2.0)
        self.assertEqual([(e.t_start, e.t_end) for e in entries], [(100.0, 101.5)])
        entries = merge_frames(1, self.frames(["天气凉了", "", "", "天气凉了"]), fps=2.0)
        self.assertEqual([(e.t_start, e.t_end) for e in entries], [(100.0, 100.5), (101.5, 102.0)])

    def test_tie_prefers_confidence_then_longer_spelling(self):
        frames = self.frames(["王他不会放过你们的", "宁王他不会放过你们的"])
        self.assertEqual(merge_frames(1, frames, fps=2.0)[0].text, "宁王他不会放过你们的")


if __name__ == "__main__":
    unittest.main()
