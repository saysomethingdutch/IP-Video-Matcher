"""【使用的比例】 written by code from the RowDigest (WO-004c §3.1); the model never writes a number here.

Episodes: content's lyric-free dialogue evidence; without it the title/logo episode (yellow); then the
plaintiff's claim, written as "原告主张的第X集" and never turned into a percentage (WO-004c-R1 §2: an
unchecked episode must not look exact). Episode length: the local copies real_test/qsjx_epNN.ts measured
with ffprobe; an episode missing locally counts as the mean of the local ones ("每集平均长约X分钟").
Quoted footage: the video duration. Above 10% the short template is used (R1 §3, like human sample 851).
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from statistics import mean

from pilot.content.evidence import parse_claim

MAX_SHOWN_PCT = 10.0        # 要求第 4 条：超过 10% 不写数值，只写"占原作品比例较少"


def flag(level: str, reason: str) -> dict:
    return {"level": level, "reason": reason}


def episode_seconds(episodes_dir: Path, cache: Path, pattern: str = r"qsjx_ep(\d+)\.ts$") -> dict[int, float]:
    """{episode: seconds} of the local copies, cached after the first ffprobe pass."""
    if cache.exists():
        return {int(k): v for k, v in json.loads(cache.read_text(encoding="utf-8")).items()}
    out = {}
    filename_pattern = re.compile(pattern)
    for path in sorted(episodes_dir.iterdir()):
        if not path.is_file():
            continue
        m = filename_pattern.search(path.name)
        if not m:
            continue
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                           capture_output=True, text=True, check=True)
        out[int(m.group(1))] = round(float(r.stdout.strip()), 1)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


def corresponding_episodes(d: dict) -> tuple[list[int], str, list[dict]]:
    """(episodes, where they come from, flags)."""
    ev = d["episodes"]["evidenced"]
    firm = sorted({e["episode"] for e in ev if not e.get("suspected")})
    if firm:
        return firm, "台词证据", []
    loose = sorted({e["episode"] for e in ev})
    if loose:
        return loose, "疑似台词证据", [flag("yellow", "比例段：集数只有疑似证据（前情回顾），请核对")]
    te = d["episodes"].get("title_episode")
    if te and te.get("episodes"):
        return [te["episodes"][0]], "标题/台标", [flag("yellow", "比例段：没有台词证据，集数取标题/台标")]
    claim = parse_claim(d["plaintiff"]["part_claimed"])
    if claim:
        return sorted(claim), "原告主张", [flag("yellow", "比例段：集数只有原告主张，未核实，不算百分比")]
    return [], "无", [flag("red", "比例段：对应集数无法确定")]


def episode_label(eps: list[int]) -> str:
    """[1..38] -> 第1-38集, [3, 4] -> 第3、4集, [1, 2, 3, 8] -> 第1-3、8集."""
    runs: list[list[int]] = []
    for e in sorted(eps):
        if runs and e == runs[-1][-1] + 1:
            runs[-1].append(e)
        else:
            runs.append([e])
    return "第" + "、".join(f"{r[0]}-{r[-1]}" if len(r) >= 3 else "、".join(map(str, r)) for r in runs) + "集"


def minutes_seconds(seconds: float) -> str:
    m, s = divmod(int(round(seconds)), 60)
    return f"{m}分{s}秒" if m and s else f"{m}分钟" if m else f"{s}秒"


def pct_text(pct: float) -> str:
    return f"{max(pct, 0.01):.2f}".rstrip("0").rstrip(".")


def proportion(d: dict, ep_secs: dict[int, float], source_enabled: bool = True) -> dict:
    """{"text", "numbers" (every number the paragraph may contain), "basis", "flags"}."""
    if not source_enabled:
        return {"text": "本案未配置原片对应集数和时长，无法核算具体引用比例，请人工核对。", "numbers": set(),
                "basis": {"source": "原片未配置"}, "flags": [flag("yellow", "比例段：原片未配置，引用比例待人工核对")]}
    eps, source, flags = corresponding_episodes(d)
    if not eps:
        return {"text": "", "numbers": set(), "basis": {"source": source}, "flags": flags}
    avg_local = mean(ep_secs.values())
    missing = [e for e in eps if e not in ep_secs]
    total = sum(ep_secs.get(e, avg_local) for e in eps)
    minutes = round(total / len(eps) / 60)
    label = ("原告主张的" if source == "原告主张" else "") + episode_label(eps)
    if missing:
        length = f"每集平均长约{minutes}分钟"
        flags.append(flag("yellow", f"比例段：第{'、'.join(map(str, missing))}集本地缺片，按本地各集平均时长计"))
    else:
        length = f"约长{minutes}分钟" if len(eps) == 1 else f"每集长约{minutes}分钟"
    numbers = set(eps) | {minutes}
    video = d["ledger"]["video_duration"]["value"]
    basis = {"episodes": eps, "source": source, "episode_minutes": minutes, "missing_locally": missing,
             "total_seconds": round(total, 1), "video_seconds": video}
    short = f"对应剧集为{label}，{length}，占原作品比例较少，足以构成适当引用。"
    if not isinstance(video, (int, float)) or video <= 0 or source == "原告主张":
        return {"text": short, "numbers": numbers, "basis": basis, "flags": flags}
    pct = video / total * 100
    basis["pct"] = round(pct, 3)
    if pct > MAX_SHOWN_PCT:                    # no "仅18分45秒" for a reaction that covers four tenths of an episode
        return {"text": short, "numbers": numbers, "basis": basis, "flags": flags}
    m, s = divmod(int(round(video)), 60)
    numbers |= {m, s, pct_text(pct)}
    text = (f"对应剧集为{label}，{length}，被诉视频引用原作品的画面仅{minutes_seconds(video)}，"
            f"占原作品比例较少，为{pct_text(pct)}%，足以构成适当引用。")
    return {"text": text, "numbers": numbers, "basis": basis, "flags": flags}
