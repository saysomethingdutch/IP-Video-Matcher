"""WO-102 §2: local screening, collages and cross-check, segments and AC lines — synthetic data only (no
recording, no OCR binary, no cloud call)."""
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from pilot.timeline import cloud as C
from pilot.timeline import frames as F
from pilot.timeline import local as Lc
from pilot.timeline import pipeline as P
from pilot.timeline import segments as S

RULES = Lc.load_rules()


def item(text, x=0.1, y=0.1, w=0.2, h=0.05, conf=1.0):
    return {"text": text, "x": x, "y": y, "w": w, "h": h, "conf": conf}


def qr_image(payload: str) -> np.ndarray:
    code = cv2.QRCodeEncoder.create().encode(payload)
    code = cv2.resize(code, None, fx=8, fy=8, interpolation=cv2.INTER_NEAREST)
    canvas = np.full((632, 1122), 60, np.uint8)
    y, x = (632 - code.shape[0]) // 2, (1122 - code.shape[1]) // 2
    canvas[y:y + code.shape[0], x:x + code.shape[1]] = code
    return cv2.cvtColor(canvas, cv2.COLOR_GRAY2BGR)


class LocalScreenTest(unittest.TestCase):
    def setUp(self):
        self.s = Lc.Screen(RULES)

    def test_logo_as_ocr_misreads_it_counts_only_in_the_lower_right(self):
        for text in ("吞噬呈空", "吞獵呈空", "容噬宝空", "吞噬导空"):
            self.assertTrue(self.s.logo_like(text), text)
        for text in ("星空", "天空", "夜空下", "吞噬星空236"):
            self.assertFalse(self.s.logo_like(text), text)
        self.assertTrue(self.s.logo_hit([item("吞噬呈空", x=0.8, y=0.85, w=0.1, h=0.04)]))
        self.assertFalse(self.s.logo_hit([item("吞噬呈空", x=0.05, y=0.05, w=0.1, h=0.04)]))   # an uploader's title card

    def test_film_watermark_is_the_theatrical_form(self):
        self.assertEqual(self.s.film_hit([item("决战原始星", x=0.8, y=0.86, w=0.12, h=0.04)]), "决战原始星")

    def test_traffic_words_and_platforms(self):
        other = self.s.traffic([item("百度网盘 提取码 8x2k")], [])
        self.assertEqual(other["platforms"], ["other"])
        self.assertEqual(self.s.traffic([item("进QQ群：123456789")], [])["platforms"], ["tencent"])
        self.assertEqual(self.s.traffic([item("关注公众号领取全集")], [])["platforms"], ["tencent"])
        self.assertIsNone(self.s.traffic([item("扫码")], []))                       # a weak word alone
        self.assertIsNone(self.s.traffic([item("www.bilibili.com")], []))           # the page itself

    def test_qr_payloads_decide_the_platform_and_skip_the_site_login(self):
        self.assertEqual(self.s.traffic([], ["https://pan.quark.cn/s/abc"])["kinds"], ["网盘二维码"])
        self.assertEqual(self.s.traffic([], ["https://weixin.qq.com/g/abc"])["platforms"], ["tencent"])
        self.assertIsNone(self.s.traffic([], ["https://passport.bilibili.com/qrcode"]))
        self.assertEqual(self.s.traffic([], [""])["evidence"], ["二维码：未能解码"])

    def test_opencv_reads_a_code_laid_over_the_picture(self):
        self.assertEqual(Lc.qr_codes(qr_image("https://pan.quark.cn/s/cc8e")), ["https://pan.quark.cn/s/cc8e"])
        self.assertEqual(Lc.qr_codes(np.full((360, 640, 3), 90, np.uint8)), [])

    def test_player_reading_and_blank(self):
        self.assertEqual(self.s.reading([item("01:23 / 05:40", y=0.9)]), (83, 340))
        self.assertIsNone(self.s.reading([item("01:23 / 05:40", y=0.3)]))        # not the control bar
        self.assertTrue(self.s.blank(np.zeros((90, 160, 3), np.uint8)))

    def test_screen_settles_logo_and_traffic_frames(self):
        f = {"t": 81.0}
        loc = self.s.screen(f, [item("吞噬呈空", x=0.8, y=0.85, w=0.1, h=0.04)], qr_image("https://pan.quark.cn/s/x"))
        self.assertEqual((loc["content"], loc["traffic"]["platforms"]), ("tsxk", ["other"]))


class FramesTest(unittest.TestCase):
    def test_dedupe_marks_repeats_of_the_first_frame_of_a_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            frames = []
            for t, v in ((0, 10), (10, 11), (20, 200), (30, 201)):
                p = Path(tmp) / f"{t}.jpg"
                cv2.imwrite(str(p), np.full((72, 128, 3), v, np.uint8))
                frames.append({"t": t, "path": str(p)})
            self.assertEqual(F.dedupe(frames), 2)
            self.assertEqual([f.get("dup_of") for f in frames], [None, 0, None, 20])

    def test_default_box_scales_with_the_recording(self):
        self.assertEqual(F.default_box(1920, 1080), F.DEFAULT_BOX)
        self.assertEqual(F.default_box(960, 540), (84, 129, 561, 316))


class CloudTest(unittest.TestCase):
    def answer(self, content, form="", traffic="none"):
        return {"content": content, "form": form, "traffic": traffic, "face": False, "text": True, "why": ""}

    def test_parse_needs_every_tile(self):
        reply = {"frames": [{"i": 1, "content": "tsxk"}, {"i": 2, "content": "tsxk_other", "form": "手游《黎明》"}]}
        got = C.parse(reply, 2)
        self.assertEqual((got[0]["content"], got[1]["form"]), ("tsxk", "手游"))
        self.assertIsNone(C.parse(reply, 3))
        self.assertEqual(C.parse({"frames": [{"i": 1, "content": "cat"}]}, 1)[0]["content"], "unknown")

    def test_cross_adopts_only_what_both_say(self):
        same = C.cross({"zhipu": self.answer("unrelated"), "deepseek": self.answer("unrelated")})
        self.assertEqual((same["content"], same["flags"]), ("unrelated", []))
        split = C.cross({"zhipu": self.answer("tsxk"), "deepseek": self.answer("unrelated")})
        self.assertEqual(split["content"], "unknown")
        self.assertIn("两家不一致", split["flags"][0])
        form = C.cross({"zhipu": self.answer("tsxk_other", "漫画"), "deepseek": self.answer("tsxk_other", "手游")})
        self.assertEqual((form["content"], form["form"]), ("tsxk_other", ""))
        one = C.cross({"zhipu": self.answer("tsxk", traffic="other"), "deepseek": self.answer("tsxk")})
        self.assertEqual(one["traffic"], [])
        self.assertIn("未采用", one["flags"][0])
        self.assertEqual(C.cross({"zhipu": None, "deepseek": self.answer("tsxk")})["content"], "unknown")

    def test_collage_and_board_are_pictures_with_marks(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for i in range(3):
                p = Path(tmp) / f"{i}.jpg"
                cv2.imwrite(str(p), np.full((632, 1122, 3), 50 * i, np.uint8))
                paths.append(str(p))
            out = C.collage(paths, [0, 10, 3725], Path(tmp) / "c.jpg")
            self.assertEqual(cv2.imread(str(out)).shape, (720, 1280, 3))
            gdir = Path(tmp) / "g"
            gdir.mkdir()
            for ref in RULES["work"]["references"]:
                cv2.imencode(".png", np.full((300, 500, 3), 120, np.uint8))[1].tofile(str(gdir / ref["file"]))
            board, legend = C.reference_board(RULES, gdir, [Path(paths[0])], Path(tmp) / "b.jpg")
            self.assertTrue(board.exists())
            self.assertIn("R1：动画剧场版《血洛大陆》", legend)
            self.assertIn("S1：", legend)
        self.assertEqual((C.mmss(95), C.mmss(3725)), ("01:35", "1:02:05"))

    def test_prompt_is_target_blind(self):
        for word in ("被告", "原告", "辩护", "侵权", "合理使用", "吞噬星空236"):
            self.assertNotIn(word, C.PROMPT)


class SegmentsTest(unittest.TestCase):
    def frame(self, t, content, traffic=(), form=""):
        return {"t": t, "content": content, "form": form, "traffic": list(traffic), "kinds": ["网盘二维码"] if traffic else [],
                "traffic_evidence": ["二维码：pan.quark.cn"] if traffic else [], "source": "本地"}

    def test_clock_uses_readings_else_the_start_and_the_recording_clock_is_identity(self):
        c = S.Clock(40.0, readings=[(62.0, (21, 650)), (72.0, (31, 650))])
        self.assertEqual((c.offset, c.duration, c(102.0)), (41.0, 650, 61.0))
        self.assertEqual(S.Clock(40.0)(50.0), 10.0)
        self.assertEqual(S.Clock.recording()(81.0), 81.0)
        self.assertIn("不连贯", S.Clock(0, readings=[(10, (5, 99)), (50, (5, 99))]).flags[0])

    def test_a_code_over_the_series_takes_its_neighbours_content(self):
        frames = [self.frame(0, "tsxk"), self.frame(10, None, ["other"]), self.frame(20, "tsxk"),
                  self.frame(30, "unrelated"), self.frame(40, None, ["other"])]
        S.fill_from_neighbours(frames)
        self.assertEqual([f["content"] for f in frames], ["tsxk", "tsxk", "tsxk", "unrelated", "traffic"])

    def test_segments_traffic_and_ac_lines_on_the_recording_clock(self):
        frames = [self.frame(t, "tsxk") for t in (40, 50, 60)] + [self.frame(70, "tsxk", ["other"])] + \
                 [self.frame(t, "unrelated") for t in (80, 90, 100)]
        clock = S.Clock.recording()
        segs = S.segments(frames, clock, 707.0)
        self.assertEqual([(s["start"], s["end"], s["content"]) for s in segs], [(40, 79, "tsxk"), (80, 707, "unrelated")])
        traffic = S.traffic_intervals(frames, clock)
        lines = S.ac_lines(segs, traffic)
        self.assertEqual(lines["content"], "00:40-01:19为《吞噬星空》动画画面；01:20-11:47为无关内容")
        self.assertEqual(lines["traffic"], ["01:10出现网盘二维码"])
        self.assertEqual(lines["unrelated"], ["01:20-11:47为无关内容"])
        s = S.summary(frames, segs)
        self.assertEqual((s["has_tsxk"], s["traffic_platforms"]), (True, ["other"]))

    def test_boundary_moves_to_the_first_second_that_looks_like_the_next_segment(self):
        frames = [self.frame(40, "tsxk"), self.frame(50, "unrelated")]
        probe = lambda a, b: (lambda t: 1.0 if t >= 46 else -1.0)
        segs = S.segments(frames, S.Clock.recording(), 60.0, probe)
        self.assertEqual([(s["start"], s["end"]) for s in segs], [(40, 45), (46, 60)])

    def test_scene_grouping_sends_the_first_frame_of_a_still_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            todo = []
            for t, v in ((0, 30), (10, 32), (20, 34), (30, 220)):
                p = Path(tmp) / f"{t}.jpg"
                cv2.imwrite(str(p), np.full((72, 128, 3), v, np.uint8))
                todo.append({"t": t, "path": str(p), "local": {"content": None, "traffic": None}})
            send, scene_of = P.scenes(todo, todo)
            self.assertEqual(([f["t"] for f in send], scene_of), ([0, 30], {10: 0, 20: 0}))

    def test_an_unknown_scene_head_gets_up_to_three_more_frames_and_a_majority(self):
        head = {"t": 0, "cloud": {"content": "unknown"}}
        group = [{"t": t} for t in (10, 20, 30, 40, 50)]
        probes, members = P.second_round([head], {f["t"]: 0 for f in group}, [head] + group)
        self.assertEqual([f["t"] for f in probes], [10, 30, 50])
        for f, c in zip(probes, ("unrelated", "unrelated", "unknown")):
            f["cloud"] = {"content": c, "form": ""}
        self.assertEqual(P.votes(members, probes)[0]["content"], "unrelated")
        probes[1]["cloud"]["content"] = "creator"
        self.assertEqual(P.votes(members, probes), {})



class R1Test(unittest.TestCase):
    """WO-102-R1: frames on a fixed grid and answers cached per frame, so a shorter span costs nothing again."""

    def test_grid_pick_is_a_subset_when_the_span_shrinks(self):
        keys = [t + 0.27 for t in range(0, 300, 5)]
        long = F.grid_pick(keys, (40.0, 290.0), 10)
        short = F.grid_pick(keys, (45.0, 200.0), 10)
        self.assertTrue(set(short) <= set(long))
        self.assertEqual(long[0], (40.0, 40.27))
        self.assertEqual(F.grid_pick([], (0.0, 20.0), 10), [(0.0, None), (10.0, None), (20.0, None)])   # exact seeks

    def test_answers_are_cached_per_frame_and_seeded_from_the_last_timeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            pic = Path(tmp) / "00081.27.jpg"
            cv2.imwrite(str(pic), np.full((72, 128, 3), 77, np.uint8))
            frame, cache = {"t": 81.27, "path": str(pic)}, Path(tmp) / "cache"
            answer = {"content": "tsxk", "form": "", "traffic": "none", "face": False, "text": False, "why": ""}
            self.assertIsNone(C.cached(frame, "d1", cache))
            C.store(frame, "d1", cache, "zhipu", answer, {})
            self.assertIsNone(C.cached(frame, "d1", cache))                   # both providers are needed
            C.store(frame, "d1", cache, "deepseek", answer, {})
            self.assertEqual(C.cached(frame, "d1", cache)["zhipu"]["content"], "tsxk")
            self.assertIsNone(C.cached(frame, "d2", cache))                   # another prompt or board
            prev = {"version": "wo102-2", "frames": [{"t": 81.27, "cloud": {"answers": {"zhipu": answer, "deepseek": answer}}}]}
            frame2, cache2 = {"t": 81.27, "path": str(pic)}, Path(tmp) / "cache2"
            self.assertEqual(P.seed_cache(prev, [frame2], "d3", cache2, C.BACKENDS), 1)
            self.assertIsNotNone(C.cached(frame2, "d3", cache2))

    def test_throttle_drops_to_four_after_a_rate_limit_across_processes(self):
        t = C.Throttle("zhipu")
        self.assertEqual(t.cap(), 8)
        t.limited()
        self.assertEqual(t.cap(), 4)
        with tempfile.TemporaryDirectory() as tmp:
            a, b = C.Throttle("zhipu", slot_dir=Path(tmp)), C.Throttle("zhipu", slot_dir=Path(tmp))
            self.assertEqual(a.cap(), 8)
            a.limited()
            self.assertEqual(b.cap(), 4)                     # another process sees the lower limit
            with a:
                self.assertTrue(any(Path(tmp).glob("zhipu.*.lock")))

    def test_captions_music_chip_and_lyrics_are_recorded(self):
        s, f = Lc.Screen(RULES), {"t": 1.0}
        s.screen(f, [item("就点名要布洛琳", y=0.85), item("J 海阔天空", y=0.9), item("词：黄家驹", y=0.4)],
                 np.full((90, 160, 3), 90, np.uint8))
        self.assertEqual((f["captions"], f["music_tag"], f["lyric"]), (["就点名要布洛琳"], True, True))


if __name__ == "__main__":
    unittest.main()
