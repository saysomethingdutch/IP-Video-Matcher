import unittest
from unittest import mock

from pilot.semantic import classify as K
from pilot.semantic import fields as F
from pilot.semantic.llm import Reply, extract_json
from pilot.semantic.pipeline import repeated_captures

RULES = F.load_rules()


def digest(**kw):
    d = {"title": {"value": kw.get("title", "【七时吉祥】reaction")},
         "plaintiff": {"views_claimed": kw.get("claimed"), "part_claimed": kw.get("claim", "1-38")},
         "ledger": {"views": {"value": kw.get("views")}, "parts": {"value": kw.get("parts", [])},
                    "duplicate_bv_rows": kw.get("dups", []), "seeks": {"value": kw.get("seeks", [])}},
         "episodes": {"verdict": kw.get("verdict"), "evidenced": kw.get("evidenced", []), "statement": "…",
                      "title_episode": kw.get("te"), "source": "content"},
         "visual": kw.get("visual"), "music": {"value": kw.get("music", []), "source": "OCR"},
         "caption_counts": kw.get("counts", {}), "source_lines": {"count": 0, "episodes": []},
         "up_texts": kw.get("lines", []), "up_text_extra": {}}
    return d


class FieldTest(unittest.TestCase):
    def test_hms(self):
        self.assertEqual(F.hms(465), "00:07:45")
        self.assertEqual(F.hms(3725.4), "01:02:05")
        self.assertEqual(F.hms(None), "未知")
        self.assertEqual(F.hms("未知"), "未知")

    def test_parts(self):
        v, w = F.parts_columns(digest(title="EP.31→ 泰国小哥reaction→_ 1/2"), {})
        self.assertEqual((v["value"], w["value"]), ("分P", "共2P，取证了第1P"))
        v, w = F.parts_columns(digest(title="无标记", dups=["1052"]), {})
        self.assertEqual((v["value"], w["value"]), ("分P", "未识别"))
        v, w = F.parts_columns(digest(title="无标记", parts=["合集 5/27"]), {})
        self.assertEqual((v["value"], w["value"]), ("合集", "共27个，取证了第5个"))
        v, w = F.parts_columns(digest(title="无标记"), {"竖屏详情页": 3})
        self.assertEqual((v["value"], w["value"]), ("否", "/"))
        v, w = F.parts_columns(digest(title="无标记"), {})
        self.assertEqual((v["value"], w["value"]), ("未识别", "未识别"))

    def test_views(self):
        self.assertEqual(F.views_column(digest(views="270", claimed="270"))[0]["value"], "是")
        self.assertEqual(F.views_column(digest(views="1.2万", claimed="12000"))[0]["value"], "是")
        self.assertEqual(F.views_column(digest(views="11", claimed="——"))[0]["value"], "无，11")
        value, flags = F.views_column(digest(views="26", claimed="3193"))
        self.assertEqual((value["value"], flags[0]["level"]), ("否，26", "yellow"))
        self.assertEqual(F.views_column(digest(views=None, claimed="3"))[0]["value"], "未知")

    def test_episode(self):
        ev = [{"episode": 11}]
        self.assertEqual(F.episode_column(digest(verdict="SUPPORTED", evidenced=ev))[0]["value"], "是")
        value, flags = F.episode_column(digest(verdict="NARROWER", evidenced=ev, claim="1-38"))
        self.assertEqual(value["value"], "否，已取证部分对应第11集（原告声称1-38）")
        self.assertTrue(flags)
        value, _ = F.episode_column(digest(verdict="CONTRADICTED", evidenced=[{"episode": 3}, {"episode": 4}]))
        self.assertEqual(value["value"], "否，第3、4集")
        te = {"episodes": [7], "agrees": True}
        value, flags = F.episode_column(digest(verdict="UNVERIFIED", te=te, claim="7"))
        self.assertEqual(value["value"], "是（未能核实；标题/台标为第7集，与声称一致）")
        self.assertEqual(flags[0]["level"], "yellow")

    def test_visual_columns(self):
        vis = {"uploader_on_camera": "是", "layout": "原剧缩小加背景或边框", "other_works": "否", "other_works_desc": "",
               "overlays": ["贴纸或表情包", "UP主水印"], "rights_ratio_geometry": "约1/4", "rights_ratio_model": "约1/4",
               "source": "vlm"}
        z, _ = F.non_rights_column(digest(visual=vis, music=["光年之外"]))
        self.assertEqual(z["value"], "是，存在UP主真人出镜、背景画面、背景音乐《光年之外》")
        aa, _ = F.occlusion_column(digest(visual=vis, counts={"底部解说字幕": 5}))
        self.assertEqual(aa["value"], "是，存在UP主字幕、贴纸/表情包")          # no watermark
        ab, flags = F.size_column(digest(visual=vis))
        self.assertEqual((ab["value"], flags), ("是，画面缩小为约1/4", []))
        ab, flags = F.size_column(digest(visual={**vis, "rights_ratio_model": "约1"}))
        self.assertEqual(flags[0]["level"], "yellow")
        self.assertEqual(F.size_column(digest())[0]["value"], "未识别")
        ab, flags = F.size_column(digest(visual={**vis, "rights_ratio_geometry": "不确定", "rights_ratio_model": "不确定"}))
        self.assertEqual((ab["value"], flags[0]["level"]), ("未识别", "yellow"))
        settled = {**vis, "consensus": True, "rights_ratio_geometry": "约1", "rights_ratio_model": "约1/4"}
        self.assertEqual(F.size_column(digest(visual=settled))[0]["value"], "是，画面缩小为约1/4")   # R3: settled value wins
        ab, flags = F.size_column(digest(visual={**settled, "rights_ratio_model": "不确定"}))
        self.assertEqual((ab["value"], flags[0]["reason"]), ("未识别", "画面大小：三方定值为不确定"))
        unsure = {**vis, "uploader_on_camera": "不确定", "layout": "全屏"}
        z, flags = F.non_rights_column(digest(visual=unsure))
        self.assertEqual((z["value"], flags[0]["level"]), ("未识别", "yellow"))   # not a plain "否"


class MusicTest(unittest.TestCase):
    def test_song_names(self):
        from pilot.semantic.digest import song_names
        self.assertEqual(song_names(["眉间雪", "肩间雪", "Say U Love Me", "SayULoveMe", "唯爱"]),
                         (["眉间雪", "Say U Love Me", "唯爱"], 0))
        self.assertEqual(song_names(["发现2首音乐", "发现2首斋乐"]), ([], 2))
        z, _ = F.non_rights_column({**digest(visual=None, music=[]), "music_counted": 2})
        self.assertEqual(z["value"], "是，存在背景音乐（B站识别到2首，曲名未读到）")


class GridTest(unittest.TestCase):
    def grid(self, nature, subtype=None, recreation=None, jump=False):
        return F.four_grid(nature, subtype, recreation, jump, RULES)

    def test_client_rules(self):
        self.assertEqual(self.grid("剧情解说")[0], ["否", "是", "/", "剧情解说"])
        self.assertEqual(self.grid("夹叙夹议解说", jump=True)[0], ["是", "是", "跳播+夹叙夹议类解说", "跳播+夹叙夹议解说"])
        self.assertEqual(self.grid("观点评析", "恶搞讽刺")[0], ["是", "是", "观点评析（恶搞讽刺）", "观点评析类解说"])
        self.assertEqual(self.grid("reaction", jump=True)[0], ["是", "是", "跳播+Reaction类", "跳播+Reaction类"])
        self.assertEqual(self.grid("混剪", "CP向", "明显")[0], ["是", "是", "混剪类", "混剪类"])

    def test_undecided_cases_are_flagged(self):
        grid, flags = self.grid("混剪", "CP向", "很少")
        self.assertEqual((grid, flags[0]["level"]), (["不确定"] * 4, "yellow"))
        grid, flags = self.grid("原片搬运")
        self.assertEqual(grid, ["否", "否", "/", "/"])
        self.assertIn("待所有者确认", flags[0]["reason"])
        grid, flags = self.grid("观点评析", "胡编")
        self.assertEqual(grid[2], "观点评析（待定）")
        self.assertEqual(self.grid("不确定")[0], ["不确定"] * 4)


def fake_ask(nature, tags, quote="我觉得他爸妈对他真的好好呀"):
    def ask(backend, prompt, images=(), **kw):
        if "逐句判断类型" in prompt:
            wanted = [int(x.split(".")[0]) for x in prompt.splitlines() if x[:1].isdigit()]
            return Reply(backend, "fake", data={"tags": [{"i": i, "tag": tags[(i - 1) % len(tags)]} for i in wanted]})
        return Reply(backend, "fake", data={"nature": nature, "subtype": "细节分析",
                                            "evidence": [{"point": "p", "quote": quote}, {"point": "p", "quote": "编造的话"}]})
    return ask


class ClassifyTest(unittest.TestCase):
    def run_with(self, nature, tags):
        lines = [{"i": i, "text": t, "kind": "底部解说字幕"} for i, t in
                 enumerate(["我觉得他爸妈对他真的好好呀"] + [f"第{i}句" for i in range(2, 11)], 1)]
        with mock.patch.object(K, "ask", fake_ask(nature, tags)):
            return K.classify(digest(lines=lines), "local", RULES)

    def test_commentary_is_split_by_comment_share(self):
        self.assertEqual(self.run_with("剧情解说", ["评论观点", "评论观点", "叙述剧情"])["nature"], "夹叙夹议解说")
        self.assertEqual(self.run_with("观点评析", ["评论观点", "评论观点", "叙述剧情"])["nature"], "观点评析")
        self.assertEqual(self.run_with("夹叙夹议解说", ["叙述剧情"] * 9 + ["其他"])["nature"], "剧情解说")
        r = self.run_with("夹叙夹议解说", ["评论观点", "叙述剧情", "叙述剧情"])
        self.assertEqual(r["nature"], "不确定")
        self.assertTrue(any("评论占比" in f["reason"] for f in r["flags"]))

    def test_only_verbatim_quotes_survive(self):
        r = self.run_with("reaction", ["评论观点"])
        self.assertEqual([e["quote"] for e in r["evidence"]], ["我觉得他爸妈对他真的好好呀"])
        self.assertTrue(any("找不到" in f["reason"] for f in r["flags"]))

    def test_one_call_prompt_points_at_the_collage(self):
        d = digest(lines=[])
        self.assertIn("【画面事实】无", K.nature_prompt(d))
        self.assertIn("见附图", K.nature_prompt(d, with_image=True))
        self.assertIn("【画面事实】布局", K.nature_prompt(d, vision_note="布局：全屏"))

    def test_prompt_templates_have_no_forbidden_words(self):
        text = K.template_words()
        self.assertEqual([w for w in K.FORBIDDEN if w in text], [])
        self.assertNotIn("视频类型", text)


class MiscTest(unittest.TestCase):
    def test_no_local_call_when_local_is_reserved(self):
        from pilot.semantic import llm
        with mock.patch.object(llm, "LOCAL_ALLOWED", False), mock.patch.dict("os.environ", {"DEEPSEEK_API_KEY": ""}), \
                mock.patch.object(llm, "_post", side_effect=AssertionError("no call expected")):
            r = llm.ask("deepseek", "x")                     # no key: the fallback would be local
        self.assertIsNone(r.data)
        self.assertIn("D-026", r.error)

    def test_repeated_capture_needs_the_same_part(self):
        def row(rid, title, dups, complete, seconds):
            return {"row_id": rid, "plaintiff": {"title": title}, "duplicate_bv_rows": dups,
                    "complete": complete, "capture_seconds": seconds}
        rows = {"1114": row("1114", "…looklook 2/2", ["1116"], False, 120.0),
                "1116": row("1116", "…looklook 2/2", ["1114"], False, 80.0),
                "1051": row("1051", "…looklook 1/2", ["1052"], False, 10.0),
                "1052": row("1052", "…looklook 2/2", ["1051"], False, 20.0)}
        self.assertEqual(repeated_captures(rows), {"1116": "1114"})

    def test_a_broken_response_is_an_error_not_a_crash(self):
        import http.client
        from pilot.semantic import llm
        with mock.patch.dict("os.environ", {"DEEPSEEK_API_KEY": "x"}), mock.patch.object(llm.time, "sleep"), \
                mock.patch.object(llm, "_post", side_effect=http.client.IncompleteRead(b"")):
            r = llm.ask("deepseek", "x")
        self.assertIsNone(r.data)
        self.assertTrue(r.error.startswith("IncompleteRead"))
        self.assertEqual(r.attempts, 3)                        # cloud calls get two retries

    def test_consistency_is_per_row_against_the_majority(self):
        from pilot.semantic.pipeline import consistency

        def res(rid, nature, jump):
            grid = ["是", "是", ("跳播+" if jump else "") + "Reaction类", ("跳播+" if jump else "") + "Reaction类"]
            d = digest(seeks=[{"at": 1}] if jump else [], lines=[{"i": 1, "text": "x"}])
            d["uploader"] = {"value": "up"}
            return {"row_id": rid, "digest": d, "flags": [],
                    "classification": {"nature": nature, "comment_share": None},
                    "fields": dict(zip(("AG", "AH", "AI", "AJ"), ({"value": g} for g in grid)))}
        rs = [res("1", "reaction", True), res("2", "reaction", False), res("3", "reaction", True), res("4", "原片搬运", True)]
        rs[3]["fields"] = rs[0]["fields"]                 # grid differences alone are checked too
        warnings = consistency(rs)
        self.assertEqual([w["row_id"] for w in warnings], ["4"])     # the 跳播 prefix alone is not a difference
        self.assertTrue(rs[3]["flags"][0]["reason"].startswith("一致性"))

    def test_extract_json(self):
        self.assertEqual(extract_json('<think>…</think>```json\n{"a": {"b": 1}}\n```'), {"a": {"b": 1}})
        self.assertEqual(extract_json('说明 {"x": "含}的字符串"} 结束'), {"x": "含}的字符串"})
        self.assertIsNone(extract_json("没有 JSON"))
        self.assertEqual(extract_json('前言 {"q": "他说\\"}"} 后记'), {"q": '他说"}'})   # escaped quote inside a string
        truncated = '{"tags": [{"i": 1, "tag": "其他"}, {"i": 2, "tag": "评'
        self.assertEqual(extract_json(truncated), {"i": 1, "tag": "其他"})
        self.assertIsNone(extract_json(truncated, ("tags",)))                       # not the inner object


if __name__ == "__main__":
    unittest.main()
