"""WO-004c-R1 §4: a second model writes the same 20 rows from the same prompts and inputs, for the Owner's
blind review. The review files never name a model; which text is A or B is kept only in key.json."""
from __future__ import annotations

import csv
import json
import random
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pilot.semantic.table import Table

from . import prompt as P
from .pipeline import draft, facts, route
from .proportion import episode_seconds, proportion
from .samples import client_examples, corrections, table_samples

STRATA = (("reaction", ("reaction",), 5), ("混剪", ("混剪",), 5), ("观点评析/夹叙夹议", ("观点评析", "夹叙夹议解说"), 5),
          ("不确定", ("不确定",), 5))
SEED = 20260926


def sample(rows: dict[str, dict]) -> list[str]:
    """20 drafted rows stratified by nature (fixed seed)."""
    rnd = random.Random(SEED)
    picked = []
    for _, natures, n in STRATA:
        pool = sorted(rid for rid, r in rows.items() if route(r) == "draft" and r["classification"]["nature"] in natures
                      and (r.get("opinion") or {}).get("text"))
        picked += rnd.sample(pool, min(n, len(pool)))
    return sorted(picked)


def stats(records: list[dict]) -> dict:
    calls = [c for r in records for c in r["calls"]]
    return {"rows": len(records), "calls": len(calls), "passed": sum(1 for r in records if r["ok"]),
            "seconds_per_row": round(sum(c.get("seconds") or 0 for c in calls) / max(1, len(records)), 1),
            "tokens": {k: sum((c.get("usage") or {}).get(k) or 0 for c in calls) for k in ("prompt", "completion", "reasoning", "cached")},
            "usd": round(sum(c.get("usd") or 0 for c in calls), 4)}


def run_ab(case_dir: Path, table: Table, backend: str = "zhipu", workers: int = 4, episodes_dir: Path | None = None,
           log=print) -> dict:
    sem = case_dir / "semantic"
    out = sem / "opinion_ab"
    out.mkdir(exist_ok=True)
    rows = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(sem.glob("1*.json"))}
    ep_secs = episode_seconds(episodes_dir or case_dir.resolve().parents[2] / "real_test", sem / "episode_seconds.json")
    system = P.system_prompt(client_examples() + table_samples(table) + corrections(case_dir / "corrections.jsonl"))
    picked = sample(rows)

    def one(rid: str) -> tuple[str, dict]:
        r = rows[rid]
        return rid, draft(r, facts(r, case_dir), proportion(r["digest"], ep_secs), system, backend)

    other: dict[str, dict] = {}
    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for rid, res in ex.map(one, picked):
            other[rid] = res
            log(f"[{time.monotonic() - t0:6.0f}s] {rid} {'通过' if not res['errors'] else '未通过'} 尝试 {len(res['calls'])}")

    rnd = random.Random(SEED + 1)
    key, md, main_model = {}, [], None
    for rid in picked:
        r, mine = rows[rid], rows[rid]["opinion"]
        main_model = mine.get("model")
        pair = [(mine.get("model"), mine["text"]), (other[rid]["calls"][-1]["model"] if other[rid]["calls"] else backend,
                                                   other[rid]["text"])]
        rnd.shuffle(pair)
        key[rid] = {"A": pair[0][0], "B": pair[1][0]}
        d, fl = r["digest"], r["fields"]
        lines = "；".join(l["text"] for l in d["up_texts"][:4]) or "无"
        md.append(f"## {rid}　{r['classification']['nature']}　（{fl['AI']['value']} / {fl['AJ']['value']}）\n\n"
                  f"- 标题：{d['title']['value']}\n- 画面：非权利作品 {fl['Z']['value']}；遮挡物 {fl['AA']['value']}；画面大小 {fl['AB']['value']}\n"
                  f"- UP 主文字（前几句）：{lines[:160]}\n\n### 意见 A\n\n{pair[0][1] or '（没有生成）'}\n\n### 意见 B\n\n{pair[1][1] or '（没有生成）'}\n")
    (out / "ab_review.md").write_text("# 比对意见盲评（20 对）\n\n每行两份意见，A、B 顺序随机；模型对应关系在 key.json，评完再看。\n"
                                      "评分填在 ab_scores.csv。\n\n" + "\n".join(md), encoding="utf-8")
    with (out / "ab_scores.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["总编号", "更好的一份（A/B/相当）", "A（直接用/小改/大改/不能用）", "B（直接用/小改/大改/不能用）", "备注"])
        for rid in picked:
            w.writerow([rid, "", "", "", ""])
    mine_records = [{"ok": rows[rid]["opinion"]["validation"]["ok"], "calls": rows[rid]["opinion"].get("calls", [])} for rid in picked]
    other_records = [{"ok": not other[rid]["errors"], "calls": other[rid]["calls"]} for rid in picked]
    summary = {"rows": picked, "stats": {main_model or "main": stats(mine_records), backend: stats(other_records)}}
    (out / "key.json").write_text(json.dumps({"pairs": key, **summary,
                                              "other_drafts": {rid: {"text": other[rid]["text"], "errors": other[rid]["errors"],
                                                                     "evidence": other[rid]["data"].get("evidence")}
                                                               for rid in picked}},
                                             ensure_ascii=False, indent=1), encoding="utf-8")
    return summary
