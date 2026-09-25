import unittest

from pilot.content import evidence as ev
from pilot.content.extract import Line, is_player_frame, is_ui, merge_events, row_lines, to_content
from pilot.textindex.search import Index, SubEntry
from pilot.textindex.text import normalize


def item(text, x, y, w=None, h=0.04, conf=1.0):
    return {"text": text, "x": x, "y": y, "w": w if w is not None else 0.02 * len(text), "h": h, "conf": conf}


def caption(text, y=0.70, conf=1.0):
    w = 0.02 * len(text)
    return item(text, 0.48 - w / 2, y, w, conf=conf)


class ClaimTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(ev.parse_claim("13"), {13})
        self.assertEqual(ev.parse_claim("12-14"), {12, 13, 14})
        self.assertEqual(ev.parse_claim("9-14、25-32"), set(range(9, 15)) | set(range(25, 33)))
        self.assertEqual(ev.parse_claim("25-25"), {25})
        self.assertEqual(len(ev.parse_claim("1-38")), 38)
        for bad in ("25-22", "", None, "第12集"):
            self.assertIsNone(ev.parse_claim(bad), bad)

    def test_verdicts(self):
        e11 = [{"episode": 11}]
        self.assertEqual(ev.verdict([], {11}), ev.UNVERIFIED)
        self.assertEqual(ev.verdict(e11, None), ev.CLAIM_UNPARSEABLE)
        self.assertEqual(ev.verdict(e11, {11}), ev.SUPPORTED)
        self.assertEqual(ev.verdict(e11, set(range(1, 39))), ev.NARROWER)
        self.assertEqual(ev.verdict(e11 + [{"episode": 15}], set(range(9, 15))), ev.CONTRADICTED)

    def test_statement_is_about_the_captured_part(self):
        s = ev.statement([{"episode": 11, "label": "EP11", "lines": 3}], "1-38", set(range(1, 39)), ev.NARROWER)
        self.assertTrue(s.startswith("已取证部分显示"))
        self.assertIn("EP11（3 句）", s)


def link(ep, t, text, entry_id, conf=0.97, sim=1.0):
    return {"status": ev.LINKED, "episode": ep, "t_source": t, "source_text": text, "text": text,
            "entry_id": entry_id, "conf": conf, "sim": sim, "candidates": []}


class EvidenceTest(unittest.TestCase):
    def test_threshold(self):
        two = [link(3, 100, "买点东西怎么了", 1), link(3, 104, "你别难过啊", 2)]
        self.assertEqual([e["episode"] for e in ev.evidenced_episodes(two)], [3])
        one_long = [link(5, 529, "待我取下赫赫战功", 3, conf=0.85)]
        self.assertEqual([e["episode"] for e in ev.evidenced_episodes(one_long)], [5])
        one_weak = [link(5, 529, "待我取下赫赫战功", 3, conf=0.6), link(7, 10, "第一计", 4)]
        self.assertEqual(ev.evidenced_episodes(one_weak), [])
        same_line_twice = [link(8, 50, "我会悔憾终身的", 5, conf=0.5), link(8, 50, "我会悔憾终身的", 5, conf=0.5)]
        self.assertEqual(ev.evidenced_episodes(same_line_twice), [])


def index_of(rows):
    return Index([SubEntry(n + 1, ep, t, t + 2, s, normalize(s), 1.0, ()) for n, (ep, t, s) in enumerate(rows)])


class RecapTest(unittest.TestCase):
    """D-011: a line in the recap window of episode N+1 is recorded as episode N."""

    def setUp(self):
        self.index = index_of([(3, 2273.0, "我是真的没有想到"), (4, 137.5, "我是真的没有想到"), (4, 900.0, "你要是出了什么事儿"),
                               (23, 120.0, "你可知我是为了救你呀"), (23, 800.0, "初空哥哥你别走")])
        self.eps = ev.Episodes(self.index)

    def test_recap_copy_goes_to_the_previous_episode(self):
        r = link(4, 137.5, "我是真的没有想到", 2)
        ev._assign_recap(r, *self.eps.recap_origin(4, 137.5, normalize(r["source_text"])))
        self.assertEqual((r["episode"], r["t_source"], r["suspected"]), (3, 2273.0, False))
        self.assertIsNone(self.eps.recap_origin(4, 900.0, "你要是出了什么事儿"))   # outside the window

    def test_missing_previous_episode_is_suspected(self):
        self.assertIn(22, self.eps.missing)
        links = [link(23, 120.0, "你可知我是为了救你呀", 4)]
        origin = self.eps.recap_origin(23, 120.0, normalize("你可知我是为了救你呀"))
        ev._assign_recap(links[0], *origin)
        (found,) = ev.evidenced_episodes(links)
        self.assertEqual((found["episode"], found["suspected"]), (22, True))
        self.assertEqual(found["label"], "疑似第22集（缺集，经第23集前情命中）")

    def test_caption_links_apply_the_rule(self):
        out = ev.caption_links(self.index, self.eps, [(0.0, "我是真的没有想到")])
        self.assertEqual((out[0]["status"], out[0]["episode"]), (ev.LINKED, 3))

    def test_adjacent_pair_matches_a_merged_caption(self):
        idx = ev.with_adjacent_pairs(index_of([(16, 863.0, "该不是"), (16, 864.5, "要告自吧"), (16, 900.0, "别的")]))
        best = idx.search("该不是要告自吧")[0]
        self.assertEqual((best.entry.episode, best.entry.t_start, best.sim), (16, 863.0, 1.0))


class ExtractTest(unittest.TestCase):
    def test_player_frame_layout(self):
        portrait = [item(f"推荐视频标题{n}", 0.33, 0.05 * n, w=0.2) for n in range(10)]
        self.assertFalse(is_player_frame(portrait))
        self.assertFalse(is_player_frame([item("简介", 0.34, 0.33), caption("现在要怎么办呀")]))
        self.assertTrue(is_player_frame([item("松ken大法好", 0.65, 0.15), caption("现在要怎么办呀")]))
        ui = [item("02:18/03:00", 0.09, 0.64), item("1.5X", 0.79, 0.72), item("发个友善的弹幕见证当下", 0.24, 0.74)]
        self.assertTrue(is_player_frame(ui + [caption("现在要怎么办呀")]))

    def test_ui(self):
        title = normalize("师父怎么不教我双修笑吐了")
        for t in ("02:18/03:00", "1.5X", "下午2:59", "发个友善的弹", "J 命运"):
            self.assertTrue(is_ui(item(t, 0.4, 0.5), title), t)
        self.assertTrue(is_ui(item("目动", 0.85, 0.74), title))                # garbled 自动 in the corner
        self.assertTrue(is_ui(item("师父怎么不教我双修", 0.1, 0.18), title))    # title bar at the top
        self.assertFalse(is_ui(caption("师父怎么不教我双修"), title))           # the same words as a caption
        self.assertFalse(is_ui(caption("大家记得给我们点赞关注评论"), title))

    def test_row_lines_drop_danmaku_watermark_and_merge_events(self):
        frames = {}
        for t in range(100, 112):
            frames[t] = [item("松ken大法好 bilibili", 0.65, 0.15, w=0.13),          # watermark
                         item("哈哈哈哈哈哈", 0.9 - 0.05 * (t - 100), 0.25, w=0.1)]  # scrolling danmaku
        for t in (101, 102, 103):
            frames[t].append(caption("现在要怎么办呀"))
        for t in (104, 105):
            frames[t].append(caption("我若是现在走了"))
            frames[t].append(caption("然后这样子互相奔赴", y=0.66))                  # uploader's caption above
        row = {"video_start": 100, "video_end": 111, "plaintiff": {"title": "", "uploader": "松ken大法好"}}
        rf = row_lines(frames, row)
        self.assertEqual(rf.dropped["弹幕"], 12)
        self.assertEqual(rf.dropped["水印"], 12)
        events = merge_events(rf.lines)
        self.assertEqual(sorted((e.best.text, e.rec_start, e.rec_end) for e in events),
                         [("我若是现在走了", 104.0, 105.0), ("然后这样子互相奔赴", 104.0, 105.0), ("现在要怎么办呀", 101.0, 103.0)])

    def test_playback_map(self):
        pmap = [{"video_start": 1397.0, "video_end": 1429.0, "content_start": 24, "content_end": 72, "event": "play"},
                {"video_start": 1430.0, "video_end": 1431.0, "content_start": 73, "content_end": 138, "event": "seek"},
                {"video_start": 1431.0, "video_end": 1460.0, "content_start": 138, "content_end": 180, "event": "play"}]
        self.assertEqual(to_content(1413.0, pmap, 1.5), (48.0, False))
        self.assertEqual(to_content(1431.0, pmap, 1.5), (138.0, False))    # after the seek, not inside it
        self.assertEqual(to_content(1390.0, pmap, 1.5), (13.5, True))      # before the first reading: estimated
        self.assertEqual(to_content(1400.0, [], None), (None, True))

    def test_line_geometry(self):
        line = Line(1.0, "现在要怎么办呀", "现在要怎么办呀", 1.0, (0.4, 0.7, 0.14, 0.04))
        self.assertAlmostEqual(line.cx, 0.47)
        self.assertAlmostEqual(line.cy, 0.72)


if __name__ == "__main__":
    unittest.main()
