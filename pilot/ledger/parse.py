"""Deterministic OCR parsing and closed-set matching primitives."""
from __future__ import annotations

from difflib import SequenceMatcher
import re

BASE58 = set("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")
READING = re.compile(r"(?<!\d)(\d{1,3})[:;](\d{2})\s*[/7]\s*(\d{1,3})[:;](\d{2})(?!\d)")
SPEED = re.compile(r"(?<!\d)(0[.]5|0[.]75|1[.]0|1[.]25|1[.]5|2[.]0)\s*[Xx倍](?!\w)")
BV = re.compile(r"(?<![A-Za-z0-9])[B8][Vv][1Il][A-Za-z0-9]{9}(?![A-Za-z0-9])")
CLOCK = re.compile(r"(?:(上午|下午)\s*)?([01]?\d|2[0-3])[:;]([0-5]\d)")
PART = re.compile(r"(?:合集\s*\d+\s*/\s*\d+|\bP\s*\d+\b|(?<!\d)\d{1,2}-\d{1,2}(?!\d))", re.I)
DATE = re.compile(r"20\d{2}\s*[年./-]\s*\d{1,2}\s*[月./-]\s*\d{1,2}")
VIEWS = re.compile(r"(?:播放(?:量)?\s*[:：]?\s*([\d.]+\s*[万亿]?)|([\d.]+\s*[万亿]?)\s*(?:播放|次观看))")


def parse_reading(text):
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


def parse_items(items):
    strings = [item["text"].strip() for item in items if item.get("text")]
    readings = [r for text in strings if (r := parse_reading(text))]
    speeds = [float(m.group(1)) for text in strings for m in SPEED.finditer(text)]
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
    speed = speeds[0] if len(set(speeds)) == 1 else None  # Ignore an open speed menu.
    if readings:
        reading_boxes = [item for item in items if parse_reading(item.get("text", ""))]
        page = "竖屏详情页" if any(box.get("y", 1) < .42 for box in reading_boxes) else "横屏播放器"
    elif any("搜索" in s for s in strings):
        page = "搜索结果页"
    elif any("关注" in s or "简介" in s or "播放量" in s for s in strings):
        page = "竖屏详情页"
    else:
        page = "其他"
    return {"readings": readings, "speed": speed, "bvs": bvs, "clock": clocks[0] if clocks else None,
            "parts": parts, "dates": dates, "views": views, "page": page, "texts": strings}
