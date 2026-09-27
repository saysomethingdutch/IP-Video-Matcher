"""The owner page keeps source labels outside the browser document."""
from pathlib import Path
from tempfile import TemporaryDirectory
from html.parser import HTMLParser
import unittest

from .robustness_owner import Candidate, _blind_rows, _formal, _page


class BlindOwnerPageTest(unittest.TestCase):
    def test_only_three_formal_opinions_qualify(self):
        formal = "【使用的性质和目的】" + "评论原剧。" * 20 + "【使用的比例】少量片段。【结论】该视频构成合理使用。"
        self.assertTrue(_formal(formal))
        self.assertFalse(_formal("/"))
        self.assertFalse(_formal("【待所有者决定：疑似故意跳播】"))

    def test_blind_parser_and_page_do_not_expose_mapping(self):
        formal = "【使用的性质和目的】" + "评论原剧。" * 20 + "【使用的比例】少量片段。【结论】该视频构成合理使用。"
        with TemporaryDirectory() as folder:
            path = Path(folder) / "blind.md"
            path.write_text("## 总编号 1688\n\n标题：测试标题\n\n" +
                            "\n\n".join(f"### 意见 {label}\n\n{formal}" for label in "甲乙丙"))
            parsed = _blind_rows(path)
        self.assertEqual(parsed["1688"][0], "测试标题")
        self.assertEqual(set(parsed["1688"][1]), set("甲乙丙"))
        row = Candidate("qsjx_4086", "1688", "测试标题", "reaction", parsed["1688"][1],
                        {"甲": "人工", "乙": "GLM", "丙": "DeepSeek"})
        page = _page([(row, row.opinions)])
        self.assertEqual(page.count('class="opinion"'), 3)
        for source in ("人工", "GLM", "DeepSeek"):
            self.assertNotIn(source, page)
        class Links(HTMLParser):
            def __init__(self):
                super().__init__()
                self.urls = []

            def handle_starttag(self, tag, attrs):
                self.urls.extend(value for name, value in attrs if name in ("src", "href"))

        links = Links()
        links.feed(page)
        self.assertEqual(links.urls, [])


if __name__ == "__main__":
    unittest.main()
