"""WO-004b-R3: skipped reactions are presumed; missing claimed episodes never give a bare "否"."""
import unittest

from pilot.semantic import fields as F
from pilot.semantic.pipeline import presume_skipped_reactions

RULES = F.load_rules()


def row(rid, nature, title="【丁禹兮考古向】七时29 reaction 七时29-1", seeks=({"at": 10},), comments=0, up="丁禹兮考古向",
        capture=200.0, duration=280.0):  # noqa: E501
    grid, _ = F.four_grid(nature, None, None, bool(seeks), RULES)
    tags = {str(i): "评论观点" for i in range(comments)} | {"99": "叙述剧情"}
    return {"row_id": rid, "classification": {"nature": nature, "model_nature": nature, "tags": tags},
            "digest": {"uploader": {"value": up}, "title": {"value": title}, "audio": None, "up_text_extra": {},
                       "ledger": {"seeks": {"value": list(seeks)}, "capture_seconds": {"value": capture},
                                  "video_duration": {"value": duration}}},
            "fields": {**dict(zip(("AG", "AH", "AI", "AJ"), (F.col(g, "x") for g in grid))), "Y": F.col("00:05:00", "x"),
                       "U": F.col("00:05:00", "x")},
            "flags": [{"level": "yellow", "reason": "原片搬运：四格待所有者确认"}] if nature == "原片搬运" else []}


def grid(r):
    return " / ".join(r["fields"][c]["value"] for c in ("AG", "AH", "AI", "AJ"))


SPEC = RULES["skip_reaction_presumption"]
PRESUMED = "是 / 是 / 跳播+Reaction类 / 跳播+Reaction类"


class PresumptionTest(unittest.TestCase):
    def test_no_or_few_comments_with_skips_are_presumed(self):
        rs = [row("1681", "reaction", comments=5), row("1689", "原片搬运", comments=2), row("1663", "原片搬运")]
        self.assertEqual(presume_skipped_reactions(rs, RULES), ["1689", "1663"])        # 1689: two opinions only
        self.assertEqual((grid(rs[1]), rs[1]["fields"]["Y"]["value"]), (PRESUMED, "/"))
        self.assertEqual([f["reason"] for f in rs[1]["flags"]], [SPEC["flag"]])
        self.assertEqual(rs[1]["classification"]["presumed_from"], "原片搬运")

    def test_a_very_short_capture_counts_as_a_skip(self):
        rs = [row("1681", "reaction", comments=5), row("1695", "原片搬运", seeks=(), capture=7, duration=297)]
        self.assertEqual(presume_skipped_reactions(rs, RULES), ["1695"])
        self.assertEqual(grid(rs[1]), PRESUMED)
        self.assertIn(SPEC["short_flag"], [f["reason"] for f in rs[1]["flags"]])
        long_share = [row("1", "reaction", comments=5), row("2", "原片搬运", seeks=(), capture=25, duration=3000)]
        self.assertEqual(presume_skipped_reactions(long_share, RULES), ["2"])       # < 10 % of a long video

    def test_a_commented_row_of_the_series_is_never_a_raw_upload(self):
        rs = [row("1", "reaction", comments=5), row("2", "原片搬运", seeks=(), comments=3)]
        self.assertEqual(presume_skipped_reactions(rs, RULES), ["2"])
        self.assertEqual(grid(rs[1]), "是 / 是 / Reaction类 / Reaction类")          # no skip: no prefix
        self.assertIn(SPEC["commented_flag"], [f["reason"] for f in rs[1]["flags"]])

    def test_few_comments_without_skip_is_left_to_the_owner(self):
        rs = [row("1", "reaction", comments=5), row("2", "原片搬运", seeks=())]
        presume_skipped_reactions(rs, RULES)
        self.assertEqual(grid(rs[1]), "不确定 / 不确定 / 不确定 / 不确定")
        self.assertIn(SPEC["undecided_flag"], [f["reason"] for f in rs[1]["flags"]])

    def test_series_known_from_the_uploader_even_without_the_word(self):
        rs = [row("1", "reaction", title="七时吉祥第3集", comments=4), row("2", "原片搬运", title="七时吉祥第4集")]
        self.assertEqual(presume_skipped_reactions(rs, RULES), ["2"])

    def test_no_presumption_when_the_whole_uploader_says_little(self):
        rs = [row("1", "原片搬运", comments=1), row("2", "原片搬运")]
        self.assertEqual(presume_skipped_reactions(rs, RULES), [])
        self.assertIn(SPEC["no_presumption_flag"], [f["reason"] for f in rs[0]["flags"]])

    def test_other_natures_with_commentary_stay(self):
        rs = [row("1", "reaction", comments=5), row("2", "剧情解说", comments=3)]
        self.assertEqual(presume_skipped_reactions(rs, RULES), [])

    def test_rerun_is_stable(self):
        rs = [row("1681", "reaction", comments=5), row("1689", "原片搬运", comments=2)]
        presume_skipped_reactions(rs, RULES)
        presume_skipped_reactions(rs, RULES)                                            # second pass changes nothing
        self.assertEqual([f["reason"] for f in rs[1]["flags"]].count(SPEC["flag"]), 1)
        self.assertEqual((grid(rs[1]), rs[1]["classification"]["presumed_from"]), (PRESUMED, "原片搬运"))


def digest(verdict, gap, claim="29", evidenced=()):
    return {"plaintiff": {"part_claimed": claim},
            "episodes": {"verdict": verdict, "evidenced": [{"episode": e} for e in evidenced], "statement": "…",
                         "title_episode": None, "source": "content", "claim_missing": gap}}


class GapColumnTest(unittest.TestCase):
    def test_ae_wording(self):
        v, f = F.episode_column(digest("MISSING_UNVERIFIED", {"missing": [29], "other_lines": 2, "reliable_lines": 0},
                                       evidenced=[30]))
        self.assertEqual(v["value"], "是（未能核实：声称集本地缺片，相邻集命中 2 句）")
        self.assertEqual(f[0]["level"], "yellow")
        v, _ = F.episode_column(digest("PARTLY_UNVERIFIED", {"missing": [20, 21, 22]}, claim="15-22", evidenced=[15, 16]))
        self.assertEqual(v["value"], "是（部分未能核实：第20、21、22集本地缺片）；已取证部分可核实为第15、16集")
        v, f = F.episode_column(digest("CONTRADICTED", {"missing": [34], "kept": True, "reliable_lines": 15, "other_lines": 15},
                                       claim="30-34", evidenced=[25]))
        self.assertEqual((v["value"], "可靠命中 15 句" in f[0]["reason"]), ("否，第25集", True))
        v, _ = F.episode_column(digest("UNVERIFIED", {"missing": [29]}))
        self.assertTrue(v["value"].startswith("是（未能核实：第29集本地缺片"))


if __name__ == "__main__":
    unittest.main()
