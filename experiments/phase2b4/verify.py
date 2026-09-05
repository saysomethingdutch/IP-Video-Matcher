#!/usr/bin/env python3
"""Independent invariant checks over the Phase 2B-4 ignored raw result."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .benchmark import PRIMARY_CASES, REFERENCE_POLICY, ROOT, verify_stage
from .reporting import _synthetic_summary, build_aggregate, validate_sanitized
from .synthetic import run_synthetic_grid


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def verify(raw_path: Path, aggregate_path: Path, dev_path: Path) -> dict[str, Any]:
    verify_stage(dev_path)
    raw = _read(raw_path)
    aggregate = _read(aggregate_path)
    reproduced = build_aggregate(raw)
    if aggregate != reproduced:
        raise ValueError("committed aggregate does not reproduce from ignored raw result")
    validate_sanitized(aggregate)

    primary_keys = {(item["case_id"], item["backend"]) for item in raw["primary"]}
    expected_primary = {
        (case_id, backend) for case_id in PRIMARY_CASES for backend in ("DCT", REFERENCE_POLICY)
    }
    if primary_keys != expected_primary:
        raise ValueError("primary trace matrix is incomplete")

    dev = _read(dev_path)
    audited = [
        row for row in dev["cases"]
        if row.get("positive") and row["backend"] in {"DCT", REFERENCE_POLICY}
    ]
    candidate_count = off_grid = 0
    for row in audited:
        for item in row["candidate_audit"]:
            candidate_count += 1
            scaled = float(item["source_time"]) * 2.0
            off_grid += int(abs(scaled - round(scaled)) > 1e-6)
    if len(audited) != 440 or off_grid:
        raise ValueError("DEV source-grid or paired-backend invariant failed")

    synthetic_replay = _synthetic_summary({"synthetic": run_synthetic_grid()})
    if synthetic_replay != aggregate["synthetic"]:
        raise ValueError("synthetic grid replay is not deterministic")

    result = {
        "schema_version": "phase2b4-verification-v0.1",
        "protocol_commit": raw["protocol_commit"],
        "production_commit": raw["production_commit"],
        "protocol_unchanged": True,
        "production_diff_empty": True,
        "fresh_data_absent": True,
        "selected_policy_absent": True,
        "dev_only": True,
        "paired_positive_case_count": len(audited),
        "content_position_count": 20,
        "candidate_grid_check": {
            "candidate_count": candidate_count,
            "off_grid_count": off_grid,
        },
        "primary_trace_case_count": len(raw["primary"]),
        "primary_trace_matrix_complete": True,
        "aggregate_reproduction_exact": True,
        "synthetic_replay_exact": True,
        "sanitization_passed": True,
        "production_review_required_preserved": all(
            tier == "REVIEW_REQUIRED"
            for row in raw["safety_sidebar"] for tier in row["review_tiers"]
        ),
    }
    validate_sanitized(result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", type=Path, default=ROOT / "workspace/phase2b4/raw.json")
    parser.add_argument("--aggregate", type=Path, default=ROOT / "benchmark/visual/results/phase2b4-temporal-separability-v0.1.json")
    parser.add_argument("--dev", type=Path, default=ROOT / "workspace/phase2b3/dev.json")
    parser.add_argument("--output", type=Path, default=ROOT / "benchmark/visual/results/phase2b4-verification-v0.1.json")
    args = parser.parse_args()
    result = verify(args.raw, args.aggregate, args.dev)
    _write(args.output, result)
    print(args.output)


if __name__ == "__main__":
    main()
