"""Rerun WO-008 evidence stages, then stop before classification/opinions."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil

from .case_config import CaseConfig
from .integration import _captions, _ledger, _visual
from .audio.pipeline import run as run_audio


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", nargs="+", default=["cases/qsjx_4086.yaml", "cases/qsjx_2591.yaml"])
    args = parser.parse_args()
    for path in args.cases:
        config = CaseConfig(path)
        print(f"WO-008 {config.case_id}: ledger", flush=True)
        old = config.case_dir / "ledger.jsonl"
        baseline = config.case_dir / "ledger_wo007_baseline.jsonl"
        if old.is_file() and not baseline.exists():
            shutil.copy2(old, baseline)
        _ledger(config)
        print(f"WO-008 {config.case_id}: captions", flush=True)
        _captions(config, config.row_ids)
        print(f"WO-008 {config.case_id}: local audio", flush=True)
        audio = run_audio(config, config.row_ids)
        if audio["error"]:
            raise RuntimeError(f"{config.case_id}: {audio['error']} audio rows failed")
        print(f"WO-008 {config.case_id}: local and cloud visual", flush=True)
        _visual(config, config.row_ids)
        print(json.dumps({"case_id": config.case_id, "audio": audio, "status": "evidence_complete"},
                         ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
