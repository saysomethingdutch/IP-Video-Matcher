"""After WO-008 evidence completes: classify, write opinions, freeze, then blind-export."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil

from .case_config import CaseConfig
from .integration import _classification, _opinion
from .robustness import freeze, generate, run_deepseek_opinions


def _evidence_ready(config: CaseConfig) -> None:
    rows = [json.loads(line) for line in (config.case_dir / "ledger.jsonl").read_text().splitlines() if line]
    if {row["row_id"] for row in rows} != set(config.row_ids):
        raise RuntimeError(f"{config.case_id}: ledger rows are incomplete")
    for rid in config.row_ids:
        audio = config.case_dir / "audio" / f"{rid}.json"
        visual_dir = config.vlm_dir / "r3" / rid
        visual = visual_dir / "consensus.json"
        audio_result = json.loads(audio.read_text()) if audio.is_file() else {}
        if audio_result.get("status") not in ("ok", "skipped", "whisper_thai") or \
           audio_result.get("rule_version") != "D-043-audio-v2":
            raise RuntimeError(f"{config.case_id}: audio incomplete at {rid}")
        if not visual.is_file() or json.loads(visual.read_text()).get("status") not in ("ok", "skipped"):
            raise RuntimeError(f"{config.case_id}: visual incomplete at {rid}")
        for name in ("local", "glm-5.3-flash", "deepseek-flash"):
            path = visual_dir / f"{name}.json"
            if not path.is_file() or json.loads(path.read_text()).get("status") not in ("ok", "skipped"):
                raise RuntimeError(f"{config.case_id}: {name} visual incomplete at {rid}")
        if not (config.case_dir / "content" / f"{rid}.json").is_file():
            raise RuntimeError(f"{config.case_id}: captions incomplete at {rid}")
    (config.case_dir / "local_stage_complete.json").write_text(json.dumps(
        {"case_id": config.case_id, "rows": len(rows), "audio": True, "visual": True},
        ensure_ascii=False, indent=1), encoding="utf-8")


def _preserve_old_blind(config: CaseConfig) -> None:
    root = config.case_dir / "robustness"
    target = root / "wo007_baseline"
    target.mkdir(parents=True, exist_ok=True)
    for name in ("machine_freeze.json", "blind_opinions.md", "blind_scores.csv",
                 "key.json", "summary.json", "grid_compare.csv"):
        original = root / name
        copy = target / name
        if original.is_file() and not copy.exists():
            shutil.copy2(original, copy)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", nargs="+", default=["cases/qsjx_4086.yaml", "cases/qsjx_2591.yaml"])
    args = parser.parse_args()
    pilot = CaseConfig("cases/qsjx.yaml")
    validation = pilot.case_dir / "audio" / "validation_d043.json"
    if not validation.is_file():
        raise RuntimeError("D-043 pilot confusion table is missing; classification remains gated")
    counts = json.loads(validation.read_text(encoding="utf-8")).get("counts") or {}
    if counts.get("TP", 0) + counts.get("FN", 0) < 20 or counts.get("TN", 0) + counts.get("FP", 0) < 70:
        raise RuntimeError("D-043 pilot validation is incomplete; classification remains gated")
    cases = [CaseConfig(path) for path in args.cases]
    for config in cases:
        _evidence_ready(config)
        print(f"WO-008 {config.case_id}: classification and GLM opinions", flush=True)
        _classification(config, config.row_ids, force=True)
        _opinion(config, config.row_ids, force=True)
        print(f"WO-008 {config.case_id}: DeepSeek opinions", flush=True)
        run_deepseek_opinions(config)
    # Freeze *both* machine batches before the first read of any held-out U–AJ cell.
    for config in cases:
        _preserve_old_blind(config)
        record = freeze(config)
        print(f"WO-008 {config.case_id}: frozen {len(record['machine_sha256'])} machine files", flush=True)
    for config in cases:
        summary = generate(config)
        print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
