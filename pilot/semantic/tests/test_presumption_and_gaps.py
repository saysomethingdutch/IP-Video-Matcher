"""WO-004b-R3: skipped reactions are presumed; missing claimed episodes never give a bare "否"."""
import unittest

from pilot.semantic import fields as F
from pilot.semantic.pipeline import presume_skipped_reactions

RULES = F.load_rules()


def row(rid, nature, title="【丁禹兮考古向】七时29 reaction 七时29-1", seeks=({"at": 10},), comments=0, up="丁禹兮考古向"):
    grid, _ = F.four_grid(nature, None, None, bool(seeks), RULES)
    tags = {str(i): "评论观点" for i in range(comments)} | {"99": "叙述剧情"}
    return {"row_id": rid, "classification": {"nature": nature, "model_nature": nature, "tags": tags},
            "digest": {"uploader": {"value": up}, "title": {"value": title}, "ledger": {"seeks": {"value": list(seeks)}},
                       "audio": None, "up_text_extra": {}},
            "fields": {**dict(zip(("AG", "AH", "AI", "AJ"), (F.col(g, "x") for g in grid))), "Y": F.col("00:05:00", "x")},
            "flags": [{"level": "yellow", "reason": "原片搬运：四格待所有者确认"}] if nature == "原片搬运" else []}


def grid(r):
    return " / ".join(r["fields"][c]["value"] for c in ("AG", "AH", "AI", "AJ"))


class PresumptionTest(unittest.TestCase):
    def test_silent_skipped_part_of_a_reaction_series(self):
        rs = [row("1681", "reaction", comments=5), row("1689", "原片搬运"), row("1690", "原片搬运", seeks=())]
        self.assertEqual(presume_skipped_reactions(rs, RULES), ["1689"])
        self.assertEqual(grid(rs[1]), "是 / 是 / 跳播+Reaction类 / 跳播+Reaction类")
        self.assertEqual(rs[1]["fields"]["Y"]["value"], "/")
        self.assertEqual([f["reason"] for f in rs[1]["flags"]], [RULES["skip_reaction_presumption"]["flag"]])
        self.assertEqual(grid(rs[2]), "否 / 否 / / / /")                 # no skip: judged as before
        self.assertEqual(rs[1]["classification"]["presumed_from"], "原片搬运")

    def test_series_known_from_the_uploader_even_without_the_word(self):
        rs = [row("1", "reaction", title="七时吉祥第3集", comments=4), row("2", "原片搬运", title="七时吉祥第4集")]
        self.assertEqual(presume_skipped_reactions(rs, RULES), ["2"])

    def test_no_presumption_when_the_whole_uploader_is_silent(self):
        rs = [row("1", "原片搬运"), row("2", "原片搬运")]
        self.assertEqual(presume_skipped_reactions(rs, RULES), [])
        self.assertIn(RULES["skip_reaction_presumption"]["no_presumption_flag"], [f["reason"] for f in rs[0]["flags"]])

    def test_a_row_with_commentary_is_judged_on_it(self):
        rs = [row("1", "reaction", comments=3), row("2", "剧情解说", comments=1)]
        self.assertEqual(presume_skipped_reactions(rs, RULES), [])


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
