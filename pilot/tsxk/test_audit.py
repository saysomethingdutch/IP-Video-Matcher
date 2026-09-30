"""WO-105 audit must distinguish added pictures from subtitles and BGM."""
import unittest

from pilot.tsxk.audit import issues


def row(ac: str, **changes):
    fields = dict(D="标题", F="https://www.bilibili.com/video/BV1234567890", L="2026/3/24 11:13:30",
                  Q="否", R="是", S="无", T="否", U="是", V="否", W="否", X="否",
                  Y="二创（解说类）", Z="中", AA="无法判断", AB="无", AC=ac)
    fields.update(changes)
    return {"fields": {key: {"value": value} for key, value in fields.items()}, "flags": []}


class AuditTests(unittest.TestCase):
    def test_ai_picture_conflicts_with_w_but_subtitles_do_not(self):
        base = "【新增创作要素情况】\n{}\n【比对意见】\n合理使用可能性中等。"
        ai = row(base.format("新增AI生成配图，非原片画风；插入其他国漫画面"))
        text = row(base.format("新增UP主解说字幕，疑似增加背景音乐及水印"))
        self.assertIn("W_AC_VISUAL", {item[0] for item in issues(ai, {})})
        self.assertNotIn("W_AC_VISUAL", {item[0] for item in issues(text, {})})

    def test_music_description_does_not_imply_an_added_visual(self):
        machine = row("【新增创作要素情况】\n新增背景音乐，画面出现台词字幕但无人声\n"
                      "【比对意见】\n合理使用可能性较低。", V="是", U="否", Y="二创（单IP混剪类）", Z="低")
        codes = {item[0] for item in issues(machine, {})}
        self.assertNotIn("V_OTHER_ELEMENTS", codes)
        self.assertNotIn("W_AC_VISUAL", codes)

    def test_original_novel_wording_satisfies_form_description(self):
        machine = row("【段落情节】\n结合原著结局说明人物去向。\n"
                      "【新增创作要素情况】\n新增解说字幕\n"
                      "【比对意见】\n合理使用可能性中等。", T="是（小说）")
        codes = {item[0] for item in issues(machine, {}, "口播明确提到原著结局")}
        self.assertNotIn("FORM_AC", codes)
        self.assertNotIn("NOVEL_EVIDENCE", codes)

    def test_category_and_time_checks_report_without_changing_values(self):
        machine = row("【新增创作要素情况】\n无\n【比对意见】\n合理使用可能性较低。",
                      Y="偷渡", U="否", W="否", Z="中", X="是（其他）")
        original = {key: value["value"] for key, value in machine["fields"].items()}
        codes = {item[0] for item in issues(machine, {})}
        self.assertTrue({"SMUGGLE_W", "SMUGGLE_SPAN", "TRAFFIC_TIME", "Z_OPINION"} <= codes)
        self.assertEqual(original, {key: value["value"] for key, value in machine["fields"].items()})


if __name__ == "__main__":
    unittest.main()
