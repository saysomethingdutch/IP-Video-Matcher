"""Frames → segments on the video's clock, traffic intervals, and the lines AC needs (WO-102 §2.4, §3).

Content classes are rules_tsxk.yaml timeline.content plus "traffic": a frame that is nothing but a drive page
or a code (its neighbours do not tell what lies underneath). Boundaries between two settled classes are moved
to the first second that looks more like the next segment than the last one (1 s probes, local only).
"""
from __future__ import annotations

import statistics
from typing import Callable

from .cloud import mmss

PHRASE = {"tsxk": "为《吞噬星空》动画画面", "tsxk_other": "为《吞噬星空》{form}内容", "creator": "为UP主自制画面",
          "unrelated": "为无关内容", "traffic": "为引流画面", "blank": "为黑屏或无画面", "unknown": "画面未能判定（请核对）"}
SETTLED = {"tsxk", "tsxk_other", "creator", "unrelated", "traffic", "blank"}


class Clock:
    """Recording seconds → seconds on the B站 video's own clock. Offsets come from the web ledger's anchors or
    the player's hover readings (median); without either, playback is taken to start at the span start."""

    def __init__(self, start: float, readings=(), anchors=(), duration: float | None = None):
        points = [(float(r), float(v)) for r, v in anchors] + [(float(t), float(r[0])) for t, r in readings]
        self.flags = []
        if points:
            offsets = [r - v for r, v in points]
            self.offset = statistics.median(offsets)
            self.source = "台账的播放器读数" if anchors else "画面里的播放器读数"
            if max(offsets) - min(offsets) > 3:
                self.flags.append("播放器读数前后不连贯（可能有跳转或暂停），时间段可能不准")
        else:
            self.offset = start
            self.source = "按开始播放点推算（未经播放器读数核对）"
        totals = [r[1] for _, r in readings]
        self.duration = duration or (max(totals) if totals else None)

    @classmethod
    def recording(cls) -> "Clock":
        """The evidence recording's own clock: what the guidance examples use (12429 "02:31-11:47" ends at the
        recording's length, 11:47; the part itself is 10:50), and what the owner can check in the file."""
        c = cls(0.0)
        c.source = "取证录屏时间"
        return c

    def __call__(self, t: float) -> float:
        return max(0.0, t - self.offset)

    def to_recording(self, v: float) -> float:
        return v + self.offset


def fill_from_neighbours(frames: list[dict]) -> None:
    """A traffic frame with no content of its own takes its neighbours' content when both sides agree (a code
    laid over the series); otherwise it is a traffic frame."""
    settled = [i for i, f in enumerate(frames) if f["content"] not in (None, "unknown")]
    for i, f in enumerate(frames):
        if f["content"] is not None:
            continue
        before = next((frames[j] for j in reversed(settled) if j < i), None)
        after = next((frames[j] for j in settled if j > i), None)
        if before and after and (before["content"], before.get("form")) == (after["content"], after.get("form")) \
                and before["content"] != "traffic":
            f.update(content=before["content"], form=before.get("form", ""), source=f["source"] + "+前后帧推定")
        else:
            f["content"] = "traffic" if f.get("traffic") else "unknown"


def _series(f: dict) -> bool:
    return f["content"] == "tsxk" or (f["content"] == "tsxk_other" and f.get("form") == "动画剧场版")


def lone_unrelated(frames: list[dict]) -> list[dict]:
    """D-072: one unrelated frame with 吞噬星空 animation right before and right after it is undetermined, not
    unrelated content (a single frame does not make a 偷渡). Rewrites those frames and returns them."""
    lone = [b for a, b, c in zip(frames, frames[1:], frames[2:]) if b["content"] == "unrelated" and _series(a) and _series(c)]
    for f in lone:
        f.update(content="unknown", form="", source=f["source"] + "+单帧无关改待定")
    return lone


def unrelated_block(frames: list[dict]) -> bool:
    """D-072: unrelated content that can make a 偷渡 — two or more frames in a row, or any frame before the first or
    after the last frame of 吞噬星空 animation (片头、片尾)."""
    series = [i for i, f in enumerate(frames) if _series(f)]
    return any(f["content"] == "unrelated" and (not series or i < series[0] or i > series[-1] or
                                                (i + 1 < len(frames) and frames[i + 1]["content"] == "unrelated"))
               for i, f in enumerate(frames))


def _boundary(a: dict, b: dict, probe: Callable[[float], float | None] | None) -> float:
    """First recording second in (a, b] that looks more like b than a; b's time when probing is impossible."""
    if probe is None or a["content"] not in SETTLED or b["content"] not in SETTLED or b["t"] - a["t"] <= 1.5:
        return b["t"]
    t = a["t"] + 1
    while t < b["t"]:
        side = probe(t)                    # < 0: looks like a, > 0: looks like b
        if side is not None and side > 0:
            return t
        t += 1
    return b["t"]


def segments(frames: list[dict], clock: Clock, stop: float, probe=None, start: float | None = None) -> list[dict]:
    """[{start, end, content, form, frames, sources}] on `clock`; `stop` = where playback ends (recording s).
    probe(a, b) gives a function t → (> 0 when the picture at t looks more like frame b than frame a)."""
    runs = []
    for f in frames:
        key = (f["content"], f.get("form", ""))
        if runs and runs[-1]["key"] == key:
            runs[-1]["frames"].append(f)
        else:
            runs.append({"key": key, "frames": [f]})
    first = runs[0]["frames"][0]["t"]
    starts = [min(first, start) if start is not None else first]           # content starts when playback does
    for p, r in zip(runs, runs[1:]):
        a, b = p["frames"][-1], r["frames"][0]
        starts.append(_boundary(a, b, probe(a, b) if probe else None))
    out = []
    for k, r in enumerate(runs):
        s = clock(starts[k])
        e = clock(starts[k + 1]) - 1 if k + 1 < len(runs) else clock(stop)
        out.append({"start": round(s), "end": max(round(s), round(e)), "content": r["key"][0], "form": r["key"][1],
                    "frames": len(r["frames"]), "sources": sorted({f["source"] for f in r["frames"]})})
    return out


def traffic_intervals(frames: list[dict], clock: Clock, check=None, step: float = 10) -> list[dict]:
    """Runs of frames that carry traffic, widened second by second while `check(t)` still sees it."""
    runs, cur = [], None
    for f in frames:
        if f.get("traffic"):
            if cur and f["t"] - cur["last"] <= step * 1.5:
                cur["last"] = f["t"]
                cur["platforms"] |= set(f["traffic"])
                cur["kinds"] |= set(f.get("kinds") or ())
                cur["evidence"] += [e for e in f.get("traffic_evidence", []) if e not in cur["evidence"]]
            else:
                cur = {"first": f["t"], "last": f["t"], "platforms": set(f["traffic"]), "kinds": set(f.get("kinds") or ()),
                       "evidence": list(f.get("traffic_evidence", []))}
                runs.append(cur)
    out = []
    for r in runs:
        first, last = r["first"], r["last"]
        if check:
            while first - 1 >= 0 and first - r["first"] > -step and check(first - 1):
                first -= 1
            while last - r["last"] < step and check(last + 1):
                last += 1
        out.append({"start": round(clock(first)), "end": round(clock(last)), "platforms": sorted(r["platforms"]),
                    "kinds": sorted(r["kinds"]) or ["引流信息"], "evidence": r["evidence"][:4]})
    return out


def ac_lines(segs: list[dict], traffic: list[dict]) -> dict:
    """The time lines that go into AC【其他】 verbatim (the model never writes numbers)."""
    def span(s, e):
        return mmss(s) if s == e else f"{mmss(s)}-{mmss(e)}"

    content = "；".join(f"{span(s['start'], s['end'])}{PHRASE[s['content']].format(form=s['form'] or '其他形式')}"
                        for s in segs if not (s["content"] == "blank" and s["end"] - s["start"] < 10))
    lines = [f"{span(t['start'], t['end'])}出现{'、'.join(t['kinds'])}" for t in traffic]
    return {"content": content, "traffic": lines,
            "unrelated": [f"{span(s['start'], s['end'])}为无关内容" for s in segs if s["content"] == "unrelated"]}


def summary(frames: list[dict], segs: list[dict]) -> dict:
    seconds = {}
    for s in segs:
        seconds[s["content"]] = seconds.get(s["content"], 0) + s["end"] - s["start"] + 1
    return {"seconds": seconds, "forms": sorted({s["form"] for s in segs if s["content"] == "tsxk_other" and s["form"]}),
            "has_tsxk": any(f["content"] == "tsxk" for f in frames),
            "has_film": any(f["content"] == "tsxk_other" and f.get("form") == "动画剧场版" for f in frames),
            "traffic_platforms": sorted({p for f in frames for p in f.get("traffic") or ()}),
            "unknown_frames": sum(1 for f in frames if f["content"] == "unknown"),
            "face_frames": sum(1 for f in frames if f.get("face")), "text_frames": sum(1 for f in frames if f.get("text")),
            "creator_frames": sum(1 for f in frames if f["content"] == "creator"),
            "ai_note_frames": sum(1 for f in frames if f.get("ai_note")),
            "film_watermark": any((f.get("local") or {}).get("form") == "动画剧场版" for f in frames),   # WO-102-R2 A5
            "novel_frames": sum(1 for f in frames if f["content"] == "tsxk_other" and f.get("form") == "小说"),
            "unrelated_block": unrelated_block(frames),                                                 # D-072
            "lone_unrelated": sum(1 for f in frames if f["source"].endswith("单帧无关改待定")),
            "disputed_frames": sum(1 for f in frames if ((f.get("cloud") or {}).get("arbitration"))),     # WO-102-R4 §1
            "disputed_pending": sum(1 for f in frames if ((f.get("cloud") or {}).get("arbitration") or {}).get("verdict", "").startswith("复核后")),
            "local_frames": sum(1 for f in frames if f["source"].startswith("本地")),
            "cloud_frames": sum(1 for f in frames if f["source"].startswith("云端"))}
