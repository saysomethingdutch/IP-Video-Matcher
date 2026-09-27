"""WO-004b-R2: 分P and repeated captures, the WO-008 sound interface, story_edit_policy."""
import json
import tempfile
import unittest
from pathlib import Path

from pilot.semantic import fields as F
from pilot.semantic.audio import describe, load_audio
from pilot.semantic.classify import facts_text
from pilot.semantic.digest import up_texts
from pilot.semantic.parts import analyse, parse_part
from pilot.semantic.pipeline import process_row

RULES = F.load_rules()
T4086 = "【丁禹兮考古向】七时16 reaction 七时16-{}"          # batch 4086 titles (WO-007)


def ledger_row(rid, title, bv="BV1x4SHYBEmD", parts=(), complete=True, seconds=300.0, dups=()):
    return {"row_id": rid, "plaintiff": {"title": title, "bv": bv}, "parts": list(parts), "complete": complete,
            "capture_seconds": seconds, "duplicate_bv_rows": list(dups)}


class PartsTest(unittest.TestCase):
    def test_title_endings(self):
        p = parse_part(T4086.format(4), ["16-1", "16-2", "16-3", "16-4", "60-0"])   # "60-0" is a clock reading
        self.assertEqual((p["n"], p["m"]), (4, 4))
        self.assertEqual(parse_part("【七时吉祥】〔泰配〕EP.31→ reaction 1/2")["m"], 2)
        self.assertEqual(parse_part("初空祥云混剪（3）")["n"], 3)
        self.assertEqual(parse_part("七时吉祥 解说 P2")["n"], 2)
        self.assertEqual(parse_part("无标记", ["P1"])["n"], 1)
        self.assertIsNone(parse_part("【七时吉祥】〔泰配〕EP.31→ 泰国小哥reaction"))
        self.assertIsNone(parse_part("【丁禹兮考古向】七时37 reaction"))

    def test_4086_parts_are_not_repeats(self):
        rows = {str(1663 + i): ledger_row(str(1663 + i), T4086.format(n), dups=[str(1663 + j) for j in range(4) if j != i])
                for i, n in enumerate((4, 1, 2, 3))}
        rows["1663"]["parts"] = ["16-1", "16-2", "16-3", "16-4"]
        info, repeats = analyse(rows)
        self.assertEqual(repeats, {})
        v, w = F.parts_columns({"row_id": "1664", "title": {"value": T4086.format(1)},
                                "ledger": {"parts": {"value": []}, "duplicate_bv_rows": []}}, {}, info["1664"])
        self.assertEqual((v["value"], w["value"]), ("分P", "共4P，取证了第1P"))

    def test_only_the_same_part_is_a_repeat(self):
        rows = {"1": ledger_row("1", T4086.format(2), seconds=100), "2": ledger_row("2", T4086.format(2), seconds=200),
                "3": ledger_row("3", "单P视频", bv="BV2"), "4": ledger_row("4", "单P视频", bv="BV2", complete=False),
                "5": ledger_row("5", "【丁禹兮考古向】七时37 reaction 七时37-1", bv="BV3"),
                "6": ledger_row("6", "【丁禹兮考古向】七时37 reaction", bv="BV3")}
        info, repeats = analyse(rows)
        self.assertEqual(repeats, {"1": "2", "4": "3"})              # the fuller capture is the main row
        self.assertIn("本行未识别", info["6"]["flags"][0])            # part unknown on one side: compared, flagged
        self.assertEqual((info["5"]["part"]["n"], info["5"]["total"], info["5"]["flags"]), (1, None, []))
        rows["7"] = ledger_row("7", "【丁禹兮考古向】七时37 reaction 七时37-3", bv="BV3")
        self.assertIn("推断", analyse(rows)[0]["5"]["flags"][0])    # total inferred from the batch's other parts


    def test_single_part_video_captured_twice_is_not_a_part(self):
        rows = {"1": ledger_row("1", "单P视频", bv="BV2"), "2": ledger_row("2", "单P视频", bv="BV2", complete=False)}
        info, _ = analyse(rows)
        v, _ = F.parts_columns({"row_id": "1", "title": {"value": "单P视频"}, "ledger": {"parts": {"value": []},
                                "duplicate_bv_rows": []}}, {}, info["1"])
        self.assertEqual(v["value"], "未识别")                       # not "分P"


class CarryTest(unittest.TestCase):
    def test_opinion_survives_a_classification_rerun(self):
        from pilot.semantic.pipeline import carry_opinion
        prev = {"opinion": {"text": "意见"}, "classification": {"nature": "不确定"},
                "fields": {"AF": {"value": "意见"}, **{c: {"value": "不确定"} for c in ("AG", "AH", "AI", "AJ")}},
                "flags": [{"level": "yellow", "reason": "意见：外语转写，请核对大意"}]}
        new = {"row_id": "1", "classification": {"nature": "夹叙夹议解说"}, "flags": [],
               "fields": {"AF": {"value": ""}, **{c: {"value": "是"} for c in ("AG", "AH", "AI", "AJ")}}}
        carry_opinion(new, prev)
        self.assertEqual(new["fields"]["AF"]["value"], "意见")
        from pilot.semantic.pipeline import mark_stale_opinions
        self.assertEqual(mark_stale_opinions([new]), ["1"])
        self.assertEqual([f["reason"][:12] for f in new["flags"]], ["意见：外语转写，请核对大意"[:12], "意见：分类已变（不确定 → 夹叙"[:12]])
        self.assertNotIn("_prev_state", new)


class AudioTest(unittest.TestCase):
    def audio(self, payload):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        (Path(tmp.name) / "audio").mkdir()
        (Path(tmp.name) / "audio" / "1663.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return load_audio(Path(tmp.name), "1663")

    def test_interface_and_spellings(self):
        a = self.audio({"engine": "SenseVoice-Small", "segments": [
            {"start": 3.2, "end": 6.8, "text": "这个剧情我真的笑死", "kind": "uploader"},
            {"start": 9.0, "end": 11.0, "text": "你给我闭嘴", "kind": "source", "episode": 16},
            {"content_start_seconds": 20.0, "text": "他俩太好嗑了", "classification": "NON_ORIGINAL"}],
            "events": {"speech_ratio": 0.62, "bgm": 0.3}, "uploader_music": False})
        self.assertEqual([s["text"] for s in a["up_speech"]], ["这个剧情我真的笑死", "他俩太好嗑了"])
        self.assertEqual((a["speech_ratio"], a["bgm_ratio"], a["uploader_music"]), (0.62, 0.3, False))
        self.assertEqual(describe(a), "UP主口播 2 句（已并入【UP主文字】）；疑似UP主配乐：否")    # no raw shares (R4)
        self.assertIsNone(load_audio(Path(tempfile.gettempdir()), "no-such-row"))

    def test_uploader_speech_joins_the_uploader_text(self):
        a = self.audio({"segments": [{"start": 5, "text": "我觉得男主演得太好了", "kind": "uploader"}],
                        "events": {"speech": 0.5, "bgm": 0.1}, "uploader_music": {"suspected": False}})
        lines, _ = up_texts({"events": []}, None, audio=a)
        self.assertEqual([(l["kind"], l["text"]) for l in lines], [("口播", "我觉得男主演得太好了")])


def digest(nature_tags=None, music=(), audio=None):
    return {"row_id": "1", "title": {"value": "初空祥云字幕剪辑"}, "uploader": {"value": "up"},
            "plaintiff": {"views_claimed": None, "part_claimed": "3", "video_type": "切条"},
            "ledger": {"video_duration": {"value": 120}, "capture_seconds": {"value": 120}, "complete": {"value": True},
                       "views": {"value": None}, "parts": {"value": []}, "duplicate_bv_rows": [], "seeks": {"value": []}},
            "episodes": {"verdict": "SUPPORTED", "evidenced": [{"episode": 3}], "statement": "…", "title_episode": None,
                         "source": "content"},
            "visual": None, "music": {"value": list(music), "source": "OCR"}, "music_counted": 0,
            "caption_counts": {"底部解说字幕": 0}, "source_lines": {"count": 0, "episodes": []},
            "up_texts": [{"i": i, "text": f"第{i}句", "kind": "底部解说字幕"} for i in range(1, 11)],
            "up_text_extra": {}, "asr": "无 ASR", "other_batches": {"value": []}, "audio": audio}


def saved(nature, model_nature, tags, recreation=None):
    return {"classification": {"nature": nature, "model_nature": model_nature, "subtype": None, "recreation": recreation,
                               "comment_share": None, "tagged_lines": len(tags), "tags": tags,
                               "evidence": [{"point": "事实", "quote": None, "fact": "…"}], "reason": ""},
            "classification_flags": [], "calls": []}


def grid(result):
    return " / ".join(result["fields"][c]["value"] for c in ("AG", "AH", "AI", "AJ"))


class PolicyTest(unittest.TestCase):
    def run_row(self, d, reuse, policy):
        rules = {**RULES, "story_edit_policy": policy}
        return process_row({"row_id": "1"}, d, rules, None, None, "deepseek", [], reuse)

    def test_strict_and_lenient(self):
        plot = {str(i): "叙述剧情" for i in range(1, 11)}
        for policy, expected in (("strict", "否 / 是 / / / 剧情解说"), ("lenient", "是 / 是 / 混剪类 / 混剪类")):
            r = self.run_row(digest(music=["眉间雪"]), saved("剧情解说", "剧情解说", plot), policy)
            self.assertEqual(grid(r), expected, policy)
        self.assertTrue(any(f["reason"].startswith("口径：宽松") for f in r["flags"]))
        r = self.run_row(digest(), saved("剧情解说", "剧情解说", plot), "lenient")      # nothing thematic: stays
        self.assertEqual(grid(r), "否 / 是 / / / 剧情解说")

    def test_team_line_is_reapplied_to_saved_judgments(self):
        tags = {str(i): ("评论观点" if i in (1, 4, 7, 10) else "叙述剧情") for i in range(1, 11)}   # 4 opinions, 0.4
        r = self.run_row(digest(), saved("不确定", "夹叙夹议解说", tags), "strict")
        self.assertEqual(grid(r), "是 / 是 / 夹叙夹议类解说 / 夹叙夹议解说")
        self.assertTrue(any(f["reason"].startswith("观点偏少") for f in r["flags"]))

    def test_uploader_music_is_recreation(self):
        audio = {"up_speech": [], "speech_ratio": 0.05, "bgm_ratio": 0.9, "laughter_ratio": 0.0,
                 "uploader_music": True, "music_basis": "", "source": "audio/1.json"}
        r = self.run_row(digest(audio=audio), saved("混剪", "混剪", {}, recreation="很少"), "strict")
        self.assertEqual(grid(r), "是 / 是 / 混剪类 / 混剪类")

    def test_no_sound_file_leaves_the_prompt_unchanged(self):
        self.assertNotIn("【声音】", facts_text(digest()))
        audio = {"up_speech": [{"t": 1, "text": "x"}], "speech_ratio": 0.6, "bgm_ratio": 0.9, "laughter_ratio": 0.0,
                 "uploader_music": None, "music_label": "无旁证", "source": "audio/1.json"}
        self.assertIn("【声音】UP主口播 1 句（已并入【UP主文字】）；疑似UP主配乐：无旁证", facts_text(digest(audio=audio)))
        self.assertNotIn("BGM", facts_text(digest(audio=audio)))                       # raw BGM share never reaches the model
        bgm_only = {**audio, "up_speech": []}                                          # nothing corroborated: no 【声音】
        self.assertEqual(facts_text(digest(audio=bgm_only)), facts_text(digest()))


if __name__ == "__main__":
    unittest.main()
