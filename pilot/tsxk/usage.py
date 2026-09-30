"""Cloud calls per provider and kind: input, thinking and output tokens and time, to see the bottleneck (D-069).

    python -m pilot.tsxk usage cases/tsxk.yaml [--since 2026-09-29T18:00] [--until …] [--run "tsxk run"]

Reads workspace/<case>/usage/calls.jsonl, where every cloud request is appended as it ends (llm.CALL_LOG).
"""
from __future__ import annotations

import json
import statistics
from datetime import datetime
from pathlib import Path

KINDS = {"collage": "画面判断（2×2 拼图）", "arbitration": "争议帧复核", "classification": "分类与意见"}
PROVIDERS = {"zhipu": "GLM", "deepseek": "DeepSeek"}
TOKENS = ("input", "thinking", "output")


def read(path: Path, since: str = "", until: str = "", run: str = "") -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            c = json.loads(line)
        except ValueError:
            continue                                      # a line cut short by a killed process
        if (since and c["at"] < since) or (until and c["at"] > until) or (run and run not in c.get("run", "")):
            continue
        out.append(c)
    return out


def _p90(values: list[float]) -> float:
    values = sorted(values)
    return values[min(len(values) - 1, int(len(values) * 0.9))] if values else 0


def summarize(calls: list[dict]) -> list[dict]:
    """One line per provider and kind: counts, token sums and per-call medians, and time."""
    groups = {}
    for c in calls:
        groups.setdefault((c["backend"], c.get("label") or "other"), []).append(c)
    lines = []
    for (backend, label), cs in sorted(groups.items()):
        ok = [c for c in cs if not c.get("error")]
        line = {"backend": backend, "label": label, "calls": len(cs), "errors": len(cs) - len(ok),
                "cached_sum": sum(c.get("cached", 0) for c in cs), "hours": sum(c["seconds"] for c in cs) / 3600,
                "seconds_median": statistics.median([c["seconds"] for c in ok]) if ok else 0,
                "seconds_p90": _p90([c["seconds"] for c in ok])}
        for k in TOKENS:
            line[f"{k}_sum"] = sum(c.get(k, 0) for c in cs)
            line[f"{k}_median"] = statistics.median([c.get(k, 0) for c in ok]) if ok else 0
        lines.append(line)
    return lines


def in_flight(calls: list[dict]) -> dict[str, float]:
    """Average number of calls running at once per provider over the span: near the slot limit (4 after a
    rate limit, else 8) means the limit is what holds the run back, well below it means something else does."""
    out = {}
    for b in sorted({c["backend"] for c in calls if c.get("at")}):
        cs = [c for c in calls if c["backend"] == b and c.get("at")]
        ends = [datetime.fromisoformat(c["at"]).timestamp() for c in cs]
        span = max(ends) - min(e - c["seconds"] for e, c in zip(ends, cs))
        out[b] = sum(c["seconds"] for c in cs) / span if span > 0 else 0.0
    return out


def markdown(calls: list[dict]) -> str:
    if not calls:
        return "没有调用记录。"
    out = ["| 模型 | 环节 | 调用 | 出错 | 输入 token（其中缓存） | 思考 token | 输出 token | 每次中位：输入 / 思考 / 输出 "
           "| 每次耗时中位 / P90（秒） | 累计耗时（小时） |", "|---|---|---|---|---|---|---|---|---|---|"]
    for s in summarize(calls):
        out.append(f"| {PROVIDERS.get(s['backend'], s['backend'])} | {KINDS.get(s['label'], s['label'])} | {s['calls']} "
                   f"| {s['errors']} | {s['input_sum']:,}（{s['cached_sum']:,}） | {s['thinking_sum']:,} | {s['output_sum']:,} "
                   f"| {s['input_median']:,.0f} / {s['thinking_median']:,.0f} / {s['output_median']:,.0f} "
                   f"| {s['seconds_median']:.0f} / {s['seconds_p90']:.0f} | {s['hours']:.1f} |")
    out.append("")
    for b in sorted({c["backend"] for c in calls}):
        cs = [c for c in calls if c["backend"] == b]
        made = sum(c.get("thinking", 0) + c.get("output", 0) for c in cs)
        if made:
            out.append(f"- {PROVIDERS.get(b, b)}：生成的 token 里思考占 {sum(c.get('thinking', 0) for c in cs) / made:.0%}。")
    busy = in_flight(calls)
    if busy:
        out.append("- 平均同时在跑的调用：" + "；".join(f"{PROVIDERS.get(b, b)} {v:.1f}" for b, v in busy.items())
                   + "（接近并发上限说明卡在上限，远低于上限说明卡在别处）。")
    return "\n".join(out)
