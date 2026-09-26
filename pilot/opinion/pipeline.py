"""WO-004c rows: route each row (§1), write 【使用的比例】 by code (§3.1), draft the other two paragraphs with
the model (§3.2–3.3), check (§5), retry once, and merge into semantic/<row>.json, opinions.csv and fields.csv.

Every drafted row keeps a hash of its inputs (facts, picture columns Z / AA / AB, proportion paragraph,
system prompt). A later run with reuse keeps a saved opinion only while that hash is unchanged, so after
WO-004a-R3 changes the picture columns exactly the affected rows are drafted again. Opinions are also kept
in semantic/opinions/<row>.json, because a WO-004b refresh rewrites semantic/<row>.json without them.
"""
from __future__ import annotations

import csv
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from pilot.semantic.llm import ask
from pilot.semantic.table import Table
from pilot.textindex.t2s import T2S

from . import prompt as P
from .proportion import episode_seconds, proportion
from .samples import client_examples, corrections, table_samples
from .validate import CONCLUSION, PROPORTION, PURPOSE, SUBSTITUTION, validate

SKIP_TEXT = ("原告故意跳播，跳去 up 主点评内容，导致被告根本无法判断视频的性质，基于原告这种故意截取、有意跳过的行为，"
             "应当推定涉案视频构成合理使用。")      # 要求"故意跳播"B：候选文字，不起草，由所有者决定（§1）
SKIP_PLACEHOLDER = "【待所有者决定：疑似故意跳播】"
SHORT_SECONDS = 300
TEMPERATURE = 0.3
PICTURE = {"Z": "非权利作品（Z）", "AA": "遮挡物（AA）", "AB": "画面大小（AB）"}
TAG = "意见："


def usd(usage: dict, when: datetime) -> float:
    """deepseek-flash list price (USD per million tokens): peak UTC 01–04 and 06–10 on weekdays. Cached
    input is counted at the cache-miss price, so this is an upper bound."""
    peak = when.weekday() < 5 and (1 <= when.hour < 4 or 6 <= when.hour < 10)
    pin, pout = (0.30, 1.20) if peak else (0.15, 0.60)
    return ((usage.get("prompt") or 0) * pin + (usage.get("completion") or 0) * pout) / 1e6


def route(r: dict) -> str:
    if r["classification"] is None:
        return "repeat"
    if any(f["level"] == "red" and "疑似故意跳播" in f["reason"] for f in r["flags"]):
        return "skip"
    if r["fields"]["AG"]["value"] == "否":
        return "slash"
    return "draft"


def foreign_lines(case_dir: Path, rid: str) -> tuple[list[str], str | None]:
    path = case_dir / "asr" / f"{rid}.json"
    if not path.exists():
        return [], None
    segs = [s for s in json.loads(path.read_text(encoding="utf-8")).get("segments", [])
            if s.get("classification") == "NON_ORIGINAL" and s.get("language") not in (None, "zh") and (s.get("text") or "").strip()]
    lang = {"th": "泰", "en": "英", "ko": "韩", "ja": "日"}.get(segs[0]["language"], segs[0]["language"]) if segs else None
    return P.fit([s["text"].strip()[:80] for s in segs], P.FOREIGN_CHARS), lang


def facts(r: dict, case_dir: Path) -> dict:
    d, cls = r["digest"], r["classification"]
    nature = cls["nature"] + (f"（{cls['subtype']}）" if cls.get("subtype") and cls["nature"] in ("观点评析", "混剪") else "")
    note = None
    if cls["nature"] == "不确定":
        nature = f"未定（分类模型初判：{cls.get('model_nature') or '无'}）"
        note = "性质未定：请先按事实判断这条视频更接近哪一类，再按那一类的角度写。"
    grid = f"合理使用类型：{r['fields']['AI']['value']}；演绎类型：{r['fields']['AJ']['value']}"
    seeks = d["ledger"]["seeks"]["value"]
    up_all = [l["text"].translate(T2S) for l in d["up_texts"]]
    up = P.fit(up_all, P.UP_TEXT_CHARS)
    foreign, lang = foreign_lines(case_dir, r["row_id"])
    video = d["ledger"]["video_duration"]["value"]
    return {"title": d["title"]["value"], "nature": f"{nature}；{grid}", "nature_raw": cls["nature"],
            "jump": "不清楚是否跳播" if seeks == "未知" else "有跳播" if seeks else "没有跳播",
            "short": isinstance(video, (int, float)) and video < SHORT_SECONDS,
            "picture": {c: str(r["fields"][c]["value"]) for c in PICTURE},
            "picture_named": {name: str(r["fields"][c]["value"]) for c, name in PICTURE.items()},
            "up_texts": up, "up_cut": len(up) < len(up_all), "foreign": foreign, "foreign_language": lang,
            "title_only": not up and not foreign,
            "note": "".join(n for n in (note, "没有UP主文字和外语语音：第一段以标题和画面为依据，只概括主题，不要编造情节。"
                                        if not up and not foreign else None) if n) or None}


def assemble(purpose: str, prop: str, substitution: str, opinion_format: dict | None = None) -> str:
    headings = opinion_format["headings"] if opinion_format else (PURPOSE, PROPORTION, SUBSTITUTION)
    conclusion = opinion_format["conclusion"] if opinion_format else CONCLUSION
    return f"{headings[0]}{purpose.strip()}\n{headings[1]}{prop}\n{headings[2]}{substitution.strip()}\n{conclusion}"


def inputs_hash(system: str, f: dict, prop: str) -> str:
    blob = json.dumps({"system": system, "facts": f, "proportion": prop}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


def context(f: dict, prop: dict, data: dict) -> dict:
    return {"title": f["title"], "up_texts": f["up_texts"], "foreign": f["foreign"], "picture": f["picture"],
            "picture_named": f.get("picture_named"), "proportion": prop["text"], "evidence": data.get("evidence"),
            "nature": f.get("nature_raw"), "title_only": f.get("title_only")}


def revalidate(r: dict, case_dir: Path, ep_secs: dict, source_enabled: bool = True,
               client_context: dict | None = None) -> dict | None:
    """Check a saved draft again with the current checks (no model call); None when there is nothing to check."""
    op = r.get("opinion") or {}
    seg = op.get("segments")
    if op.get("kind") != "draft" or not seg:
        return None
    f, prop = facts(r, case_dir), proportion(r["digest"], ep_secs, source_enabled)
    data = {"purpose": seg["purpose"], "substitution": seg["substitution"], "evidence": op.get("evidence")}
    errors = validate(op["text"], data, {**context(f, prop, data), **(client_context or {})})
    flags = [x for x in op.get("flags", []) if not x["reason"].startswith("意见未通过校验")]
    if errors:
        flags.append({"level": "red", "reason": "意见未通过校验：" + "；".join(errors)[:300]})
    return {**op, "flags": flags, "validation": {**op["validation"], "ok": not errors, "errors": errors, "revalidated": True}}


def draft(r: dict, f: dict, prop: dict, system: str, backend: str, client_context: dict | None = None) -> dict:
    """Model draft, checked; one retry with the error list; a draft that still fails is kept and flagged red."""
    calls, previous, errors, text, data = [], None, [], "", {}
    for attempt in range(2):
        user = P.user_prompt(f) if attempt == 0 else P.retry_prompt(f, previous, errors)
        when = datetime.now(timezone.utc)
        reply = ask(backend, user, system=system, temperature=TEMPERATURE, required=("purpose", "substitution"))
        calls.append({"attempt": attempt + 1, "backend": reply.backend, "model": reply.model, "seconds": reply.seconds,
                      "usage": reply.usage, "usd": round(usd(reply.usage, when), 6) if reply.backend == "deepseek" else 0.0,
                      "error": reply.error, "fallback": reply.fallback, "valid_json": reply.data is not None})
        if reply.data is None:
            errors = [f"模型没有给出有效 JSON（{reply.error}）"]
            continue
        data = reply.data
        text = assemble(str(data.get("purpose", "")), prop["text"], str(data.get("substitution", "")),
                        (client_context or {}).get("opinion_format"))
        ctx = {**context(f, prop, data), **(client_context or {})}
        errors = validate(text, {"purpose": str(data.get("purpose", "")), "substitution": str(data.get("substitution", ""))}, ctx)
        previous = data
        if not errors:
            break
    return {"text": text, "data": data, "errors": errors, "calls": calls}


def opinion_for(r: dict, case_dir: Path, ep_secs: dict, system: str, backend: str, saved: dict | None,
                source_enabled: bool = True, client_context: dict | None = None) -> dict:
    kind = route(r)
    if kind == "repeat":
        return {"kind": kind, "text": str(r["fields"]["AF"]["value"]), "flags": [], "validation": {"ok": True, "errors": []}}
    if kind == "slash":
        return {"kind": kind, "text": "/", "flags": [], "validation": {"ok": True, "errors": []}}
    if kind == "skip":
        return {"kind": kind, "text": SKIP_PLACEHOLDER, "candidate": SKIP_TEXT, "candidate_style": "红字",
                "flags": [{"level": "red", "reason": "疑似故意跳播：不起草；候选文字取自客户要求原文，请所有者决定"}],
                "validation": {"ok": True, "errors": []}}
    f = facts(r, case_dir)
    prop = proportion(r["digest"], ep_secs, source_enabled)
    key = inputs_hash(system, f, prop["text"])
    if saved and saved.get("inputs_hash") == key and saved.get("kind") == "draft":
        return {**saved, "reused": True}
    flags = list(prop["flags"])
    if r["classification"]["nature"] == "不确定":
        flags.append({"level": "yellow", "reason": "分类为不确定：意见照常起草，请核对性质"})
    if f["foreign"]:
        flags.append({"level": "yellow", "reason": "外语转写，请核对大意"})
    if f["title_only"]:
        flags.append({"level": "yellow", "reason": "依据只有标题和画面，请核对"})
    if not prop["text"]:
        return {"kind": "draft", "text": "", "flags": flags, "inputs_hash": key,
                "validation": {"ok": False, "errors": ["比例段缺集数，未起草"]}}
    out = draft(r, f, prop, system, backend, client_context)
    ok = not out["errors"]
    if not ok:
        flags.append({"level": "red", "reason": "意见未通过校验：" + "；".join(out["errors"])[:300]})
    if any(c["fallback"] or c["backend"] in ("local", "local-vl") for c in out["calls"]):
        flags.append({"level": "yellow", "reason": "本地模型草稿"})
    data = out["data"]
    usage = {k: sum((c["usage"] or {}).get(k) or 0 for c in out["calls"]) for k in ("prompt", "completion", "reasoning", "cached")}
    return {"kind": "draft", "text": out["text"],
            "segments": {"purpose": data.get("purpose", ""), "proportion": prop["text"],
                         "substitution": data.get("substitution", ""),
                         "conclusion": (client_context or {}).get("opinion_format", {}).get("conclusion", CONCLUSION)},
            "bold": [b for b in str(data.get("purpose", "")).split("**")[1::2]],
            "evidence": data.get("evidence") or [], "proportion_basis": prop["basis"],
            "validation": {"ok": ok, "errors": out["errors"], "attempts": len(out["calls"])},
            "model": out["calls"][-1]["model"] if out["calls"] else None, "usage": usage,
            "usd": round(sum(c["usd"] for c in out["calls"]), 6), "calls": out["calls"],
            "inputs_hash": key, "flags": flags}


def merge(r: dict, op: dict) -> None:
    """Write the opinion into the row: AF (the review page reads it), the opinion record and its flags."""
    source = {"repeat": "重复取证规则", "slash": "合理使用可能性为否", "skip": "疑似故意跳播（候选文字待定）",
              "draft": f"WO-004c 草稿（{op.get('model') or '模型'}）"}[op["kind"]]
    basis = "；".join(op["validation"]["errors"]) if op["validation"]["errors"] else \
        ("校验通过" if op["kind"] == "draft" else source)
    r["fields"]["AF"] = {"value": op["text"], "source": source, "basis": basis}
    r["flags"] = [f for f in r["flags"] if not f["reason"].startswith(TAG)] + \
                 [{"level": f["level"], "reason": TAG + f["reason"]} for f in op.get("flags", [])]
    r["opinion"] = op


def run(case_dir: Path, table: Table, backend: str = "deepseek", only: set[str] | None = None, workers: int = 4,
        reuse: bool = False, episodes_dir: Path | None = None, corrections_path: Path | None = None, log=print,
        recheck: bool = False, case_config=None) -> dict:
    sem = case_dir / "semantic"
    valid_rows = set(case_config.row_ids) if case_config else None
    rows = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(sem.glob("*.json"))
            if p.stem.isdecimal() and (valid_rows is None or p.stem in valid_rows)}
    if case_config:
        source = case_config.raw["source"]
        source_enabled = bool(source["enabled"])
        episodes_dir = case_config.path_of(source.get("episode_dir")) if source_enabled else None
        ep_secs = (episode_seconds(episodes_dir, sem / "episode_seconds.json", source["episode_pattern"])
                   if source_enabled else {})
        client = case_config.raw["client"]
        opinion_format = client["opinion_format"]
        client_context = {"work_title": client["work_title"], "opinion_format": opinion_format}
        style = client["style_examples"]
        requirements = case_config.path_of(style.get("requirements"))
        excluded = set(map(int, case_config.row_ids)) if style.get("exclude_current_rows", True) else set()
        sample_conclusion = None if opinion_format.get("validation_profile") == "qsjx" else opinion_format["conclusion"]
        samples = (client_examples(requirements, opinion_format["headings"], sample_conclusion)
                   + table_samples(table, style.get("table_rows") or {}, excluded,
                                   opinion_format["headings"], sample_conclusion)
                   + corrections(corrections_path or case_dir / "corrections.jsonl"))
        rules_path = case_config.path_of(opinion_format.get("prompt_rules"))
        rules = rules_path.read_text(encoding="utf-8") if rules_path else None
    else:
        source_enabled, client_context = True, None
        episodes_dir = episodes_dir or case_dir.resolve().parents[2] / "real_test"
        ep_secs = episode_seconds(episodes_dir, sem / "episode_seconds.json")
        samples = client_examples() + table_samples(table) + corrections(corrections_path or case_dir / "corrections.jsonl")
        rules = None
    system = P.system_prompt(samples, rules)
    todo = [rid for rid in rows if not only or rid in only]
    todo.sort(key=lambda rid: (rows[rid]["digest"]["uploader"]["value"] or "", rid))      # same uploader together
    t0 = time.monotonic()

    store = sem / "opinions"
    store.mkdir(exist_ok=True)

    def one(rid: str) -> tuple[str, dict]:
        if recheck:
            return rid, revalidate(rows[rid], case_dir, ep_secs, source_enabled, client_context) or rows[rid].get("opinion")
        kept = store / f"{rid}.json"
        saved = (rows[rid].get("opinion") or (json.loads(kept.read_text(encoding="utf-8")) if kept.exists() else None)) \
            if reuse else None
        return rid, opinion_for(rows[rid], case_dir, ep_secs, system, backend, saved, source_enabled, client_context)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for rid, op in ex.map(one, todo):
            merge(rows[rid], op)
            (sem / f"{rid}.json").write_text(json.dumps(rows[rid], ensure_ascii=False, indent=1), encoding="utf-8")
            (store / f"{rid}.json").write_text(json.dumps({k: v for k, v in op.items() if k != "reused"},
                                                          ensure_ascii=False, indent=1), encoding="utf-8")
            v = op["validation"]
            log(f"[{time.monotonic() - t0:6.0f}s] {rid} {op['kind']}" + ("（沿用）" if op.get("reused") else "")
                + ("" if op["kind"] != "draft" else f" {'通过' if v['ok'] else '未通过'} 尝试 {v.get('attempts', 0)}"))
    write_outputs(rows, sem, table)
    return summary(rows, time.monotonic() - t0, len(samples))


def dry(case_dir: Path, table: Table, only: set[str] | None = None, episodes_dir: Path | None = None) -> dict:
    """The prompts and proportion paragraphs of the rows that would be drafted; no model call, nothing written."""
    sem = case_dir / "semantic"
    rows = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(sem.glob("1*.json"))}
    ep_secs = episode_seconds(episodes_dir or case_dir.resolve().parents[2] / "real_test", sem / "episode_seconds.json")
    system = P.system_prompt(client_examples() + table_samples(table))
    out = {"system": system, "rows": []}
    for rid, r in rows.items():
        if only and rid not in only:
            continue
        kind = route(r)
        item = {"row_id": rid, "kind": kind}
        if kind == "draft":
            f = facts(r, case_dir)
            prop = proportion(r["digest"], ep_secs)
            item.update({"proportion": prop["text"], "proportion_flags": prop["flags"], "user": P.user_prompt(f)})
        out["rows"].append(item)
    return out


def write_outputs(rows: dict, sem: Path, table: Table) -> None:
    from pilot.semantic.report import write_rows
    with (sem / "opinions.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["总编号", "比对意见", "类别", "校验结果", "标记"])
        for rid, r in sorted(rows.items()):
            op = r.get("opinion") or {}
            v = op.get("validation") or {}
            w.writerow([rid, op.get("text", ""), op.get("kind", ""),
                        "通过" if v.get("ok") else "；".join(v.get("errors") or ["未起草"]),
                        "；".join(f"[{x['level']}] {x['reason']}" for x in op.get("flags", []))])
    write_rows(list(r for _, r in sorted(rows.items())), sem, table)       # fields.csv with 比对意见 filled


def summary(rows: dict, seconds: float, n_samples: int) -> dict:
    ops = [r.get("opinion") or {} for r in rows.values()]
    drafts = [o for o in ops if o.get("kind") == "draft"]
    calls = [c for o in drafts for c in o.get("calls", [])]
    return {"rows": len(rows), "kinds": {k: sum(1 for o in ops if o.get("kind") == k) for k in ("draft", "slash", "repeat", "skip")},
            "drafts_passed": sum(1 for o in drafts if o["validation"]["ok"]), "drafts": len(drafts),
            "retried": sum(1 for o in drafts if o["validation"].get("attempts", 0) > 1),
            "calls": len(calls), "tokens": {k: sum((c["usage"] or {}).get(k) or 0 for c in calls)
                                            for k in ("prompt", "completion", "reasoning", "cached")},
            "usd": round(sum(c["usd"] for c in calls), 4), "seconds": round(seconds, 1), "style_samples": n_samples}
