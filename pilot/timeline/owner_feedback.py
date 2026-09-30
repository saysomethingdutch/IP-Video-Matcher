"""D-077: repair an existing timeline without opening images or calling a model.

Keep the previously measured boundaries. Reclassify the cached frames, split runs
only where a label changes, and rebuild summaries; no boundary probe is rerun.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import re

from . import segments as S

END_TEXT = re.compile(r"重[播橎]|相关推荐|取消连[播櫹]|自动连播")
END_WHY = re.compile(r"结束[页画]|播放结束|推荐|App\s*界面|播放器|连播", re.I)


def votes(frame: dict) -> tuple[bool, bool]:
    """(any series vote in any round, both providers explicitly unrelated).

    A later arbitration cannot erase an earlier positive vote. Copied scene or
    duplicate answers are usable only when the two original providers exist.
    """
    cloud = frame.get("cloud") or {}
    answers = cloud.get("answers") or {}
    series = any(a and (a.get("content") == "tsxk" or a.get("form") == "动画剧场版")
                 for a in answers.values())
    arb = cloud.get("arbitration") or {}
    series |= any(x is True for x in (arb.get("votes") or {}).values())
    series |= "tsxk" in (arb.get("before") or {}).values()
    both = all((answers.get(k) or {}).get("content") == "unrelated" for k in ("zhipu", "deepseek"))
    # In a second round, both explicit 'not series' can corroborate a prior
    # unrelated vote, but any earlier series vote still vetoes adoption.
    both |= all((arb.get("votes") or {}).get(k) is False for k in ("zhipu", "deepseek")) and any(
        (a or {}).get("content") == "unrelated" for a in answers.values())
    return series, bool(both and not series)


def repair(timeline: dict) -> dict:
    out = deepcopy(timeline)
    if out.get("owner_feedback_version") == "D-077-1":
        return out
    frames = [f for f in out.get("frames", []) if not f.get("ignored")]
    original = deepcopy(frames)
    old_segs = timeline.get("segments") or []
    for f in frames:
        if f.get("content") == "tsxk_other" and f.get("form") == "动画剧场版":
            f.update(content="tsxk", form="", source=f.get("source", "") + "+D077")
    last = max((f["t"] for f in frames if f.get("content") == "tsxk"), default=None)
    stop = old_segs[-1]["end"] if old_segs else (out.get("content_span") or out["span"])[1]
    changes = []
    for f in frames:
        if f.get("content") == "tsxk":
            continue
        why = " ".join(str(a.get("why") or "") for a in ((f.get("cloud") or {}).get("answers") or {}).values() if a)
        after = last is not None and f["t"] > last
        tail = next((s for s in old_segs if s["start"] <= f["t"] < s["end"] + 1), {})
        short_tail = after and f.get("content") in ("unrelated", "unknown") and stop - tail.get("start", f["t"]) < 10
        exception = bool(f.get("traffic")) or f.get("content") == "creator"
        if END_TEXT.search(" ".join(f.get("ocr") or [])) or (after and END_WHY.search(why)) or (short_tail and not exception):
            f.update(ignored="结束页或连播", content=None, form="", traffic=[], kinds=[], face=False, text=False)
            changes.append({"t": f["t"], "to": "ignored", "reason": "结束页或连播"})
    # Adjacency includes ignored frames as breaks. Never collapse the list before
    # deciding whether two unrelated pictures are consecutive.
    eligible = [not f.get("ignored") and f.get("content") == "unrelated" and votes(f)[1] for f in frames]
    for i, f in enumerate(frames):
        if f.get("ignored") or f.get("content") != "unrelated":
            continue
        adjacent = (i > 0 and eligible[i-1]) or (i+1 < len(frames) and eligible[i+1])
        if not eligible[i] or not adjacent:
            f.update(content="unknown", form="", source=f.get("source", "") + "+D077待定")
            changes.append({"t": f["t"], "to": "unknown", "reason": "未获两家全轮一致或非连续两帧"})
    runs = []
    for i, f in enumerate(frames):
        if f.get("ignored"):
            continue
        key = (f.get("content"), f.get("form", ""))
        if runs and runs[-1]["key"] == key and i == runs[-1]["last"] + 1:
            runs[-1]["frames"].append(f)
            runs[-1]["last"] = i
        else:
            old = next((s for s in old_segs if s["start"] <= f["t"] < s["end"] + 1), None)
            first_old = old and not any(old["start"] <= p["t"] < f["t"] for p in original)
            runs.append({"key": key, "frames": [f], "first": i, "last": i,
                         "start": old["start"] if first_old else round(f["t"])})
    segs = []
    for i, run in enumerate(runs):
        end = runs[i+1]["start"] - 1 if i+1 < len(runs) else stop
        # An ignored ending cuts off at its old measured boundary.
        next_ignored = next((f for f in frames[run["last"]+1:] if f.get("ignored")), None)
        if next_ignored:
            old = next((s for s in old_segs if s["start"] <= next_ignored["t"] < s["end"] + 1), None)
            end = min(end, (old["start"] if old else round(next_ignored["t"])) - 1)
        segs.append({"start": run["start"], "end": max(run["start"], end), "content": run["key"][0],
                     "form": run["key"][1], "frames": len(run["frames"]),
                     "sources": sorted({f.get("source", "缓存") for f in run["frames"]})})
    inside = [f for f in frames if not f.get("ignored")]
    # Traffic facts have already been measured locally; remove spans wholly
    # inside the ending instead of inventing new boundaries.
    traffic = [t for t in out.get("traffic", []) if any(s["start"] <= t["end"] and s["end"] >= t["start"] for s in segs)]
    out.update(segments=segs, traffic=traffic, ac=S.ac_lines(segs, traffic), summary=S.summary(inside, segs),
               owner_feedback_version="D-077-1", owner_feedback_changes=changes,
               frames_ignored=dict(Counter(f["ignored"] for f in out["frames"] if f.get("ignored"))))
    # Old frame-level disputes describe labels that have now been corrected.
    flags = [f for f in out.get("flags", []) if not re.search(r"单独一帧|画面未能判定|剧场版|争议帧|两家.*不一致", f["reason"])]
    for s in segs:
        if s["content"] == "unknown":
            flags.append({"level": "yellow", "tier": "technical" if s["end"]-s["start"]+1 < 30 else "focus",
                          "reason": f"画面仍待定：{s['start']}–{s['end']} 秒（缓存复算）"})
    out["flags"] = flags
    return out
