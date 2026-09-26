"""§3.4b comparison: the same inputs through GLM-5.3-Flash, DeepSeek V4.1 Flash and the local models.

1. visual facts for the 19 WO-004a rows -> predictions_cloud.csv (columns of predictions_r1.csv);
2. nature + four grid for 30 rows sampled by the plaintiff's video type, both as one call (collage +
   digest text) and in two steps (look at the collage, then classify from text);
3. valid-JSON rate, seconds, tokens and failures per call -> compare_calls.csv / compare_summary.json.

Only collages cropped to the player picture are sent, after being copied to cloud_outbox/ (D-024).
Owner's annotations are not used here in any way.
"""
from __future__ import annotations

import copy
import csv
import json
import random
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from itertools import combinations
from pathlib import Path

from pilot.content.extract import load_ocr

from . import collage as C
from .classify import classify
from .digest import build_digest, load_visual
from .fields import four_grid, load_rules
from .llm import ask

SAMPLE_PER_TYPE = {"Reaction": 8, "切条": 8, "解说": 8, "原片": 6}
SEED = 20260925
R1_COLUMNS = ["row_id", "video_type", "model", "collage_path", "ui_status", "layout", "rights_content_ratio",
              "uploader_on_camera", "overlays", "other_works", "other_works_desc", "form_guess", "evidence_frames",
              "elapsed_seconds", "peak_resident_mb", "peak_vram_mb", "status", "error", "description_raw", "geometry_ratio"]
VISION_BACKENDS = {"local": "local-vl", "deepseek": "deepseek", "zhipu": "zhipu"}
# USD per million tokens (deepseek-flash, cache-miss input / output) at peak hours, UTC 01-04 and 06-10 on
# weekdays, when this comparison ran; an upper bound. Zhipu's price page could not be read: check the bill.
PRICES_USD_PER_M = {"deepseek": (0.30, 1.20)}

VISUAL_PROMPT = """这是一条B站视频的 2×2 拼图，是播放中的 4 个时刻（左上=1，右上=2，左下=3，右下=4）。原剧是一部古装电视剧。
只根据画面回答，输出一个 JSON 对象：
{"layout": "全屏 / 原剧缩小加背景或边框 / 画中画(UP主为主) / 分屏 / 不确定",
 "rights_content_ratio": "原剧画面约占整个画面的面积：约1 / 约3/4 / 约2/3 / 约1/2 / 约1/4 / 约1/6 / 不确定",
 "uploader_on_camera": "UP主本人是否真人出镜：是 / 否 / 不确定",
 "overlays": ["UP主加在画面上的元素，可多选：贴纸或表情包 / 装饰花纹或边框 / 文字标题卡 / UP主解说字幕 / 歌词字幕；没有就空列表；平台水印和UP主水印不算"],
 "other_works": "是否出现这部剧以外的其他影视作品画面：是 / 否 / 不确定",
 "other_works_desc": "如果是，写出是什么",
 "form_guess": "reaction / 混剪 / 解说 / 原片切条 / 不确定",
 "evidence_frames": [依据的帧号],
 "description": "用两三句话描述画面"}"""


def _sample(case_dir: Path, vlm_dir: Path, rows: dict[str, dict], have: set[str], outbox: Path) -> tuple[list[str], dict]:
    """Per plaintiff type: rows with a WO-004a collage first, then random others (fixed seed) for which a
    player-only collage can be made; rows without one are skipped, so every sampled row has a picture."""
    by_type = defaultdict(list)
    for rid, r in rows.items():
        by_type[r["plaintiff"].get("video_type") or "其他"].append(rid)
    rnd = random.Random(SEED)
    picked, collages = [], {}
    for vt, n in SAMPLE_PER_TYPE.items():
        pool = sorted(by_type.get(vt, []))
        rest = [x for x in pool if x not in have]
        rnd.shuffle(rest)
        got = 0
        for rid in [x for x in pool if x in have] + rest:
            if got >= n:
                break
            made = _collages(case_dir, vlm_dir, rows, [rid], outbox)
            if rid in made:
                picked.append(rid)
                collages.update(made)
                got += 1
    return sorted(picked), collages


def _collages(case_dir: Path, vlm_dir: Path, rows: dict[str, dict], rids: list[str], outbox: Path) -> dict[str, Path]:
    made = {}
    frames_by_video: dict[str, dict] = {}
    for rid in rids:
        existing = vlm_dir / "collages_r1" / f"{rid}.jpg"
        out = outbox / f"{rid}.jpg"
        if existing.exists():
            made[rid] = C.inset_existing(existing, out)
            continue
        row = rows[rid]
        if row.get("video") not in frames_by_video:
            frames_by_video.clear()
            frames_by_video[row["video"]] = load_ocr(case_dir, row["video"])
        path = C.make_collage(row, frames_by_video[row["video"]], out)
        if path:
            made[rid] = path
    return made


def visual_facts(backend: str, collage: Path) -> tuple[dict | None, dict]:
    r = ask(VISION_BACKENDS[backend], VISUAL_PROMPT, images=[collage], max_tokens=1024, required=("layout",))
    call = {"backend": r.backend, "model": r.model, "seconds": r.seconds, "usage": r.usage, "error": r.error,
            "fallback": r.fallback, "attempts": r.attempts, "valid_json": r.data is not None}
    return r.data, call


def _vision_note(v: dict | None) -> str:
    if not v:
        return "看图失败，无画面事实"
    return (f"布局：{v.get('layout')}；UP主真人出镜：{v.get('uploader_on_camera')}；附加元素：{'、'.join(v.get('overlays') or []) or '无'}；"
            f"原剧画面约占：{v.get('rights_content_ratio')}；其他作品：{v.get('other_works')} {v.get('other_works_desc') or ''}；"
            f"画面描述：{v.get('description') or ''}")


def _replaced(task: str, mode: str, steps: list[str], modes: list[str]) -> bool:
    """Does this run redo a saved call or result of the same model?"""
    return "visual" in steps if task == "visual" else "classify" in steps and (mode or "") in modes


def compare(case_dir: Path, vlm_dir: Path, table, out_dir: Path, backends: list[str], steps: list[str],
            modes: list[str] = ("one_call", "two_step"), workers: int = 1, resume: bool = False,
            redo_failed: bool = False) -> None:
    """resume: keep the saved classification results and calls, run only the (row, model, mode) still missing;
    redo_failed: also rerun a (row, model, mode) whose classification had a failed call (network, proxy)."""
    if steps == ["summary"]:                     # recompute compare_summary.json from the saved CSVs, no model calls
        write_summary(out_dir)
        return
    rows = {r["row_id"]: r for r in map(json.loads, (case_dir / "ledger.jsonl").read_text(encoding="utf-8").splitlines())}
    r1 = {r["row_id"]: r for r in csv.DictReader((vlm_dir / "predictions_r1.csv").open(encoding="utf-8-sig"))
          if r["model"].startswith("qwen3-vl")}
    outbox = out_dir / "cloud_outbox"
    out_dir.mkdir(parents=True, exist_ok=True)
    rules = load_rules()
    calls = []
    visual_rows = sorted(r1)
    sample, collages = _sample(case_dir, vlm_dir, rows, set(visual_rows), outbox)
    collages.update(_collages(case_dir, vlm_dir, rows, [r for r in visual_rows if r not in collages], outbox))
    (out_dir / "compare_sample.json").write_text(json.dumps({"visual_rows": visual_rows, "classify_rows": sample,
                                                             "outbox": sorted(p.name for p in outbox.glob("*.jpg"))},
                                                            ensure_ascii=False, indent=1), encoding="utf-8")
    seen_visual: dict[tuple[str, str], dict | None] = {}
    if "visual" not in steps:                    # two-step rows reuse this model's saved visual facts (already scored)
        for r in _read(out_dir / "predictions_cloud.csv"):
            fam = _family(r.get("model", ""))
            if fam in backends and r.get("status") == "ok":
                seen_visual[(r["row_id"], fam)] = {
                    "layout": r["layout"], "rights_content_ratio": r["rights_content_ratio"],
                    "uploader_on_camera": r["uploader_on_camera"], "overlays": json.loads(r["overlays"] or "[]"),
                    "other_works": r["other_works"], "other_works_desc": r["other_works_desc"], "description": r["description_raw"]}

    families = {_family(b) for b in backends}
    old_calls = [c for c in _read(out_dir / "compare_calls.csv")
                 if resume or not (_family(c.get("backend")) in families and _replaced(c.get("task"), c.get("mode"), steps, modes))]
    old_results = [r for r in _read(out_dir / "compare_classify.csv")
                   if resume or not (r.get("backend") in backends and _replaced("classify", r.get("mode"), steps, modes))]
    if resume and redo_failed:
        failed = {(c.get("row_id"), _family(c.get("backend")), c.get("mode")) for c in old_calls
                  if c.get("task") in ("classify", "classify-look") and str(c.get("valid_json")) != "True"}
        old_calls = [c for c in old_calls if (c.get("row_id"), _family(c.get("backend")), c.get("mode")) not in failed]
        old_results = [r for r in old_results if (r["row_id"], r["backend"], r["mode"]) not in failed]
    done = {(r["row_id"], r["backend"], r["mode"]) for r in old_results} if resume else set()
    results: list[dict] = []

    def save() -> None:                          # after every row, so an interrupted run keeps its call records
        flat = [{**{k: v for k, v in c.items() if k != "usage"}, **{f"tokens_{k}": v for k, v in (c.get("usage") or {}).items()}}
                for c in calls]
        _write(out_dir / "compare_calls.csv", old_calls + flat)
        _write(out_dir / "compare_classify.csv", old_results + results)

    models_now = {VISION_BACKENDS[b] for b in backends} | set(backends)
    if "visual" in steps:
        pred_path = out_dir / "predictions_cloud.csv"
        kept = [r for r in _read(pred_path) if _backend_of(r.get("model", "")) not in models_now]
        with pred_path.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=R1_COLUMNS)
            w.writeheader()
            w.writerows(kept)
            for rid in visual_rows:
                for b in backends:
                    data, call = visual_facts(b, collages[rid]) if rid in collages else (None, {"error": "没有拼图"})
                    seen_visual[(rid, b)] = data
                    calls.append({"task": "visual", "row_id": rid, "mode": "", **call})
                    d = data or {}
                    w.writerow({"row_id": rid, "video_type": r1[rid]["video_type"], "model": call.get("model", b),
                                "collage_path": str(collages.get(rid, "")), "ui_status": r1[rid]["ui_status"],
                                "layout": d.get("layout", ""), "rights_content_ratio": d.get("rights_content_ratio", ""),
                                "uploader_on_camera": d.get("uploader_on_camera", ""),
                                "overlays": json.dumps(d.get("overlays") or [], ensure_ascii=False),
                                "other_works": d.get("other_works", ""), "other_works_desc": d.get("other_works_desc", ""),
                                "form_guess": d.get("form_guess", ""), "evidence_frames": json.dumps(d.get("evidence_frames") or []),
                                "elapsed_seconds": call.get("seconds", ""), "peak_resident_mb": "", "peak_vram_mb": "",
                                "status": "ok" if data else "error", "error": call.get("error", ""),
                                "description_raw": d.get("description", ""), "geometry_ratio": r1[rid].get("geometry_ratio", "")})
                    f.flush()
                    save()

    save()
    if "classify" in steps:
        bases, corpora = {}, {}
        for rid in sample:
            content = _json(case_dir / "content" / f"{rid}.json")
            asr = _json(case_dir / "asr" / f"{rid}.json")
            bases[rid] = build_digest(rows[rid], content, asr, None, "", [], [])
            corpora[rid] = [e["text"] for e in (content or {}).get("events", [])] + \
                           [s.get("text", "") for s in (asr or {}).get("segments", [])]

        def job(key: tuple[str, str, str]) -> tuple[dict, list[dict]]:
            rid, b, mode = key
            d, own = copy.deepcopy(bases[rid]), []
            if mode == "one_call":
                cls = classify(d, b if b != "local" else "local-vl", rules,
                               images=[collages[rid]] if rid in collages else [], corpus=corpora[rid],
                               vision_note="" if rid in collages else "无拼图")
            else:
                v = seen_visual.get((rid, b))
                if v is None and rid in collages:
                    v, call = visual_facts(b, collages[rid])
                    own.append({"task": "classify-look", "row_id": rid, "mode": mode, **call})
                cls = classify(d, b, rules, vision_note=_vision_note(v), corpus=corpora[rid])
            seeks = bases[rid]["ledger"]["seeks"]["value"]
            grid, _ = four_grid(cls["nature"], cls["subtype"], cls["recreation"], isinstance(seeks, list) and bool(seeks), rules)
            own += [{"task": "classify", "row_id": rid, "mode": mode, **c} for c in cls["calls"]]
            return ({"row_id": rid, "plaintiff_type": rows[rid]["plaintiff"].get("video_type"), "backend": b,
                     "mode": mode, "nature": cls["nature"], "model_nature": cls["model_nature"],
                     "subtype": cls["subtype"], "comment_share": cls["comment_share"],
                     "grid": " / ".join(grid), "evidence_ok": sum(1 for e in cls["evidence"] if e.get("quote")),
                     "flags": "；".join(f["reason"] for f in cls["flags"])}, own)

        keys = [(rid, b, mode) for rid in sample for b in backends for mode in modes if (rid, b, mode) not in done]
        with ThreadPoolExecutor(max_workers=1 if "local" in backends else max(1, workers)) as ex:
            for result, own in ex.map(job, keys):
                results.append(result)
                calls.extend(own)
                save()
    save()
    write_summary(out_dir)


def write_summary(out_dir: Path) -> None:
    calls = [{**c, "usage": {k: _num(c.get(f"tokens_{k}")) for k in ("prompt", "completion", "reasoning")},
              "seconds": _num(c.get("seconds")), "valid_json": str(c.get("valid_json")) == "True"}
             for c in _read(out_dir / "compare_calls.csv")]
    summary = _summary(calls, _read(out_dir / "compare_classify.csv"), _read(out_dir / "predictions_cloud.csv"))
    (out_dir / "compare_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")


def _read(path: Path) -> list[dict]:
    return list(csv.DictReader(path.open(encoding="utf-8-sig"))) if path.exists() else []


def _num(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _backend_of(model_or_backend: str) -> str:
    """predictions / calls rows name a model or a backend; map both to the backend name."""
    for name, b in (("deepseek", "deepseek-flash"), ("zhipu", "glm-5.3-flash"), ("local-vl", "qwen3-vl"), ("local", "qwen3:4b")):
        if model_or_backend in (name, b) or model_or_backend.startswith(b):
            return name
    return model_or_backend


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _write(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    keys = list(dict.fromkeys(k for r in rows for k in r))
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


def _family(backend: str) -> str:
    b = _backend_of(backend or "")
    return "local" if b == "local-vl" else b


def _summary(calls: list[dict], results: list[dict], predictions: list[dict] = ()) -> dict:
    """Per model: visual facts (19 rows) and classification in one call or two steps (30 rows). A two-step
    row costs its look at the collage (the visual-facts call when the row has one) plus the text calls.
    A visual-facts call missing from the call log (the first run was stopped before writing it) is taken
    from predictions_cloud.csv (status, seconds) with this model's mean tokens of recorded look calls: same
    prompt, same collage size. Such calls are counted as imputed."""
    look = {}
    for c in calls:
        if c.get("task") in ("visual", "classify-look"):
            look.setdefault((_family(c.get("backend")), c.get("row_id")), []).append(c)
    mean_usage = {}
    for fam in {f for f, _ in look}:
        cs = [c for (f, _), v in look.items() if f == fam for c in v]
        mean_usage[fam] = {k: sum(c["usage"].get(k) or 0 for c in cs) / len(cs) for k in ("prompt", "completion", "reasoning")}
    for p in predictions:                        # visual-facts calls known only from the predictions file
        fam = _family(p.get("model", ""))
        if (fam, p["row_id"]) not in look and fam in mean_usage:
            look[(fam, p["row_id"])] = [{"task": "visual", "row_id": p["row_id"], "backend": fam, "imputed": True,
                                         "seconds": _num(p.get("elapsed_seconds")), "usage": dict(mean_usage[fam]),
                                         "valid_json": p.get("status") == "ok", "error": p.get("error") or ""}]
    groups = defaultdict(lambda: defaultdict(list))            # (model, job) -> row -> calls
    for (fam, rid), cs in look.items():
        if any(c.get("task") == "visual" for c in cs):
            groups[(fam, "画面事实")][rid] += [c for c in cs if c.get("task") == "visual"]
    for c in calls:
        if c.get("task") == "classify":
            job = "分类·一次调用" if c.get("mode") == "one_call" else "分类·两步"
            groups[(_family(c.get("backend")), job)][c.get("row_id")].append(c)
    for (fam, job), per_row in groups.items():
        if job == "分类·两步":
            for rid in per_row:
                per_row[rid] = per_row[rid] + look.get((fam, rid), [])[:1]
    out = {}
    for (fam, job), per_row in sorted(groups.items()):
        cs = [c for rc in per_row.values() for c in rc]
        n = max(1, len(per_row))
        tokens = {k: sum(c["usage"].get(k) or 0 for c in cs) for k in ("prompt", "completion", "reasoning")}
        price = PRICES_USD_PER_M.get(fam)
        out[f"{fam} {job}"] = {
            "rows": len(per_row), "calls": len(cs), "imputed_calls": sum(1 for c in cs if c.get("imputed")),
            "valid_json_rate": round(sum(bool(c.get("valid_json")) for c in cs) / max(1, len(cs)), 3),
            "retried_calls": sum(1 for c in cs if _num(c.get("attempts")) > 1),
            "fallback_calls": sum(1 for c in cs if c.get("fallback")),
            "seconds_per_row": round(sum(c.get("seconds") or 0 for c in cs) / n, 1),
            "tokens_per_row": {k: round(v / n) for k, v in tokens.items()},
            "usd_per_row": round((tokens["prompt"] * price[0] + tokens["completion"] * price[1]) / 1e6 / n, 5) if price else None,
            "usd_total": round((tokens["prompt"] * price[0] + tokens["completion"] * price[1]) / 1e6, 4) if price else None,
            "failures": Counter((c.get("error") or "")[:60] for c in cs if c.get("error")).most_common(5)}
    natures = defaultdict(dict)
    for r in results:
        natures[f"{_family(r['backend'])} {r['mode']}"][r["row_id"]] = (r["nature"], r["grid"])
    out["nature_distribution"] = {k: dict(Counter(n for n, _ in v.values())) for k, v in natures.items()}
    keys = sorted(natures)
    out["agreement"] = {f"{a} ~ {b}": {"rows": len(common := natures[a].keys() & natures[b].keys()),
                                        "same_nature": sum(natures[a][r][0] == natures[b][r][0] for r in common),
                                        "same_grid": sum(natures[a][r][1] == natures[b][r][1] for r in common)}
                        for a, b in combinations(keys, 2)}
    return out
