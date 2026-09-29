"""WO-102 §1, §3, §4: rules file, code-decided columns, the reply checks, AC assembly, the check list and the
scoring script — synthetic data only (no workbook, no model call)."""
import unittest
from pathlib import Path

from pilot.timeline import local as Lc
from pilot.tsxk import ac as A
from pilot.tsxk import check as CK
from pilot.tsxk import classify as K
from pilot.tsxk import fields as FD
from pilot.tsxk import samples as SM
from pilot.tsxk import sound as SD

RULES = Lc.load_rules()


def timeline(segs, traffic=(), forms=(), unknown=0):
    seconds = {}
    for s in segs:
        seconds[s["content"]] = seconds.get(s["content"], 0) + s["end"] - s["start"] + 1
    platforms = sorted({p for t in traffic for p in t["platforms"]})
    return {"segments": segs, "traffic": list(traffic), "flags": [], "frames": [],
            "ac": {"traffic": [f"{t['start']}出现网盘二维码" for t in traffic]},
            "summary": {"seconds": seconds, "forms": list(forms), "has_tsxk": any(s["content"] == "tsxk" for s in segs),
                        "has_film": "动画剧场版" in forms, "traffic_platforms": platforms, "unknown_frames": unknown,
                        "face_frames": 0, "text_frames": 0}}


def seg(start, end, content, form=""):
    return {"start": start, "end": end, "content": content, "form": form, "frames": 1, "sources": ["本地"]}


class RulesFileTest(unittest.TestCase):
    def test_every_column_has_a_source_and_who_decides(self):
        for col in ("D", "F", "L", "Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z", "AA", "AB", "AC"):
            spec = RULES["columns"][col]
            self.assertTrue(spec.get("source"), col)
            self.assertTrue(spec.get("by"), col)
        self.assertEqual(RULES["columns"]["AA"]["values"], ["无法判断"])          # D-056 ④

    def test_prompts_are_target_blind(self):
        text = K.SYSTEM + K.standards(RULES)
        for word in ("被告", "原告", "辩护", "本案以论证合理使用为目标"):
            self.assertNotIn(word, text)


class FieldsTest(unittest.TestCase):
    def test_witness_time_format(self):
        self.assertEqual(FD.witness_time("2026年8月12日10时46分6秒"), "2026/8/12 10:46:06")
        self.assertEqual(FD.witness_time("2026-04-01 09:05:00"), "2026/4/1 09:05:00")
        self.assertIsNone(FD.witness_time(""))

    def test_ledger_columns_wait_for_wo101(self):
        fields, flags = FD.ledger_fields({"C": "标题"}, None)
        self.assertIsNone(fields["D"]["value"])
        self.assertIn("WO-101", flags[0]["reason"])
        led = {"title": "吞噬星空108（五）", "address": None, "complete": True, "finished_witness_time": "2024-04-18 16:42:57"}
        fields, _ = FD.ledger_fields({"C": "studio_video_1708954227622.mp4", "E": "https://b"}, led)
        self.assertEqual((fields["D"]["value"], fields["F"]["value"], fields["L"]["value"], fields["R"]["value"]),
                         ("吞噬星空108（五）", "未显示", "2024/4/18 16:42:57", "是"))

    def test_areas_not_shown_mean_none_and_an_ac_note(self):
        fields, notes, _ = FD.page_fields(None, RULES)
        self.assertEqual((fields["S"]["value"], fields["AB"]["value"]), ("无", "无"))
        self.assertEqual(notes, ["录屏未显示标签区", "录屏未显示简介区"])
        led = {"tags": {"visible": True, "items": ["吞噬星空", "国漫"], "activity": "动画短片群星计划"},
               "intro": {"visible": True, "text": "罗峰的高光时刻"}, "collection": {"visible": True, "title": "吞噬星空"}}
        fields, notes, _ = FD.page_fields(led, RULES)
        self.assertEqual(fields["S"]["value"], "动画短片群星计划")
        self.assertEqual(fields["AB"]["value"], "标签（吞噬星空）、简介（罗峰）、合集（吞噬星空）")
        self.assertEqual(notes, [])

    def test_series_with_a_code_and_filler(self):
        tl = timeline([seg(40, 150, "tsxk"), seg(151, 707, "unrelated")],
                      [{"start": 81, "end": 91, "platforms": ["other"], "kinds": ["网盘二维码"]}])
        fields, hints, _ = FD.timeline_fields(tl, None, "吞噬星空236", RULES)
        self.assertEqual((fields["Q"]["value"], fields["W"]["value"], fields["X"]["value"]), ("否", "是", "是（其他）"))
        self.assertIsNone(hints["y_fixed"])
        lines = FD.time_lines(tl, fields, "偷渡")
        self.assertEqual(lines[0], "81出现网盘二维码")
        self.assertEqual(lines[1], "00:40-02:30为《吞噬星空》动画画面；02:31-11:47为无关内容")

    def test_no_animation_fixes_y_and_z(self):
        drive = timeline([seg(35, 60, "traffic"), seg(61, 649, "unrelated")],
                         [{"start": 35, "end": 60, "platforms": ["other"], "kinds": ["网盘二维码"]}])
        f, h, _ = FD.timeline_fields(drive, None, "吞噬星空236", RULES)
        self.assertEqual((f["Q"]["value"], f["Y"]["value"], f["Z"]["value"]), ("是", "引流", "低"))
        film = timeline([seg(0, 90, "tsxk_other", "动画剧场版")], forms=["动画剧场版"])
        f, h, flags = FD.timeline_fields(film, None, "决战原始星PV", RULES)            # D-068: judged like the series
        self.assertEqual((f["Q"]["value"], h["y_fixed"]), ("否", None))
        literal = {**RULES, "policies": {**RULES["policies"], "film_only": {"value": "错权"}}}
        f, h, flags = FD.timeline_fields(film, None, "决战原始星PV", literal)          # the other switch position
        self.assertEqual((f["Q"]["value"], f["Y"]["value"], f["Z"]["value"]), ("否", "错权", "低"))
        self.assertTrue(any("指引没有给错权" in x["reason"] for x in flags))
        manga = timeline([seg(0, 90, "tsxk_other", "漫画")], forms=["漫画"])
        self.assertEqual(FD.timeline_fields(manga, None, "吞噬星空漫画", RULES)[0]["Y"]["value"], "错权")
        pending = timeline([seg(0, 90, "unknown")], unknown=9)
        pending["summary"].update(disputed_frames=9, disputed_pending=6)
        f, h, flags = FD.timeline_fields(pending, None, "吞噬星空", RULES)             # WO-102-R4 §1
        self.assertEqual((f["Q"]["value"], f["Y"]["value"], h.get("dispute_pending")), (None, None, True))
        self.assertTrue(any(x["level"] == "red" for x in flags))
        other = timeline([seg(0, 300, "unrelated")])
        f, h, flags = FD.timeline_fields(other, None, "谆提身陨", RULES)
        self.assertEqual((f["Y"]["value"], f["Z"]["value"]), ("完全无关内容", "高"))              # 示例 11802

    def test_w_counts_non_official_pictures_by_default_and_third_party_on_the_other_switch(self):
        tl = timeline([seg(0, 33, "tsxk_other", "手游"), seg(34, 400, "tsxk")], forms=["手游"])
        f, h, flags = FD.timeline_fields(tl, None, "吞噬星空动漫吐槽", RULES)           # WO-102-R2 B2
        self.assertEqual((f["W"]["value"], h["forms"]), ("是", ["手游"]))
        self.assertIn("吞噬星空其他形式（手游）", f["W"]["basis"])
        self.assertEqual(FD.time_lines(tl, f, "二创（其他类）"), ["00:00-00:33为《吞噬星空》手游内容"])
        narrow = {**RULES, "policies": {**RULES["policies"], "w_scope": {"value": "third_party"}}}
        f, _, _ = FD.timeline_fields(tl, None, "吞噬星空动漫吐槽", narrow)               # D-058 ④
        self.assertEqual(f["W"]["value"], "否")
        creator = timeline([seg(0, 20, "creator"), seg(21, 300, "tsxk")])
        creator["summary"]["creator_frames"] = 2
        self.assertEqual(FD.timeline_fields(creator, None, "x", RULES)[0]["W"]["value"], "是")
        official = timeline([seg(0, 300, "tsxk")])
        self.assertEqual(FD.timeline_fields(official, None, "x", RULES)[0]["W"]["value"], "否")

    def test_t_needs_evidence(self):
        film = timeline([seg(0, 90, "tsxk"), seg(91, 120, "tsxk_other", "动画剧场版")], forms=["动画剧场版"])
        f, h, flags = FD.timeline_fields(film, None, "x", RULES)                          # WO-102-R2 A5
        self.assertEqual(h["forms"], [])
        self.assertTrue(any("没有看到片名水印" in x["reason"] for x in flags))
        film["summary"]["film_watermark"] = True
        self.assertEqual(FD.timeline_fields(film, None, "x", RULES)[1]["forms"], ["动画剧场版"])

    def test_r_needs_review_is_flagged(self):
        _, flags = FD.ledger_fields({"C": "t"}, {"title": "t", "complete": True, "complete_needs_review": True})
        self.assertTrue(any("需复核" in x["reason"] for x in flags))

    def test_episode_note(self):
        self.assertEqual(FD.episode_note("吞噬星空第52集看点"), "标题标注第52集")
        self.assertIsNone(FD.episode_note("吞噬星空236"))


class ReplyCheckTest(unittest.TestCase):
    def reply(self, **kw):
        base = {"Y": "二创（解说类）", "U": "是", "V": "否", "Z": "中", "T_extra": [],
                "AC": {"主题概括": "本视频对剧情进行讲解。", "段落情节": "罗峰获得宝物。", "新增创作要素": ["增加解说配音"],
                       "比对意见": "解说以剧情复述为主，合理使用可能性中等。", "其他": []}}
        base.update(kw)
        return base

    def test_a_good_reply_passes(self):
        self.assertEqual(K.check(self.reply(), None, {"W": {"value": "否"}}, None), [])

    def test_numbers_quotes_and_inconsistent_choices_fail(self):
        fixed = {"W": {"value": "否"}}
        ac = dict(self.reply()["AC"], 段落情节="第52集罗峰获得宝物。")
        self.assertIn("不要写数字", "".join(K.check(self.reply(AC=ac), None, fixed, None)))
        ac = dict(self.reply()["AC"], 主题概括="UP主说“太强了”“真燃”。")
        self.assertIn("引号", "".join(K.check(self.reply(AC=ac), None, fixed, None)))
        self.assertIn("W=否", "".join(K.check(self.reply(Y="偷渡"), None, fixed, None)))
        self.assertIn("纯cut", "".join(K.check(self.reply(Y="纯cut"), None, fixed, None)))
        self.assertIn("U：解说类", "".join(K.check(self.reply(U="否"), None, fixed, None)))
        ac = dict(self.reply()["AC"], 比对意见="一。二。三。四。")
        self.assertIn("最多 3 句", "".join(K.check(self.reply(AC=ac), None, fixed, None)))

    def test_a_fixed_y_is_not_rechecked(self):
        self.assertEqual(K.check(self.reply(Y="随便"), "引流", {"W": {"value": "是"}}, None), [])


class AcTest(unittest.TestCase):
    def test_sections_lists_and_code_lines(self):
        text = A.assemble({"主题概括": "概括。", "段落情节": "情节。", "新增创作要素": ["背景音乐", "转场特效"],
                           "比对意见": "意见。", "其他": ["画面加了透明度"]},
                          ["01:27-01:37出现网盘二维码", "录屏未显示标签区"])
        self.assertEqual(text, "【主题概括】\n概括。\n\n【段落情节】\n情节。\n\n【新增创作要素情况】\n1.背景音乐\n2.转场特效\n\n"
                               "【比对意见】\n意见。\n\n【其他】\n1.画面加了透明度\n2.01:27-01:37出现网盘二维码\n3.录屏未显示标签区")
        self.assertTrue(A.assemble({}, []).endswith("【其他】"))


class CheckListTest(unittest.TestCase):
    def rows(self):
        titles = ["【吞噬星空reaction】48", "吞噬星空等级解说", "吞噬星空踩点混剪", "吞噬星空236", "决战原始星PV", "罗峰日常"]
        return [{"序号": str(8213 + i), "标题": titles[i % 6] + str(i), "BV": f"BV{i:010d}",
                 "UP主": f"up{i % 9}", "发布时间": "", "取证完成时间": "", "取证视频链接": ""} for i in range(120)]

    def test_draw_is_stratified_capped_per_uploader_and_repeatable(self):
        picked = CK.draw(self.rows(), 30, 1)
        self.assertEqual(len(picked), 18)                  # 9 uploaders × 2
        self.assertTrue(all(n <= 2 for n in __import__("collections").Counter(r["UP主"] for r in picked).values()))
        self.assertEqual(picked, CK.draw(self.rows(), 30, 1))
        self.assertEqual(CK.stratum("【吞噬星空reaction】48"), "reaction")
        self.assertEqual(CK.stratum("吞噬星空236"), "集数标题")

    def test_score_counts_agreement_and_lists_guidance_columns(self):
        machine = {"1": {"Q": "否", "Y": "偷渡", "X": "是（其他）", "AA": "无法判断", "T": "是（手游）"},
                   "2": {"Q": "否", "Y": "二创（单IP混剪类）", "X": "否", "AA": "无法判断", "T": "否"}}
        human = {"1": {"Q": "否", "Y": "二创（混剪类）", "X": "否", "AA": "第236集", "T": "是（游戏）"},
                 "2": {"Q": "否", "Y": "二创（混剪类）", "X": "否", "AA": "无法判断", "T": "否"}}
        r = CK.score(machine, human)
        self.assertEqual(r["scored"]["Q"]["agree"], 2)
        self.assertEqual(r["scored"]["Y"]["agree"], 1)          # 混剪类 is read as 单IP混剪类
        self.assertEqual(r["scored"]["T"]["agree"], 2)          # 游戏 is read as 手游
        self.assertEqual(len(r["guidance_over_human"]["X"]["differ"]), 1)
        self.assertEqual(r["tou_du"], [{"row": "1", "human_Y": "二创（混剪类）"}])
        self.assertIn("计分列", CK.markdown(r))


class SamplesTest(unittest.TestCase):
    def test_scrub_takes_out_times_episodes_and_shares(self):
        text = "【其他】\n1.02:47-5:17无关内容\n2.第108集的内容\n3.80%以上为无关内容"
        self.assertEqual(SM.scrub(text), "【其他】\n1.相关集数的内容\n2.大部分为无关内容")



class SoundTest(unittest.TestCase):
    """WO-102-R1 §1.2, §1.5: what the prompt hears, and the evidence that music was added by the uploader."""

    def chunk(self, a, b, tags, text=""):
        return {"recording_start": a, "recording_end": b, "tags": tags, "text": text}

    def tl(self, frames, segs):
        return {"segments": segs, "frames": frames}

    def test_series_with_pure_music_is_no_evidence(self):
        audio = {"status": "ok", "audio_chunks": [self.chunk(40, 60, ["BGM"])], "uploader_speech": []}
        tl = self.tl([{"t": 50, "content": "tsxk", "captions": []}], [seg(40, 60, "tsxk")])
        m = SD.music_evidence(audio, tl)
        self.assertEqual((m["verdict"], m["series_music_seconds"]), ("无旁证", 20))
        self.assertIn("只有原片画面加纯音乐", SD.section(audio, m))
        self.assertNotIn("BGM", SD.section(audio, m))                       # never the raw share (D-047)

    def test_the_four_kinds_of_evidence(self):
        audio = {"status": "ok", "audio_chunks": [self.chunk(40, 60, ["BGM"]), self.chunk(60, 80, ["BGM"])]}
        off = self.tl([{"t": 70, "content": "unrelated"}], [seg(40, 59, "tsxk"), seg(60, 80, "unrelated")])
        self.assertIn("非吞噬星空画面上的配乐", SD.music_evidence(audio, off)["evidence"][0])
        silent = self.tl([{"t": 50, "content": "tsxk", "captions": ["就点名要布洛琳"]}], [seg(40, 80, "tsxk")])
        self.assertIn("听不到人声", SD.music_evidence(audio, silent)["evidence"][0])
        lyric = self.tl([{"t": 50, "content": "creator", "lyric": True}], [seg(40, 80, "tsxk")])
        self.assertIn("歌词字幕", "".join(SD.music_evidence(audio, lyric)["evidence"]))
        chip = self.tl([{"t": 50, "content": "tsxk", "music_tag": True}], [seg(40, 80, "tsxk")])
        self.assertEqual(SD.music_evidence(audio, chip)["evidence"], ["B站音乐标签"])

    def test_no_music_no_audio_and_the_speech_line(self):
        audio = {"status": "ok", "audio_chunks": [self.chunk(40, 44, ["Speech"], "大家好")], "uploader_speech": ["大家好"]}
        m = SD.music_evidence(audio, self.tl([], [seg(40, 44, "tsxk")]))
        self.assertEqual(m["verdict"], "否")
        self.assertEqual(SD.section(audio, m), "【声音】UP主口播（过滤后）1 句：大家好；疑似UP主配乐：否")
        self.assertIsNone(SD.music_evidence(None, None)["verdict"])
        self.assertIn("未提供", SD.section(None, SD.music_evidence(None, None)))

    def test_v_yes_needs_evidence_and_nothing_else_added(self):
        reply = {"Y": "二创（单IP混剪类）", "U": "否", "V": "是", "Z": "中", "T_extra": [],
                 "AC": {"主题概括": "混剪。", "段落情节": "打斗。", "新增创作要素": ["背景音乐"], "比对意见": "中等。", "其他": []}}
        tl = {"segments": [seg(0, 60, "tsxk")]}
        fixed = {"W": {"value": "否"}}
        self.assertIn("没有 UP 主配乐的旁证", "".join(K.check(reply, None, fixed, tl, {"verdict": "无旁证"})))
        self.assertEqual(K.check(reply, None, fixed, tl, {"verdict": "是"}), [])
        self.assertIn("口播", "".join(K.check(reply, None, fixed, tl, {"verdict": "是"}, speech=3)))

    def test_ledger_same_as_input_flags_and_certificate_time(self):
        led = {"title": "OCR 读错的标题", "title_same_as_input": True, "address": "https://x", "address_same_as_input": False,
               "complete": None, "finished_witness_time": None, "certificate_finished_time": "2026-04-01 09:05:01"}
        fields, flags = FD.ledger_fields({"C": "原表标题", "E": "https://e"}, led)
        self.assertEqual((fields["D"]["value"], fields["F"]["value"], fields["L"]["value"]),
                         ("原表标题", "https://x", "2026/4/1 09:05:01"))
        self.assertTrue(any(f["reason"].startswith("R：") for f in flags))



class Step2Test(unittest.TestCase):
    """WO-102-R1 step 2: WO-103-R1 facts, promotional speech as an X clue, the Owner's disagreement list."""

    def audio(self, **facts):
        base = {"music_only_spans": [], "lyric_subtitle_spans": [], "bilibili_music_tags": [], "subtitle_without_speech_spans": []}
        base.update(facts)
        return {"status": "ok", "segments": [{"text": "x"}], "uploader_speech": [], "music_facts": base}

    def test_music_facts_give_the_four_kinds_of_evidence(self):
        tl = {"segments": [seg(40, 99, "tsxk"), seg(100, 200, "unrelated")], "frames": []}
        self.assertEqual(SD.music_evidence(self.audio(music_only_spans=[[50, 90]]), tl)["verdict"], "无旁证")
        self.assertEqual(SD.music_evidence(self.audio(), tl)["verdict"], "否")
        self.assertIn("非吞噬星空画面上的配乐", SD.music_evidence(self.audio(music_only_spans=[[120, 180]]), tl)["evidence"][0])
        self.assertIn("听不到人声", SD.music_evidence(self.audio(subtitle_without_speech_spans=[[60, 70]]), tl)["evidence"][0])
        self.assertIn("歌词字幕", SD.music_evidence(self.audio(lyric_subtitle_spans=[[150, 160]]), tl)["evidence"][0])
        self.assertEqual(SD.music_evidence(self.audio(bilibili_music_tags=[{"t": 50}]), tl)["evidence"], ["B站音乐标签"])

    def test_promotions_merge_and_are_labelled(self):
        audio = {"status": "ok", "segments": [{"text": "x"}], "promotional_speech": [
            {"text": "本视频需要一个硬币才能观看", "recording_start": 52.76, "recording_end": 55.24},
            {"text": "喜欢的话欢迎关注投币支持一下", "recording_start": 56.0, "recording_end": 60.0},
            {"text": "想提前看更多后续的不要再到处找资源了", "recording_start": 300.0, "recording_end": 304.0}]}
        pr = SD.promotions(audio)
        self.assertEqual([(round(p["start"]), round(p["end"])) for p in pr], [(53, 60), (300, 304)])
        self.assertEqual(pr[0]["kinds"], ["要求投币或付费才能观看"])
        self.assertEqual(pr[1]["kinds"], ["引导观众去别处找后续资源"])
        line = SD.section({**audio, "uploader_speech": [], "redub_candidates": [{"text": "a"}]}, {"verdict": "否", "evidence": [],
                                                                                                "series_music_seconds": 0})
        self.assertIn("改配音候选 1 句", line)
        self.assertIn("推广口播 3 句", line)

    def test_uploader_speech_accepts_strings_or_objects(self):
        self.assertEqual(SD.uploader_speech({"status": "ok", "segments": [{}], "uploader_speech": ["甲", {"text": "乙"}]}),
                         ["甲", "乙"])

    def test_disagreements_carry_basis_frames_and_notes(self):
        from pathlib import Path as P
        tl = {"segments": [seg(40, 80, "tsxk"), seg(81, 120, "unrelated")],
              "frames": [{"t": 40.0, "content": "tsxk"}, {"t": 90.0, "content": "unrelated", "traffic": ["other"]}]}
        report = {"scored": {"Y": {"disagree": [{"row": "8454", "machine": "偷渡", "human": "纯cut"}]}},
                  "guidance_over_human": {"X": {"differ": [{"row": "8454", "machine": "是（其他）", "human": "否"}]},
                                          "R": {"differ": [{"row": "8454", "machine": "是", "human": "否"}]}}}
        rows = {"8454": {"fields": {"Y": {"basis": "GLM"}, "X": {"basis": "画面"}, "R": {"basis": "进度条"}}}}
        items = CK.disagreements(report, rows, {"8454": (P("/t/8454"), tl)}, {"8454": P("/a/8454.json")},
                                 {"8454": "声音不可靠"})
        by = {e["column"]: e for e in items}
        self.assertEqual(by["Y"]["frames"], ["/t/8454/frames/00040.00.jpg", "/t/8454/frames/00090.00.jpg"])
        self.assertEqual(by["X"]["frames"], ["/t/8454/frames/00090.00.jpg"])
        self.assertEqual((by["R"]["frames"], by["R"]["scored"]), ([], False))
        self.assertIn("WO-101-R2", by["R"]["note"])
        self.assertIn("声音不可靠", by["Y"]["note"])
        self.assertIn("裁定：", CK.disagreements_md(items))



class R2FixesTest(unittest.TestCase):
    """WO-102-R2 A4, A5 in the classification."""

    def test_novel_needs_words_inside_the_actual_playback(self):
        from pilot.tsxk.pipeline import text_evidence
        tl = {"frames": [{"ocr": ["这里和原著小说不一样"]}, {"ocr": ["有声书"], "ignored": "内容区间外"}]}
        self.assertEqual(text_evidence(tl, None), {"小说": True, "有声书": False})
        self.assertEqual(text_evidence({"frames": [{"ocr": ["罗峰出场"]}]}, None)["小说"], False)
        speech = {"status": "ok", "segments": [{}], "uploader_speech": ["小说里写得更细"]}
        self.assertTrue(text_evidence({"frames": []}, speech)["小说"])

    def test_ai_note_keeps_y_among_the_derivative_kinds(self):
        reply = {"Y": "纯cut", "U": "否", "V": "否", "Z": "低", "T_extra": [],
                 "AC": {"主题概括": "片段。", "段落情节": "罗峰。", "新增创作要素": [], "比对意见": "低。", "其他": []}}
        tl = {"segments": [seg(0, 60, "tsxk")], "summary": {"ai_note_frames": 2}}
        self.assertIn("AI 创作声明", "".join(K.check(reply, None, {"W": {"value": "是"}}, tl)))
        self.assertNotIn("AI 创作声明", "".join(K.check(dict(reply, Y="二创（单IP混剪类）"), None, {"W": {"value": "是"}}, tl)))

    def test_policies_are_in_the_prompt(self):
        text = K.standards(RULES)
        self.assertIn("口径（WO-102-R2 B", text)
        self.assertIn("归二创（单IP混剪类）", text)
        self.assertIn("水印", text)



class R3RulesTest(unittest.TestCase):
    """WO-102-R3 (D-065 ⑤-4): Z 高 only in four cases; the two check sets."""

    def reply(self, **kw):
        base = {"Y": "二创（解说类）", "U": "是", "V": "否", "Z": "高", "Z_basis": "", "T_extra": [],
                "AC": {"主题概括": "解说。", "段落情节": "罗峰。", "新增创作要素": ["配音"], "比对意见": "高。", "其他": []}}
        base.update(kw)
        return base

    def test_high_needs_one_of_the_four_cases(self):
        fixed, tl = {"W": {"value": "否"}}, {"segments": [seg(0, 60, "tsxk")], "summary": {}}
        self.assertIn("填高只限", "".join(K.check(self.reply(), None, fixed, tl)))
        speech = ["这里其实和原著里罗峰的心境完全不一样", "动画把虚拟宇宙的设定讲得更清楚了"]
        good = self.reply(Z_basis="知识解读", Z_quotes=speech)
        self.assertEqual(K.check(good, None, fixed, tl, sources=speech), [])                   # WO-102-R4 §2
        made_up = self.reply(Z_basis="知识解读", Z_quotes=["UP主深刻分析了宇宙规则的本质", "他认为这是神作"])
        self.assertIn("核对上 0 句", "".join(K.check(made_up, None, fixed, tl, sources=speech)))
        mismatch = self.reply(Z="中", AC=dict(self.reply()["AC"], 比对意见="合理使用可能性较高。"))
        self.assertIn("要和 Z（中）一致", "".join(K.check(mismatch, None, fixed, tl)))
        self.assertIn("没有 AI 创作声明", "".join(K.check(self.reply(Z_basis="AI创作"), None, fixed, tl)))
        self.assertEqual(K.check(self.reply(Z="中"), None, fixed, tl), [])

    def test_two_check_sets_and_every_list_excluded_from_style(self):
        from pilot.tsxk.config import TsxkCase
        case = TsxkCase(Path(__file__).resolve().parents[3] / "cases" / "tsxk.yaml")
        a, b = case.check_set("a"), case.check_set("b")
        self.assertEqual((a["out"].name, b["out"].name), ("check", "check30b"))
        self.assertEqual((a["list"].name, b["list"].name), ("tsxk_check30.csv", "tsxk_check30b.csv"))
        self.assertEqual(b["known_unreliable"], {})
        self.assertEqual([p.name for p in case.check_lists()], ["tsxk_check30.csv", "tsxk_check30b.csv"])



class RateLimitTest(unittest.TestCase):
    def test_a_rate_limit_waits_lowers_the_limit_and_asks_again(self):
        from unittest import mock
        from pilot.semantic import llm
        from pilot.timeline import cloud as C
        limited = llm.Reply("zhipu", "glm", error='HTTP 429: {"error":{"code":"1302"}}')
        good = llm.Reply("zhipu", "glm", data={"AC": {}})
        waits = []
        C.THROTTLE["zhipu"] = C.Throttle("zhipu")
        with mock.patch.object(K.llm, "ask", side_effect=[limited, limited, good]):
            reply = K.ask_patiently("zhipu", "p", "s", waits=(1, 2, 3), sleep=waits.append)
        self.assertEqual((reply.data, waits, C.THROTTLE["zhipu"].cap()), ({"AC": {}}, [1, 2], 4))


if __name__ == "__main__":
    unittest.main()
