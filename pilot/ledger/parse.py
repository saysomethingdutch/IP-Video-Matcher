"""Deterministic OCR parsing and closed-set matching primitives."""
from __future__ import annotations

from difflib import SequenceMatcher
import re

BASE58 = set("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")
READING = re.compile(r"(?<!\d)(\d{1,3})[:;](\d{2})\s*[/7]\s*(\d{1,3})[:;](\d{2})(?!\d)")
TRUNCATED_READING = re.compile(r"(\d{1,3})[:;](\d{2})\s*[/7|]\s*(\d{1,2})\s*[:;]?")
SPEED = re.compile(r"(?<!\d)(0[.]5|0[.]75|1[.]0|1[.]25|1[.]5|2[.]0)\s*[Xx×倍](?!\w)")
BV = re.compile(r"(?<![A-Za-z0-9])[B8][Vv][1Il][A-Za-z0-9]{9}(?![A-Za-z0-9])")
CLOCK = re.compile(r"(?:(上午|下午)\s*)?([01]?\d|2[0-3])[:;]([0-5]\d)")
PART = re.compile(r"(?:合集\s*\d+\s*/\s*\d+|\bP\s*\d+\b|(?<!\d)\d{1,2}-\d{1,2}(?!\d))", re.I)
DATE = re.compile(r"20\d{2}\s*[年./-]\s*\d{1,2}\s*[月./-]\s*\d{1,2}")
VIEWS = re.compile(r"(?:播放(?:量)?\s*[:：]?\s*([\d.]+\s*[万亿]?)|([\d.]+\s*[万亿]?)\s*(?:播放|次观看))")


def parse_reading(text, candidate_totals=None, current_total=None):
    if candidate_totals is not None:
        normalized = text.translate(str.maketrans("①②③④⑤⑥⑦⑧⑨Ｏｏ／｜；．·－", "123456789Oo/|;.·-")).replace("：", ":")
        normalized = re.sub(r"(?<=\d)[Oo](?=\d|[:;.·\-/| 7])|(?<=[/:;.·\- 7])[Oo](?=\d)", "0", normalized)
        normalized = re.sub(r"^四(?=\d)", "", normalized)
        if re.search(r"[\u4e00-\u9fff]", normalized):
            return None
        # A player readout is a short, mostly numeric OCR box. Candidate totals
        # prevent clocks and unrelated counters from becoming player evidence.
        if (len(normalized) > 24 or "上午" in normalized or "下午" in normalized
                or not re.search(r"[:;.·\-/| ]", normalized)):
            return None
        digits = "".join(re.findall(r"\d", normalized))
        explicit_slash = bool(re.search(r"[/|]", normalized))
        if explicit_slash and re.search(r"[:;.·-]\d[/|]", normalized):
            return None
        possibilities = []
        expected = set(round(x) for x in candidate_totals if x is not None)
        if current_total is not None:
            expected.add(round(current_total))
        for total in expected:
            if not 5 <= total <= 4 * 3600:
                continue
            for observed_total in range(max(5, total - 1), total + 2):
                minute, second = divmod(observed_total, 60)
                for tail in {f"{minute:02d}{second:02d}", f"{minute}{second:02d}"}:
                    if not digits.endswith(tail):
                        continue
                    prefix = digits[:-len(tail)]
                    for separator in ("", "7", "1"):
                        if separator and (explicit_slash or len(prefix) < 5):
                            continue
                        if separator and not prefix.endswith(separator):
                            continue
                        current = prefix[:-1] if separator else prefix
                        if not 3 <= len(current) <= 5:
                            continue
                        m, s = int(current[:-2]), int(current[-2:])
                        pos = m * 60 + s
                        if s >= 60 or pos > observed_total + 5:
                            continue
                        score = (3 if explicit_slash else 0) + (4 if separator else 0)
                        score += 4 if len(tail) == 4 else 0
                        score += (2 if observed_total == total else 0) + (2 if current_total == observed_total else 0)
                        score -= abs(observed_total - total)
                        possibilities.append((score, -abs(observed_total - total), pos, observed_total))
        if not possibilities:
            partial = TRUNCATED_READING.fullmatch(normalized.strip())
            if partial:
                minute, second, total_minute = map(int, partial.groups())
                position = minute * 60 + second
                matching = {total for total in expected if total // 60 == total_minute}
                if second < 60 and (len(matching) == 1 or
                                    (current_total is not None and round(current_total) in matching)):
                    total = round(current_total) if current_total is not None and round(current_total) in matching else matching.pop()
                    if position <= total + 5:
                        return {"position": position, "total": total, "raw": text,
                                "truncated_total": True}
            return None
        _, _, position, total = max(possibilities)
        return {"position": position, "total": total, "raw": text}
    match = READING.search(text.replace(" ", ""))
    if not match:
        return None
    a, b, c, d = map(int, match.groups())
    if b >= 60 or d >= 60:
        return None
    return {"position": a * 60 + b, "total": c * 60 + d, "raw": match.group()}


def normalize_bv(text):
    match = BV.search(text)
    if not match:
        return None
    value = match.group()
    value = "BV1" + value[3:].replace("I", "1").replace("l", "1")
    return value if len(value) == 12 and all(c in BASE58 for c in value[3:]) else None


def parse_clock(text):
    match = CLOCK.search(text)
    if not match:
        return None
    period, hour, minute = match.groups()
    hour, minute = int(hour), int(minute)
    if period == "下午" and hour < 12:
        hour += 12
    if period == "上午" and hour == 12:
        hour = 0
    return hour * 60 + minute


def edit_distance(a, b):
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(current[-1] + 1, previous[j] + 1,
                               previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def normalize_title(text):
    return "".join(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]+", text)).lower()


def title_score(text, title):
    a, b = normalize_title(text), normalize_title(title)
    if len(a) < 5 or len(b) < 5:
        return 0.0
    if a[:8] in b or b[:8] in a:
        return 0.85
    return SequenceMatcher(None, a[:35], b[:35]).ratio()


def uploader_score(text, uploader):
    a, b = normalize_title(text), normalize_title(uploader)
    if len(a) < 2 or len(b) < 2:
        return 0.0
    if b in a or a in b:
        return 1.0
    return SequenceMatcher(None, a[:30], b[:30]).ratio()


def parse_items(items, candidate_totals=None):
    strings = [item["text"].strip() for item in items if item.get("text")]
    joined = []
    for left in items:
        if not re.search(r"\d{1,3}[:;]\d{2}\s*[/7|]\s*\d", left.get("text", "")):
            continue
        for right in items:
            gap = right.get("x", -1) - left.get("x", 0) - left.get("w", 0)
            if right is not left and 0 <= gap <= .025 and abs(right.get("y", -1) - left.get("y", 0)) <= .018 \
                    and re.fullmatch(r"[:;：]?\d{1,2}", right.get("text", "").strip()):
                joined.append(left["text"].strip() + right["text"].strip())
    reading_raw_candidates = list(dict.fromkeys(text for text in joined + strings
        if re.search(r"\d{1,3}[:;]\d{2}\s*[/7|]\s*\d", text)))
    parsed = [r for text in joined + strings if (r := parse_reading(text, candidate_totals))]
    if any(not r.get("truncated_total") for r in parsed):
        parsed = [r for r in parsed if not r.get("truncated_total")]
    readings = list({(r["position"], r["total"]): r for r in parsed}.values())
    speed_hits = [(float(m.group(1)), item) for item in items for m in SPEED.finditer(item.get("text", ""))]
    speeds = [speed for speed, _ in speed_hits]
    bvs = [value for text in strings if (value := normalize_bv(text))]
    clocks = [parse_clock(item["text"]) for item in items
              if item.get("y", 1) < 0.25 and parse_clock(item.get("text", "")) is not None]
    parts = [m.group() for text in strings for m in PART.finditer(text)]
    dates = [m.group() for text in strings for m in DATE.finditer(text)]
    views = [m.group(1) or m.group(2) for text in strings for m in VIEWS.finditer(text)]
    # Bilibili detail pages put views before the date on one OCR line.
    # Example from the source screenshots: "口2.6万 日3 2023年8月13日".
    for text in strings:
        if DATE.search(text) and "日" in text:
            prefix = text.split("日", 1)[0]
            numbers = re.findall(r"\d+(?:\.\d+)?\s*[万亿]?", prefix)
            if numbers:
                views.append(numbers[-1].strip())
    speed_menu = len(set(speeds)) >= 3
    selected = speed_hits[0] if len(set(speeds)) == 1 else None
    speed = selected[0] if selected and not speed_menu else None
    speed_cue = None
    if selected and not speed_menu:
        box = selected[1]
        x, y = box.get("x", -1), box.get("y", -1)
        if .38 <= x <= .62 and .32 <= y <= .65:
            speed_cue = "central_confirmation"
        elif x >= .70 and y >= .74:
            speed_cue = "player_button"
    if speed_cue is None:
        speed = None
    if readings:
        reading_boxes = [item for item in items if parse_reading(item.get("text", ""), candidate_totals)]
        page = "竖屏详情页" if any(box.get("y", 1) < .42 for box in reading_boxes) else "横屏播放器"
    elif any("搜索" in s for s in strings):
        page = "搜索结果页"
    elif any("关注" in s or "简介" in s or "播放量" in s for s in strings):
        page = "竖屏详情页"
    else:
        page = "其他"
    reading_candidate = bool(reading_raw_candidates)
    return {"readings": readings, "speed": speed, "speed_cue": speed_cue,
            "speed_menu": speed_menu, "bvs": bvs, "clock": clocks[0] if clocks else None,
            "parts": parts, "dates": dates, "views": views, "page": page, "texts": strings,
            "reading_candidate": reading_candidate,
            "reading_raw_candidates": reading_raw_candidates}
