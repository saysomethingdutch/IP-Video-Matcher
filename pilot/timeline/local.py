"""Local screening (WO-102 §2.3, D-057 ④): free, exact and on this machine, so it goes first.

- Apple Vision OCR of the player crop: addresses, group numbers, cloud-drive and WeChat words → traffic;
  the official logo in the lower right → 吞噬星空; a film watermark (血洛大陆 / 决战原始星) → 剧场版;
  the player's hover reading "01:23 / 05:40" → a clock anchor (recording time ↔ video time).
- OpenCV QR detection and decoding → traffic, Tencent or other by the decoded domain. B站's own login code
  (passport.bilibili.com) is not traffic.
- A dark, flat frame → blank.

A frame settled here is not sent to the cloud. A traffic frame without the logo keeps content None: what it
shows underneath is inherited from its neighbours later (a code laid over the series, sample 12429).
"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

import cv2
import numpy as np
import yaml

from pilot.content.extract import MUSIC_TAG            # B站's "music detected" chip, the note read as "J"

RULES = Path(__file__).resolve().parents[1] / "semantic" / "rules_tsxk.yaml"
SITE_DOMAINS = ("bilibili.com", "b23.tv", "biliapi", "hdslb.com", "acg.tv")      # the page itself, not traffic
READING = re.compile(r"(?<!\d)(\d{1,2}:\d{2}(?::\d{2})?)\s*/\s*(\d{1,2}:\d{2}(?::\d{2})?)(?!\d)")
LYRIC = re.compile(r"[♪♫]|歌词|作词|作曲|^\s*[词曲]\s*[:：]")        # WO-102-R1 §1.5: lyric captions
CJK = re.compile(r"[\u4e00-\u9fff]")
WEAK_WORDS = ("扫码", "扫一扫", "完整版", "全集资源", "资源已外泄", "付费")           # only with another cue
TENCENT_PATTERNS = (r"(?:加|\+)\s*(?:vx|VX|v信|V信|威信|wx|WX)", r"微信\s*(?:号|群|扫|搜)", r"QQ\s*群", r"企鹅群")


def load_rules(path: str | Path | None = None) -> dict:
    return yaml.safe_load(Path(path or RULES).read_text(encoding="utf-8"))


def seconds(clock: str) -> int:
    parts = [int(p) for p in clock.split(":")]
    return sum(v * 60 ** i for i, v in enumerate(reversed(parts)))


def qr_codes(img: np.ndarray) -> list[str]:
    """Decoded payloads of the QR codes in a frame; "" for a code that was found but could not be read.
    Small codes in a corner are tried again at 1.6× size."""
    for scale in (1.0, 1.6):
        probe = img if scale == 1.0 else cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        for det in (cv2.QRCodeDetector(), cv2.QRCodeDetectorAruco()):
            try:
                ok, texts, _, _ = det.detectAndDecodeMulti(probe)
            except cv2.error:
                continue
            if ok:
                return [str(t) for t in texts]
    return []


def domain(text: str) -> str:
    t = text.strip()
    if not re.match(r"^[a-z]+://", t, re.I):
        t = "http://" + t
    return (urlparse(t).hostname or "").lower()


class Screen:
    def __init__(self, rules: dict):
        tr = rules["timeline"]["traffic"]
        self.tr = tr
        self.url = re.compile(tr["url_pattern"], re.I)
        self.group = re.compile(tr["group_pattern"], re.I)
        self.tencent = [re.compile(p) for p in TENCENT_PATTERNS] + [re.compile(re.escape(w)) for w in tr["tencent_words"]
                                                                   if len(w) >= 2 and not w.isascii()]
        self.strong_other = [w for w in tr["other_words"] if w not in WEAK_WORDS]
        logo = rules["work"]["official_logo"]
        self.logo_where, self.logo_text, self.logo_min = logo["where"], logo["text"], logo.get("min_match", 2)
        self.films = rules["work"]["film_watermarks"]
        self.ignore = tr.get("ignore_words") or []
        self.login_words = tr.get("login_box_words") or []
        self.undecoded_to_cloud = tr.get("undecoded_qr") == "cloud"
        self.ai_note = re.compile(rules["timeline"]["ai_note_pattern"], re.I) if rules["timeline"].get("ai_note_pattern") else None
        fuzzy = rules["timeline"].get("ai_note_top_fuzzy")
        self.ai_note_top = re.compile(fuzzy) if fuzzy else None

    def _in(self, item: dict, where) -> bool:
        x, y, w, h = where
        cx, cy = item["x"] + item["w"] / 2, item["y"] + item["h"] / 2
        return x <= cx <= x + w and y <= cy <= y + h

    def platform_of_domain(self, host: str) -> str | None:
        if not host or any(host == d or host.endswith("." + d) or d in host for d in SITE_DOMAINS):
            return None
        if any(host == d or host.endswith("." + d) for d in self.tr["tencent_domains"]):
            return "tencent"
        return "other"

    def login_box(self, items: list[dict], qrs: list[str]) -> bool:
        """B站's own login popup (WO-102-R2 A2): two sign-in methods on screen, or one and a QR code."""
        text = re.sub(r"\s", "", "".join(i["text"] for i in items))
        hits = {w for w in self.login_words if w in text}
        return len(hits) >= 2 or (len(hits) == 1 and bool(qrs))

    def traffic(self, items: list[dict], qrs: list[str]) -> dict | None:
        """{"platforms": [...], "kinds": [...], "evidence": [...]} or None. A code that cannot be read is not
        traffic on its own when undecoded_qr is "cloud" (WO-102-R2 A3): the frame goes to the cloud instead."""
        platforms, kinds, evidence = set(), set(), []
        if self.undecoded_to_cloud:
            qrs = [q for q in qrs if q]
        for q in qrs:
            host = domain(q) if q else ""
            if q and any(s in host for s in SITE_DOMAINS):
                continue                                          # B站 login code
            p = self.platform_of_domain(host) if host else "other"
            if p:
                platforms.add(p)
                kinds.add("网盘二维码" if any(d in host for d in self.tr["other_domains"]) else "二维码")
                evidence.append(f"二维码：{host or '未能解码'}")
        text = " ".join(i["text"] for i in items if not any(w in i["text"] for w in self.ignore))
        for m in self.url.finditer(text):
            p = self.platform_of_domain(domain(m.group(0)))
            if p:
                platforms.add(p)
                kinds.add("网盘链接" if any(d in m.group(0) for d in self.tr["other_domains"]) else "网址")
                evidence.append(f"网址：{domain(m.group(0))}")
        if self.group.search(text):
            platforms.add("tencent")
            kinds.add("群号")
            evidence.append("群号")
        hit = next((m.group(0) for p in self.tencent if (m := p.search(text))), None)
        if hit:
            platforms.add("tencent")
            kinds.add("微信或QQ引流")
            evidence.append(f"文字：{hit}")
        other = [w for w in self.strong_other if w in text]
        if other:
            platforms.add("other")
            kinds.add("网盘链接" if any(w in ("网盘", "云盘", "提取码") or "盘" in w for w in other) else "引流文字")
            evidence.append("文字：" + "、".join(other[:3]))
        if not platforms and qrs == [] and any(w in text for w in WEAK_WORDS):
            return None                                           # a weak word alone is not enough
        if not platforms:
            return None
        return {"platforms": sorted(platforms), "kinds": sorted(kinds), "evidence": evidence}

    def text_platforms(self, texts: list[str]) -> set[str]:
        """Platforms the frame's own words point to (D-065 ⑤-2: when the providers disagree on the platform,
        the side with text evidence wins)."""
        blob = " ".join(texts)
        out = set()
        if self.group.search(blob) or any(p.search(blob) for p in self.tencent):
            out.add("tencent")
        if any(w in blob for w in self.strong_other) or any(d in blob for d in self.tr["other_domains"]):
            out.add("other")
        return out

    def logo_like(self, text: str) -> bool:
        """The calligraphic logo as OCR reads it: 3–5 characters ending in 空, at least min_match of them in the
        same place as in 吞噬星空 ("吞噬呈空", "吞獵呈空", "容噬宝空" all count; "星空" or "天空" do not)."""
        t = re.sub(r"\s", "", text)
        if not 3 <= len(t) <= 5 or not t.endswith(self.logo_text[-1]):
            return False
        tail = t[-4:].rjust(4)
        return sum(1 for x, y in zip(tail, self.logo_text) if x == y) >= self.logo_min

    def logo_hit(self, items: list[dict]) -> str | None:
        for i in items:
            if self._in(i, self.logo_where) and self.logo_like(i["text"]):
                return i["text"]
        return None

    def film_hit(self, items: list[dict]) -> str | None:
        for i in items:
            if self._in(i, self.logo_where):
                for f in self.films:
                    if f in i["text"]:
                        return f
        return None

    @staticmethod
    def blank(img: np.ndarray) -> bool:
        grey = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        return float(grey.mean()) < 16 and float(grey.std()) < 10

    @staticmethod
    def reading(items: list[dict]) -> tuple[int, int] | None:
        """The player's hover clock in the bottom strip: (current, total) seconds."""
        for i in items:
            if i["y"] > 0.8 and (m := READING.search(i["text"].replace(" ", ""))):
                cur, total = seconds(m.group(1)), seconds(m.group(2))
                if 0 <= cur <= total:
                    return cur, total
        return None

    def ai_note_on_top(self, top_items: list[dict]) -> bool:
        """The AI label read from the top band OCR'd again at 2× (WO-102-R2 A4)."""
        return bool(self.ai_note_top) and any(self.ai_note_top.search(re.sub(r"\s", "", i["text"])) or
                                              (self.ai_note and self.ai_note.search(i["text"])) for i in top_items)

    def screen(self, frame: dict, items: list[dict], img: np.ndarray, top_items: list[dict] = ()) -> dict:
        """frame["local"]: content (or None), form, traffic, evidence; frame["ocr"]: text lines."""
        qrs = qr_codes(img)
        local = {"content": None, "form": "", "traffic": None, "evidence": []}
        if self.login_box(items, qrs):                   # the platform's own popup: neither traffic nor content
            local.update(login_box=True, evidence=["B站登录弹窗"])
            frame["local"] = local
            frame["ocr"] = [i["text"] for i in items if i.get("conf", 1) >= 0.3]
            return local
        if self.undecoded_to_cloud and "" in qrs:
            local["qr_undecoded"] = True                 # asked in the cloud; traffic only if both providers see it
        if self.blank(img):
            local.update(content="blank", evidence=["黑屏或无画面"])
        elif (film := self.film_hit(items)):
            local.update(content="tsxk_other", form="动画剧场版", evidence=[f"右下角水印：{film}"])
        elif (logo := self.logo_hit(items)):
            local.update(content="tsxk", evidence=[f"右下角台标：{logo}"])
        if (tr := self.traffic(items, qrs)):
            local["traffic"] = tr
            local["evidence"] += tr["evidence"]
        frame["local"] = local
        frame["ocr"] = [i["text"] for i in items if i.get("conf", 1) >= 0.3]
        frame["captions"] = [i["text"] for i in items if i["y"] + i["h"] / 2 >= 0.72 and len(CJK.findall(i["text"])) >= 4
                             and not self.logo_like(i["text"]) and not READING.search(i["text"])
                             and not MUSIC_TAG.match(i["text"].strip())]
        frame["music_tag"] = any(MUSIC_TAG.match(i["text"].strip()) for i in items)
        frame["lyric"] = any(LYRIC.search(i["text"]) for i in items)
        frame["ai_note"] = bool(self.ai_note and any(self.ai_note.search(i["text"]) for i in items)) or bool(
            self.ai_note_top and any(i["y"] + i["h"] / 2 < 0.2 and self.ai_note_top.search(re.sub(r"\s", "", i["text"]))
                                     for i in items)) or self.ai_note_on_top(top_items)
        if (r := self.reading(items)):
            frame["reading"] = list(r)
        return local
