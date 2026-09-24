"""Caption events from the recording OCR cache (WO-003a §3.1).

Per ledger row: keep frames inside the session that show the landscape player, drop player UI,
scrolling danmaku and static overlays (watermarks), merge the remaining lines of adjacent seconds
into events and place them on the suspect video's own timeline through the row's playback_map.
Coordinates are the recording frame's, normalized, origin top-left (as in the ledger OCR cache).
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from pilot.textindex.text import lcs_len, normalize, similarity

FRAME_RE = re.compile(r"frame_(\d+)\.")

# frame layout
# pages other than the playing video: detail page, search, share sheet, uploader space, dialogs
PORTRAIT_TEXT = re.compile(r"^(简介|搜[索素]|立即播放|取消|保存|截长屏|动态|复制链接|分享到)$|^评论\s*\d|^视频\s*\d|投稿了视频")
ROTATED_H = 0.45              # a text line this tall is a rotated page, not the landscape player
PORTRAIT_MIN_ITEMS = 8        # a portrait page is many lines of text ...
PORTRAIT_MAX_WIDTH = 0.45     # ... in a narrow column (the phone stands upright under the camera)
MAX_CONTENT_ITEMS = 20        # more text than this is a recommendation grid, comment list or end screen

# player UI
READING = re.compile(r"\d{1,2}\s*[:：;.]\s*\d{2}\s*[/7]\s*\d{1,2}\s*[:：;.]?\s*\d{2}")
SPEED = re.compile(r"^\d(\.\d+)?\s*[xX×]$")
CLOCK = re.compile(r"^(上午|下午)?\s*\d{1,2}\s*[:：]\s*\d{2}$")
UI_PHRASES = ("友善的弹", "发个友善", "见证当下", "正在看", "首音乐")
MUSIC_TAG = re.compile(r"^[J♪♫]\s+\S")   # bilibili's "music detected" chip, the note read as "J"
CORNER_LABEL_CHARS = 4        # short text in the bottom corners is a player control (倍速, 自动 read as 目动, ...)
UI_LABELS = ("弹幕", "倍速", "选集", "自动", "关注", "投屏", "高清", "超清", "清晰度", "发送", "字幕", "wifi", "wif")
HAN = re.compile(r"[\u3400-\u9fff]")
LATIN_CAPTION = re.compile(r"[A-Za-z]{2,}\s+[A-Za-z]{2,}")   # a foreign-language subtitle: two words or more
TITLE_BAR_MAX_Y = 0.35        # centre y of the landscape title bar is ~0.19 in these recordings

# tracking
DANMAKU_DX = 0.03             # same text, same row, moved at least this much between frames -> scrolling danmaku
ROW_DY = 0.015
STATIC_SHARE = 0.3            # same position in >= 30% of the session's player frames -> watermark / static overlay
STATIC_MIN_FRAMES = 5
JOIN_GAP = 0.03               # items on one row closer than this are one line
EVENT_GAP_S = 2.0             # a line may be missed in one frame (1 fps) and still continue its event
EVENT_SIM = 0.7


@dataclass
class Line:
    t: float
    text: str
    norm: str
    conf: float
    box: tuple[float, float, float, float]   # x, y, w, h

    @property
    def cx(self) -> float:
        return self.box[0] + self.box[2] / 2

    @property
    def cy(self) -> float:
        return self.box[1] + self.box[3] / 2


@dataclass
class Event:
    lines: list[Line] = field(default_factory=list)

    @property
    def rec_start(self) -> float:
        return self.lines[0].t

    @property
    def rec_end(self) -> float:
        return self.lines[-1].t

    @property
    def best(self) -> Line:
        """Most frequent spelling, then the most confident, then the longest."""
        counts = Counter(l.norm for l in self.lines)
        return max(self.lines, key=lambda l: (counts[l.norm], l.conf, len(l.norm)))

    @property
    def conf(self) -> float:
        return max(l.conf for l in self.lines)


def load_ocr(case_dir: Path, video: str) -> dict[int, list[dict]]:
    paths = sorted((case_dir / "ocr_cache").glob(f"{video}-*.jsonl"), key=lambda p: p.stat().st_mtime)
    if not paths:
        raise FileNotFoundError(f"no OCR cache for {video} in {case_dir / 'ocr_cache'}")
    frames = {}
    for raw in paths[-1].read_text(encoding="utf-8").splitlines():
        d = json.loads(raw)
        frames[int(FRAME_RE.search(d["frame"]).group(1)) - 1] = d.get("items", [])  # frame_000001 is 0 s
    return frames


def is_player_frame(items: list[dict]) -> bool:
    """Landscape player showing the video (UI shown or hidden), not a portrait page or a list."""
    if not items:
        return False
    if any(PORTRAIT_TEXT.search(o["text"].strip()) or o["h"] > ROTATED_H for o in items):
        return False
    left = min(o["x"] for o in items)
    right = max(o["x"] + o["w"] for o in items)
    if len(items) >= PORTRAIT_MIN_ITEMS and right - left <= PORTRAIT_MAX_WIDTH:
        return False
    return len(items) < MAX_CONTENT_ITEMS


def is_ui(item: dict, title_norm: str) -> bool:
    t = item["text"].strip()
    if READING.search(t) or SPEED.match(t) or CLOCK.match(t) or MUSIC_TAG.match(t) or any(p in t for p in UI_PHRASES):
        return True
    n = normalize(t)
    if len(n) <= 6 and any(w in n for w in UI_LABELS):
        return True                      # short labels only: a long caption may well say "关注"
    cx, cy = item["x"] + item["w"] / 2, item["y"] + item["h"] / 2
    if len(n) <= CORNER_LABEL_CHARS and cy > 0.6 and (cx < 0.22 or cx > 0.72):
        return True
    # the title bar (top of the player) shows a prefix of the title; titles often quote dialogue,
    # so this only applies up there
    if item["y"] + item["h"] / 2 >= TITLE_BAR_MAX_Y or len(n) < 4:
        return False
    return n in title_norm or (len(n) >= 6 and lcs_len(n, title_norm) >= 0.8 * len(n))


def is_watermark(norm: str, uploader_norm: str) -> bool:
    if "bilibili" in norm or "哔哩哔哩" in norm:
        return True
    return len(uploader_norm) >= 2 and len(norm) >= 2 and (uploader_norm in norm or similarity(norm, uploader_norm) >= 0.7)


def to_content(t: float, pmap: list[dict], speed: float | None) -> tuple[float | None, bool]:
    """Recording second -> suspect video second via the playback map; (value, estimated)."""
    plays = [s for s in pmap if s.get("event") == "play"]
    for s in plays:
        if s["video_start"] <= t <= s["video_end"]:
            span = s["video_end"] - s["video_start"]
            frac = (t - s["video_start"]) / span if span else 0.0
            return s["content_start"] + frac * (s["content_end"] - s["content_start"]), False
    if not plays:
        return None, True
    # outside the mapped playback: extrapolate from the nearest mapped point at the row's speed
    before = [s for s in plays if s["video_end"] <= t]
    after = [s for s in plays if s["video_start"] >= t]
    rate = speed or 1.0
    if before:
        s = max(before, key=lambda s: s["video_end"])
        return s["content_end"] + (t - s["video_end"]) * rate, True
    s = min(after, key=lambda s: s["video_start"])
    return max(0.0, s["content_start"] - (s["video_start"] - t) * rate), True


def _join_rows(items: list[dict]) -> list[dict]:
    """Merge items that sit on one row with a small gap (a caption split at a space)."""
    items = sorted(items, key=lambda o: (round(o["y"] + o["h"] / 2, 2), o["x"]))
    out: list[dict] = []
    for o in items:
        if out:
            p = out[-1]
            same_row = abs((p["y"] + p["h"] / 2) - (o["y"] + o["h"] / 2)) <= max(p["h"], o["h"]) / 2
            if same_row and 0 <= o["x"] - (p["x"] + p["w"]) <= JOIN_GAP:
                right = o["x"] + o["w"]
                top = min(p["y"], o["y"])
                bottom = max(p["y"] + p["h"], o["y"] + o["h"])
                out[-1] = {"text": f"{p['text']} {o['text']}", "conf": min(p["conf"], o["conf"]),
                           "x": p["x"], "y": top, "w": right - p["x"], "h": bottom - top}
                continue
        out.append(dict(o))
    return out


@dataclass
class RowFrames:
    player: list[int]                      # player frames in the session
    lines: dict[int, list[Line]]           # kept lines per frame
    dropped: Counter                       # reason -> item count


def row_lines(frames: dict[int, list[dict]], row: dict) -> RowFrames:
    """Player frames of the row's session and the lines left after dropping UI, danmaku and overlays."""
    title_norm = normalize(row["plaintiff"].get("title") or "") + normalize(row.get("observed_title") or "")
    uploader_norm = normalize(row["plaintiff"].get("uploader") or "")
    t0, t1 = int(row["video_start"]), int(row["video_end"])
    dropped: Counter = Counter()
    player = []
    for t in range(t0, t1 + 1):
        if is_player_frame(frames.get(t, [])):
            player.append(t)
        else:
            dropped["非横屏播放帧"] += 1
    raw: dict[int, list[dict]] = {}
    for t in player:
        keep = []
        for o in frames[t]:
            n = normalize(o["text"])
            if not HAN.search(n) and not LATIN_CAPTION.search(o["text"]):
                dropped["非文字碎片"] += 1       # digits / short Latin: counters, garbled watermarks, stray UI
                continue
            if is_ui(o, title_norm):
                dropped["UI"] += 1
            elif is_watermark(n, uploader_norm):
                dropped["水印"] += 1
            else:
                keep.append({**o, "norm": n})
        raw[t] = keep

    # scrolling danmaku: the same text on the same row at a different x in a neighbouring frame
    moving = set()
    for t in player:
        for i, o in enumerate(raw[t]):
            for dt in (-2, -1, 1, 2):
                for p in raw.get(t + dt, ()):
                    if abs(p["y"] - o["y"]) <= ROW_DY and abs(p["x"] - o["x"]) >= DANMAKU_DX \
                            and abs(p["w"] - o["w"]) <= 0.02 and similarity(p["norm"], o["norm"]) >= 0.8:
                        moving.add((t, i))
    # static overlays: similar text at the same place through a large share of the session
    # (captions change every few seconds, so they never qualify even when they share a position)
    min_static = max(STATIC_MIN_FRAMES, STATIC_SHARE * len(player))

    def static(o) -> bool:
        seen = 0
        for t2 in player:
            if any(abs(p["x"] - o["x"]) <= 0.02 and abs(p["y"] - o["y"]) <= 0.02
                   and similarity(p["norm"], o["norm"]) >= 0.5 for p in raw[t2]):
                seen += 1
                if seen >= min_static:
                    return True
        return False

    lines: dict[int, list[Line]] = {}
    for t in player:
        keep = []
        for i, o in enumerate(raw[t]):
            if (t, i) in moving:
                dropped["弹幕"] += 1
            elif static(o):
                dropped["水印"] += 1
            else:
                keep.append(o)
        lines[t] = [Line(float(t), o["text"].strip(), normalize(o["text"]), float(o["conf"]),
                         (o["x"], o["y"], o["w"], o["h"])) for o in _join_rows(keep)]
    return RowFrames(player, lines, dropped)


def merge_events(lines: dict[int, list[Line]]) -> list[Event]:
    """Lines of adjacent seconds with the same or nearly the same text at the same place -> one event."""
    events: list[Event] = []
    open_events: list[Event] = []
    for t in sorted(lines):
        open_events = [e for e in open_events if t - e.rec_end <= EVENT_GAP_S]
        taken = set()
        for line in sorted(lines[t], key=lambda l: l.cy):
            best, best_sim = None, 0.0
            for e in open_events:
                if id(e) in taken:
                    continue
                last = e.lines[-1]
                if abs(last.cy - line.cy) > 0.02 or abs(last.cx - line.cx) > 0.08:
                    continue
                s = similarity(last.norm, line.norm)
                if min(len(last.norm), len(line.norm)) >= 3 and (last.norm in line.norm or line.norm in last.norm):
                    s = max(s, 0.9)          # a line fading in or partly covered
                if s >= EVENT_SIM and s > best_sim:
                    best, best_sim = e, s
            if best is None:
                best = Event()
                events.append(best)
                open_events.append(best)
            best.lines.append(line)
            taken.add(id(best))
    return events
