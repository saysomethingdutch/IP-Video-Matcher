import unittest
from unittest import mock

from pilot.opinion import pipeline as PL
from pilot.opinion.proportion import episode_label, minutes_seconds, pct_text, proportion
from pilot.opinion.validate import CONCLUSION, validate
from pilot.semantic.llm import Reply

EPS = {1: 2400.0, 2: 2460.0, 3: 2520.0}


def digest(evidence=(), claim="2", video=126, te=None):
    return {"episodes": {"evidenced": [{"episode": e, "suspected": False} for e in evidence], "title_episode": te},
            "plaintiff": {"part_claimed": claim}, "ledger": {"video_duration": {"value": video}}}


class ProportionTest(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(episode_label([3, 4]), "第3、4集")
        self.assertEqual(episode_label([1, 2, 3, 8]), "第1-3、8集")
        self.assertEqual((minutes_seconds(126), minutes_seconds(45), minutes_seconds(180)), ("2分6秒", "45秒", "3分钟"))
        self.assertEqual((pct_text(0.7), pct_text(5.2249), pct_text(0.001)), ("0.7", "5.22", "0.01"))

    def test_single_episode_with_evidence(self):
        p = proportion(digest(evidence=[2]), EPS)
        self.assertEqual(p["text"], "对应剧集为第2集，约长41分钟，被诉视频引用原作品的画面仅2分6秒，占原作品比例较少，为5.12%，足以构成适当引用。")
        self.assertEqual(p["flags"], [])

    def test_over_ten_percent_uses_the_short_template(self):
        p = proportion(digest(evidence=[1], video=600), EPS)
        self.assertEqual(p["text"], "对应剧集为第1集，约长40分钟，占原作品比例较少，足以构成适当引用。")

    def test_title_episode_before_the_claim(self):
        p = proportion(digest(claim="3-4", te={"episodes": [2]}), EPS)
        self.assertTrue(p["text"].startswith("对应剧集为第2集，约长41分钟，被诉视频引用原作品的画面仅2分6秒"))
        self.assertIn("标题/台标", p["flags"][0]["reason"])

    def test_claim_only_has_no_percentage(self):
        p = proportion(digest(claim="3-4"), EPS)
        self.assertTrue(p["text"].startswith("对应剧集为原告主张的第3、4集，每集平均长约"))
        self.assertTrue(p["text"].endswith("分钟，占原作品比例较少，足以构成适当引用。"))
        self.assertNotIn("%", p["text"])
        reasons = " ".join(f["reason"] for f in p["flags"])
        self.assertIn("原告主张，未核实，不算百分比", reasons)
        self.assertIn("第4集本地缺片", reasons)

    def test_unknown_duration(self):
        p = proportion(digest(evidence=[2], video="未知"), EPS)
        self.assertEqual(p["text"], "对应剧集为第2集，约长41分钟，占原作品比例较少，足以构成适当引用。")


PROP = "对应剧集为第2集，长约41分钟，被诉视频引用原作品的画面仅2分6秒，占原作品比例较少，为5.12%，足以构成适当引用。"
GOOD_PURPOSE = ("该视频中作者对祥云与初空的相处进行评论，认为两人斗嘴其实是在互相关心，并配以UP主字幕解读人物心理，"
                "**将原作中的零散情节转化为作者对人物关系的解读**，具有转换性。")
GOOD_SUB = "观众观看该视频是为了了解UP主的观点，且时长较短，不会对原剧产生实质性替代，反而起到宣传推广作用。"
CTX = {"title": "【七时吉祥】第2集解读", "up_texts": ["两人斗嘴其实是在互相关心", "初空嘴硬心软"], "foreign": [],
       "picture": {"Z": "否", "AA": "是，存在UP主字幕", "AB": "否"}, "proportion": PROP,
       "evidence": [{"point": "观点", "quote": "两人斗嘴其实是在互相关心"}]}


def opinion(purpose=GOOD_PURPOSE, sub=GOOD_SUB):
    return PL.assemble(purpose, PROP, sub), {"purpose": purpose, "substitution": sub}


class ValidateTest(unittest.TestCase):
    def test_good_opinion_passes(self):
        self.assertEqual(validate(*opinion(), CTX), [])
        self.assertTrue(opinion()[0].endswith(CONCLUSION))

    def test_each_rule_catches(self):
        cases = {
            "数字": GOOD_PURPOSE.replace("进行评论", "在第9集进行评论"),
            "引号": GOOD_PURPOSE.replace("认为两人", "认为“两人其实早就相爱”，两人"),
            "作品名": GOOD_PURPOSE.replace("进行评论", "对照《长相思》进行评论"),
            "禁用语": GOOD_PURPOSE.replace("进行评论", "进行评论，并播放了原剧片段"),
            "加粗": GOOD_PURPOSE.replace("**", ""),
            "画面": GOOD_PURPOSE.replace("并配以", "UP主真人出镜，并配以"),
            "第一段结尾": GOOD_PURPOSE.replace("具有转换性。", "值得一看。"),
        }
        for rule, purpose in cases.items():
            errors = validate(*opinion(purpose), CTX)
            self.assertTrue(any(e.startswith(rule) for e in errors), (rule, errors))

    def test_boilerplate_is_not_specific(self):
        generic = "该视频对剧情进行了评论，**表达了作者的看法**，具有转换性。"
        errors = validate(*opinion(generic), {**CTX, "evidence": [{"quote": "初空嘴硬心软"}]})
        self.assertTrue(any(e.startswith("具体性") for e in errors))

    def test_few_quotes(self):
        many = GOOD_PURPOSE.replace("认为两人", "称“两人斗嘴”“互相关心”“初空嘴硬心软”，认为两人")
        self.assertTrue(any(e.startswith("引号：引用了 3 处") for e in validate(*opinion(many), CTX)))

    def test_length_limits(self):
        long = GOOD_PURPOSE.replace("进行评论", "进行评论" + "，两人斗嘴其实是在互相关心" * 20)
        self.assertTrue(any(e.startswith("字数") for e in validate(*opinion(long), CTX)))

    def test_type_follows_the_classification(self):
        mixed = GOOD_PURPOSE.replace("该视频中", "该视频为夹叙夹议解说，")
        sub = GOOD_SUB.replace("观众观看该视频", "该视频为观点评析，观众观看")
        self.assertTrue(any(e.startswith("类型") for e in validate(*opinion(mixed, sub), {**CTX, "nature": "不确定"})))
        self.assertTrue(any(e.startswith("类型") for e in validate(*opinion(mixed), {**CTX, "nature": "观点评析"})))
        self.assertFalse(any(e.startswith("类型") for e in validate(*opinion(mixed), {**CTX, "nature": "夹叙夹议解说"})))

    def test_title_only_rows_may_lean_on_the_title(self):
        purpose = "该视频为人物向混剪，围绕标题中陆长空历劫归来的主题，**集中呈现二人的追忆情感**，具有转换性。"
        ctx = {**CTX, "title": "仙君历劫归来，世间再无陆长空", "up_texts": [], "picture": {"Z": "否", "AA": "否", "AB": "否"},
               "evidence": [{"quote": "世间再无陆长空"}]}
        self.assertTrue(any(e.startswith("具体性") for e in validate(*opinion(purpose), ctx)))
        errors = validate(*opinion(purpose), {**ctx, "title_only": True})
        self.assertFalse(any(e.startswith(("具体性", "证据")) for e in errors), errors)

    def test_evidence_must_be_verbatim(self):
        errors = validate(*opinion(), {**CTX, "evidence": [{"quote": "编出来的话"}]})
        self.assertTrue(any(e.startswith("证据") for e in errors))


def row(ag="是", nature="reaction", cls=True, red=False):
    return {"row_id": "1", "classification": {"nature": nature} if cls else None,
            "fields": {"AG": {"value": ag}, "AF": {"value": "同序号9，重复取证"}},
            "flags": [{"level": "red", "reason": "疑似故意跳播：…"}] if red else []}


class RouteTest(unittest.TestCase):
    def test_routes(self):
        self.assertEqual(PL.route(row(cls=False)), "repeat")
        self.assertEqual(PL.route(row(red=True)), "skip")
        self.assertEqual(PL.route(row(ag="否")), "slash")
        self.assertEqual(PL.route(row(ag="不确定", nature="不确定")), "draft")
        op = PL.opinion_for(row(red=True), None, {}, "", "deepseek", None)
        self.assertEqual((op["text"], op["flags"][0]["level"]), (PL.SKIP_PLACEHOLDER, "red"))
        self.assertIn("故意跳播", op["candidate"])

    def test_one_retry_with_the_errors(self):
        replies = [Reply("deepseek", "deepseek-flash", data={"purpose": "太短", "substitution": GOOD_SUB, "evidence": []},
                         usage={"prompt": 10, "completion": 5}),
                   Reply("deepseek", "deepseek-flash", data={"purpose": GOOD_PURPOSE, "substitution": GOOD_SUB,
                                                             "evidence": CTX["evidence"]}, usage={"prompt": 10, "completion": 5})]
        prompts = []

        def fake(backend, prompt, **kw):
            prompts.append(prompt)
            return replies[len(prompts) - 1]
        f = {"title": CTX["title"], "up_texts": CTX["up_texts"], "foreign": [], "picture": CTX["picture"]}
        with mock.patch.object(PL, "ask", fake), mock.patch.object(PL.P, "retry_prompt", lambda f, prev, errs: "重试：" + "；".join(errs)), \
                mock.patch.object(PL.P, "user_prompt", lambda f: "初稿"):
            out = PL.draft({}, f, {"text": PROP}, "system", "deepseek")
        self.assertEqual((out["errors"], len(out["calls"])), ([], 2))
        self.assertTrue(prompts[1].startswith("重试：") and "具体性" in prompts[1] and "加粗" in prompts[1])


class AbTest(unittest.TestCase):
    def test_sample_is_stratified_and_fixed(self):
        from pilot.opinion.ab import sample
        natures = ["reaction"] * 8 + ["混剪"] * 8 + ["观点评析"] * 4 + ["夹叙夹议解说"] * 4 + ["不确定"] * 8 + ["reaction"]
        rows = {str(1000 + i): {"classification": {"nature": n}, "flags": [], "opinion": {"text": "x"},
                                "fields": {"AG": {"value": "是"}}} for i, n in enumerate(natures)}
        picked = sample(rows)
        self.assertEqual(len(picked), 20)
        self.assertEqual(picked, sample(rows))
        kinds = [rows[p]["classification"]["nature"] for p in picked]
        self.assertEqual((kinds.count("reaction"), kinds.count("混剪"), kinds.count("不确定")), (5, 5, 5))


if __name__ == "__main__":
    unittest.main()
