"""WO-006 case entry point. Module algorithms stay in their owning packages."""
from __future__ import annotations

import hashlib
import csv
import json
import os
import sys
import time
from pathlib import Path

from .case_config import CaseConfig, REPO

STEPS = ("ledger", "captions", "speech", "audio", "visual", "classification", "opinion", "review")


def _stamp(path: Path) -> list:
    """Use names, sizes and mtimes for large evidence; hash small files exactly."""
    if not path.exists():
        return [str(path), None]
    if path.is_dir():
        return [str(path), [_stamp(p) for p in sorted(path.iterdir()) if p.is_file()]]
    stat = path.stat()
    if stat.st_size <= 20_000_000:
        return [str(path), hashlib.sha256(path.read_bytes()).hexdigest()]
    return [str(path), stat.st_size, stat.st_mtime_ns]


def _fingerprint(config: CaseConfig, step: str, rows: list[str]) -> str:
    raw = config.raw
    inputs = {
        "ledger": [config.path_of(raw["workbook"]["path"]), config.path_of(raw["evidence"]["video_dir"]),
                   config.path_of(raw["evidence"]["screenshot_dir"])],
        "captions": [config.case_dir / "ledger.jsonl", config.case_dir / "ocr_cache",
                     config.index_dir / "index.sqlite", config.path_of(raw["source"].get("episode_dir"))],
        "speech": [config.case_dir / "ledger.jsonl", config.index_dir / "index.sqlite",
                   config.path_of(raw["models"]["speech"].get("model"))],
        "audio": [config.case_dir / "ledger.jsonl", config.index_dir / "index.sqlite",
                  config.path_of((raw["models"].get("audio") or {}).get("model"))],
        "visual": [config.case_dir / "ledger.jsonl", config.case_dir / "ocr_cache",
                   config.path_of(raw["evidence"]["video_dir"])],
        "classification": [config.case_dir / "ledger.jsonl", config.case_dir / "content", config.case_dir / "audio",
                           config.vlm_dir / "predictions_consensus_r3.csv", config.path_of(raw["client"]["rules"])],
        "opinion": [config.case_dir / "semantic", config.path_of(raw["client"]["style_examples"].get("workbook"))],
        "review": [config.case_dir / "ledger.jsonl", config.case_dir / "content", config.case_dir / "semantic",
                   config.vlm_dir / "predictions_consensus_r3.csv"],
    }[step]
    # Fingerprint only settings the stage actually reads. Changing the opinion model
    # must not invalidate classification or redo the cloud visual pass.
    settings = {
        "ledger": {"workbook": raw["workbook"], "evidence": raw["evidence"], "ledger": raw["ledger"]},
        "captions": {"source": raw["source"]},
        "speech": {"speech": raw["models"]["speech"]},
        "audio": {"audio": raw["models"].get("audio")},
        "visual": {"visual": raw["models"]["visual"], "canvas": raw.get("visual"),
                   "evidence": raw["evidence"]},
        "classification": {"rules": raw["client"]["rules"], "text": raw["models"]["text"],
                           "cloud_visual": raw["models"]["visual"]["allow_image_upload"],
                           "workbook": raw["workbook"]},
        "opinion": {"client": raw["client"], "backend": raw["models"].get("opinion", raw["models"]["text"]),
                    "source": raw["source"], "workbook": raw["workbook"]},
        "review": {"workbook": raw["workbook"], "output": raw["output"]},
    }[step]
    module_dir = {"ledger": "ledger", "captions": "content", "speech": "asr", "audio": "audio", "visual": "vlm",
                  "classification": "semantic", "opinion": "opinion", "review": "review"}[step]
    # Per-row outputs can be numerous and contain timestamps; only the selected row inputs affect a partial run.
    data = [settings, rows, [_stamp(p) for p in inputs if p is not None],
            [_stamp(p) for p in sorted((REPO / "pilot" / module_dir).glob("*.py"))]]
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _row_files(folder: Path, rows: list[str], suffix=".json") -> bool:
    return all((folder / f"{rid}{suffix}").is_file() for rid in rows)


def _ready(config: CaseConfig, step: str, rows: list[str]) -> bool:
    case, vlm = config.case_dir, config.vlm_dir
    if step == "ledger":
        path = case / "ledger.jsonl"
        if not path.is_file():
            return False
        found = {json.loads(line)["row_id"] for line in path.read_text(encoding="utf-8").splitlines()}
        return found == set(config.row_ids)
    if step == "captions":
        return _row_files(case / "content", rows) and (not config.raw["source"]["enabled"] or
                                                       (config.index_dir / "index.sqlite").is_file())
    if step == "speech":
        return _row_files(case / "asr", rows)
    if step == "audio":
        return _row_files(case / "audio", rows)
    if step == "visual":
        name = "consensus.json" if config.raw["models"]["visual"]["allow_image_upload"] else "local.json"
        return all((vlm / "r3" / rid / name).is_file() and
                   json.loads((vlm / "r3" / rid / name).read_text(encoding="utf-8")).get("status")
                   in ("ok", "skipped") for rid in rows)
    if step in ("classification", "opinion"):
        if not _row_files(case / "semantic", rows):
            return False
        if step == "opinion":
            return all((json.loads((case / "semantic" / f"{rid}.json").read_text(encoding="utf-8"))
                        .get("opinion") is not None) for rid in rows)
        if config.raw["models"]["visual"]["allow_image_upload"]:
            def visual_ready(rid: str) -> bool:
                consensus = vlm / "r3" / rid / "consensus.json"
                if consensus.is_file() and json.loads(consensus.read_text()).get("status") == "skipped":
                    return True
                row = json.loads((case / "semantic" / f"{rid}.json").read_text(encoding="utf-8"))
                return "predictions_consensus_r3.csv" in str((row.get("digest") or {}).get("visual") or {})
            return all(visual_ready(rid) for rid in rows)
        return True
    return (case / "review" / "data_summary.json").is_file()


def _healthy(config: CaseConfig, step: str, rows: list[str]) -> bool:
    if step == "speech":
        return all(json.loads((config.case_dir / "asr" / f"{rid}.json").read_text()).get("status") != "ERROR"
                   for rid in rows)
    if step == "audio":
        return all(json.loads((config.case_dir / "audio" / f"{rid}.json").read_text()).get("status") != "error"
                   for rid in rows)
    if step == "visual":
        name = "consensus.json" if config.raw["models"]["visual"]["allow_image_upload"] else "local.json"
        return all(json.loads((config.vlm_dir / "r3" / rid / name).read_text()).get("status") in ("ok", "skipped")
                   for rid in rows)
    return True


def _ledger(config: CaseConfig):
    from .ledger.__main__ import run_case
    raw = config.raw
    legacy = {"case_id": config.case_id, "case": raw["ledger"]["case"],
              "workbook": str(config.path_of(raw["workbook"]["path"])), "workbook_spec": raw["workbook"],
              "video_dir": str(config.path_of(raw["evidence"]["video_dir"])),
              "screenshot_dir": str(config.path_of(raw["evidence"]["screenshot_dir"])),
              "videos": raw["evidence"]["videos"], "output_dir": str(config.case_dir),
              **{key: raw["ledger"][key] for key in ("fps", "probe_full_and_crop", "repair_nearby_identity",
                                                    "repair_truncated_readings")},
              **({"screenshot_time_pattern": raw["ledger"]["screenshot_time_pattern"]}
                 if raw["ledger"].get("screenshot_time_pattern") else {})}
    path = config.case_dir / "integration_ledger_config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
    run_case(path)


def _captions(config: CaseConfig, rows: list[str], force: bool = False):
    from .content.pipeline import run
    from .content.report import summary, write_pages, write_rows
    from .textindex.cli import main as textindex_main
    source = config.raw["source"]
    index = None
    if source["enabled"]:
        episode_dir = config.path_of(source["episode_dir"])
        if not episode_dir or not episode_dir.is_dir():
            raise FileNotFoundError("source.enabled requires source.episode_dir")
        if force or not (config.index_dir / "index.sqlite").is_file():
            args = ["--root", str(config.index_dir.parent), "build", "--episodes", str(episode_dir),
                    "--work", config.index_dir.name]
            if source.get("boundaries"):
                args.extend(["--boundaries", str(config.path_of(source["boundaries"]))])
            if force:
                args.append("--force")
            textindex_main(args)
        index = config.index_dir / "index.sqlite"
    selected = set(rows)
    new = run(config.case_dir, index, only=selected)
    out = config.case_dir / "content"
    # Restore full aggregate CSV after a row-scoped run; individual JSONs remain the source of truth.
    existing = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in out.glob("*.json")
                if p.stem in config.row_ids}
    existing.update({r["row_id"]: r for r in new})
    all_rows = [existing[rid] for rid in config.row_ids if rid in existing]
    write_rows(all_rows, out)
    pages = write_pages(new, config.case_dir, out, "ledger.jsonl")
    (out / "run_summary.json").write_text(json.dumps(summary(all_rows, {"index": str(index) if index else None,
                                                                "pages": pages}), ensure_ascii=False, indent=2))


def _speech(config: CaseConfig, rows: list[str], force: bool = False):
    from .asr import run as asr
    asr.configure(config)
    asr.run(set(rows), force=force)


def _audio(config: CaseConfig, rows: list[str], force: bool = False):
    from .audio.pipeline import run as audio_run
    audio_run(config, rows, force=force)


def _visual(config: CaseConfig, rows: list[str], force: bool = False):
    from .vlm import r3
    if config.raw["models"]["visual"]["protocol"] != "player_canvas_r3":
        raise ValueError("visual protocol needs its own module for this evidence")
    r3.configure(config)
    if force:
        for rid in rows:
            folder = config.vlm_dir / "r3" / rid
            for name in ("meta.json", "local.json", "glm-5.3-flash.json", "deepseek-flash.json", "consensus.json"):
                (folder / name).unlink(missing_ok=True)
    selected = set(rows)
    r3.prepare(config.case_dir, selected)
    r3.local(config.case_dir, selected)
    if config.raw["models"]["visual"]["allow_image_upload"]:
        r3.cloud(config.case_dir, selected)
        if _row_files(config.vlm_dir / "r3", config.row_ids, "/glm-5.3-flash.json") and \
           _row_files(config.vlm_dir / "r3", config.row_ids, "/deepseek-flash.json"):
            r3.finalize(config.case_dir)


def _classification(config: CaseConfig, rows: list[str], force: bool = False):
    from .semantic.case_runner import run_case
    model = config.raw["models"]["text"]
    backend = {"deepseek-flash": "deepseek", "glm-5.3-flash": "zhipu", "local": "local"}[model]
    key = {"deepseek": "DEEPSEEK_API_KEY", "zhipu": "ZHIPU_API_KEY"}.get(backend)
    if key and not os.environ.get(key):
        raise RuntimeError(f"{key} is absent; launch the command with zsh -lic")
    run_case(config, rows, force=force)


def _opinion(config: CaseConfig, rows: list[str], force: bool = False):
    from .opinion.case_runner import run_case
    model = config.raw["models"].get("opinion", config.raw["models"]["text"])
    backend = {"deepseek-flash": "deepseek", "glm-5.3-flash": "zhipu", "local": "local"}[model]
    key = {"deepseek": "DEEPSEEK_API_KEY", "zhipu": "ZHIPU_API_KEY"}.get(backend)
    if key and not os.environ.get(key):
        raise RuntimeError(f"{key} is absent; launch the command with zsh -lic")
    return run_case(config, rows, force=force)


def _review(config: CaseConfig):
    from .review.data import ReviewStore
    store = ReviewStore(config.case_dir, config=config)
    summaries = store.all_summaries()
    path = config.case_dir / "review" / "data_summary.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"case_id": config.case_id, "rows": summaries}, ensure_ascii=False, indent=1))


RUNNERS = {"ledger": _ledger, "captions": _captions, "speech": _speech, "audio": _audio, "visual": _visual,
           "classification": _classification, "opinion": _opinion, "review": _review}


def run(config: CaseConfig, from_step: str | None = None, requested_rows: str | None = None) -> dict:
    if from_step and from_step not in STEPS:
        raise ValueError(f"--from must be one of {', '.join(STEPS)}")
    rows = config.selected_rows(requested_rows)
    config.case_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = config.case_dir / "integration_manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    report = {"case_id": config.case_id, "selected_rows": rows, "steps": {}, "failed_rows": [],
              "yellow_flags": 0, "red_flags": 0, "red_flag_rows": [], "cloud_cost_usd": None,
              "unpriced_cloud_usage": None}
    force_from = STEPS.index(from_step) if from_step else len(STEPS)
    for i, step in enumerate(STEPS):
        start = time.monotonic()
        if step == "speech" and not config.raw["models"]["speech"]["enabled"]:
            report["steps"][step] = {"status": "disabled", "seconds": 0}
            continue
        if step == "audio" and not (config.raw["models"].get("audio") or {}).get("enabled"):
            report["steps"][step] = {"status": "disabled", "seconds": 0}
            continue
        fingerprint = _fingerprint(config, step, rows)
        key = step + ":" + ",".join(rows)
        ready = _ready(config, step, rows)
        unhealthy = ready and not _healthy(config, step, rows)
        stale = key in manifest and manifest[key] != fingerprint
        adopt = config.raw.get("cache", {}).get("adopt_existing") and key not in manifest and ready
        if i < force_from and ready and not unhealthy and (manifest.get(key) == fingerprint or adopt):
            status = "adopted" if adopt else "cached"
            manifest[key] = fingerprint
        else:
            if step == "ledger":
                if requested_rows and ready:
                    # Ledger OCR is organized by recording; retain the complete batch during row-scoped work.
                    status = "cached_batch"
                else:
                    RUNNERS[step](config)
                    status = "ran"
            elif step == "review":
                RUNNERS[step](config)
                status = "ran"
            else:
                RUNNERS[step](config, rows, force=i >= force_from or stale)
                status = "ran"
            if not _ready(config, step, rows):
                raise RuntimeError(f"{step}: selected-row outputs are incomplete")
            manifest[key] = _fingerprint(config, step, rows)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
        report["steps"][step] = {"status": status, "seconds": round(time.monotonic() - start, 2)}
        print(f"{step}: {status} ({report['steps'][step]['seconds']}s)", flush=True)
    from .review.data import ReviewStore
    store = ReviewStore(config.case_dir, config=config)
    summaries = [store.summary(rid) for rid in rows]
    report["yellow_flags"] = sum(item["yellow"] for item in summaries)
    report["red_flags"] = sum(item["red"] for item in summaries)
    report["red_flag_rows"] = [item["row_id"] for item in summaries if item["red"]]
    failures = set()
    for rid in rows:
        if config.raw["models"]["speech"]["enabled"]:
            path = config.case_dir / "asr" / f"{rid}.json"
            if path.exists() and json.loads(path.read_text(encoding="utf-8")).get("status") == "ERROR":
                failures.add(rid)
        if config.raw["models"]["visual"]["allow_image_upload"]:
            for model in ("glm-5.3-flash", "deepseek-flash"):
                path = config.vlm_dir / "r3" / rid / f"{model}.json"
                if path.exists() and json.loads(path.read_text(encoding="utf-8")).get("status") != "ok":
                    failures.add(rid)
        else:
            path = config.vlm_dir / "r3" / rid / "local.json"
            if path.exists() and json.loads(path.read_text(encoding="utf-8")).get("status") == "error":
                failures.add(rid)
        path = config.case_dir / "semantic" / f"{rid}.json"
        if path.exists():
            item = json.loads(path.read_text(encoding="utf-8"))
            if any(call.get("error") or not call.get("valid_json", True) for call in item.get("calls") or []):
                failures.add(rid)
            opinion = item.get("opinion") or {}
            if opinion.get("kind") == "draft" and not (opinion.get("validation") or {}).get("ok", True):
                failures.add(rid)
    report["failed_rows"] = sorted(failures, key=int)
    costs = {}
    cloud_csv = config.vlm_dir / "predictions_cloud_full.csv"
    if cloud_csv.exists():
        with cloud_csv.open(encoding="utf-8-sig", newline="") as handle:
            costs["visual_usd"] = round(sum(float(r.get("cost_usd") or 0) for r in csv.DictReader(handle)
                                            if r.get("row_id") in rows), 4)
    calls = []
    opinion_cost = 0.0
    unpriced_calls, unpriced_tokens = 0, {"prompt": 0, "completion": 0}
    for rid in rows:
        path = config.case_dir / "semantic" / f"{rid}.json"
        if path.exists():
            item = json.loads(path.read_text(encoding="utf-8"))
            calls.extend(item.get("calls") or [])
            opinion = item.get("opinion") or {}
            opinion_cost += float(opinion.get("usd") or 0)
            for call in opinion.get("calls") or []:
                if call.get("backend") == "zhipu":
                    unpriced_calls += 1
                    for key in unpriced_tokens:
                        unpriced_tokens[key] += (call.get("usage") or {}).get(key) or 0
    deepseek = [c for c in calls if c.get("backend") == "deepseek"]
    if deepseek:
        prompt = sum((c.get("usage") or {}).get("prompt") or 0 for c in deepseek)
        completion = sum((c.get("usage") or {}).get("completion") or 0 for c in deepseek)
        # Cache-miss peak-hour tariff from semantic.compare; actual billed amount may be lower.
        costs["classification_upper_usd"] = round((prompt * .30 + completion * 1.20) / 1e6, 4)
    if opinion_cost:
        costs["opinion_usd"] = round(opinion_cost, 4)
    if unpriced_calls:
        report["unpriced_cloud_usage"] = {"model": "glm-5.3-flash", "opinion_calls": unpriced_calls,
                                          "tokens": unpriced_tokens, "note": "金额以平台账单为准"}
    if costs:
        costs["known_or_upper_total_usd"] = round(sum(costs.values()), 4)
        report["cloud_cost_usd"] = costs
    (config.case_dir / "integration_summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"摘要：{config.case_id}，本次范围 {len(rows)} 行")
    print("步骤耗时：" + "；".join(f"{name} {item['status']} {item['seconds']}s"
                             for name, item in report["steps"].items()))
    print(f"失败行（{len(report['failed_rows'])}）：{'、'.join(report['failed_rows']) or '无'}")
    print(f"旗标：黄 {report['yellow_flags']}，红 {report['red_flags']}（涉及 {len(report['red_flag_rows'])} 行）")
    print("云端费用（USD，已记录/上限估算，不含未定价 GLM）：" + (json.dumps(costs, ensure_ascii=False) if costs else "无"))
    if report["unpriced_cloud_usage"]:
        print("未计价云端用量：" + json.dumps(report["unpriced_cloud_usage"], ensure_ascii=False))
    print(f"明细：{config.case_dir / 'integration_summary.json'}")
    return report
