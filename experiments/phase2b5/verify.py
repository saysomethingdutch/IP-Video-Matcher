#!/usr/bin/env python3
"""Independent invariant checks for Phase 2B-5 artifacts."""

from __future__ import annotations

import argparse
import ast
import inspect
import json
from pathlib import Path
import textwrap
from typing import Any

from . import policies
from .aligner import ExperimentalExtractionAligner
from .benchmark import (
    BACKENDS, POLICIES, PROTOCOL_COMMIT, ROOT, _baseline_equal,
    _eligibility, _selection_key, run_synthetic, validate_dev, verify_stage,
)
from .reporting import _synthetic_summary, build_aggregate, validate_sanitized


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _static_runtime_audit() -> None:
    forbidden = {
        "truth", "ground_truth", "expected_source", "expected_speed",
        "complete", "is_correct",
    }
    functions = (
        policies.choose_fit,
        ExperimentalExtractionAligner._extract_episode_segments,
        ExperimentalExtractionAligner._line_band_occupancy,
        ExperimentalExtractionAligner._inlier_occupancy,
    )
    for function in functions:
        tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        arguments = {
            item.arg
            for node in ast.walk(tree) if isinstance(node, ast.arguments)
            for item in (*node.posonlyargs, *node.args, *node.kwonlyargs)
        }
        if forbidden & (names | arguments):
            raise ValueError(f"evaluator leakage token in runtime function: {function.__name__}")


def verify(
    raw_path: Path,
    synthetic_path: Path,
    aggregate_path: Path,
    dev_path: Path,
    selected_path: Path,
) -> dict[str, Any]:
    verify_stage(dev_path)
    raw = _read(raw_path)
    synthetic = _read(synthetic_path)
    aggregate = _read(aggregate_path)
    if aggregate != build_aggregate(raw, synthetic):
        raise ValueError("committed aggregate does not exactly reproduce")
    validate_sanitized(aggregate)
    _static_runtime_audit()
    if _synthetic_summary(run_synthetic()) != _synthetic_summary(synthetic):
        raise ValueError("synthetic characterization is not deterministic")

    source_rows = validate_dev(_read(dev_path))
    source_index = {(item["case_id"], item["backend"]): item for item in source_rows}
    p0_rows = [item for item in raw["rows"] if item["policy_id"] == "P0"]
    if len(p0_rows) != 480 or not all(
        _baseline_equal(source_index[(item["case_id"], item["backend"])], item)
        for item in p0_rows
    ):
        raise ValueError("P0 does not exactly replay all stored DEV rows")
    if len(raw["rows"]) != len(POLICIES) * len(BACKENDS) * 240:
        raise ValueError("seven-policy paired DEV matrix is incomplete")
    if not all(
        mapping["review_tier"] == "REVIEW_REQUIRED"
        for row in raw["rows"]
        for mapping in row["predictions"] + row["alternatives"]
    ):
        raise ValueError("production REVIEW_REQUIRED invariant was not preserved")

    candidate_count = 0
    off_grid_count = 0
    for item in source_rows:
        for candidate in item.get("candidate_audit", []):
            candidate_count += 1
            scaled = float(candidate["source_time"]) * 2.0
            off_grid_count += int(abs(scaled - round(scaled)) > 1e-6)
    if off_grid_count:
        raise ValueError("registered DEV contains off-grid candidate timestamps")

    reproduced_eligibility = _eligibility(raw["overall"], synthetic)
    if reproduced_eligibility != raw["eligibility"]:
        raise ValueError("eligibility does not reproduce")
    qualified = sorted(key for key, value in reproduced_eligibility.items() if value["eligible"])
    if qualified != sorted(raw["qualified_policies"]):
        raise ValueError("qualified policy set does not reproduce")
    if qualified:
        expected = min(
            qualified,
            key=lambda key: _selection_key(raw["overall"]["TAU_0.72"][key], key),
        )
        if raw["selected_experimental_policy"] != expected or not selected_path.exists():
            raise ValueError("selected policy artifact missing or inconsistent")
    elif raw["selected_experimental_policy"] is not None or selected_path.exists():
        raise ValueError("no policy qualified, but a selected artifact exists")
    if not qualified:
        expected_best = min(
            (item.policy_id for item in POLICIES[1:]),
            key=lambda key: _selection_key(raw["overall"]["TAU_0.72"][key], key),
        )
        if raw["best_policy_if_none_qualified"] != expected_best:
            raise ValueError("best diagnostic policy does not reproduce")

    a010_h264 = aggregate["a010_controls"]["TAU_0.72"]
    result = {
        "schema_version": "phase2b5-verification-v0.1",
        "protocol_commit": PROTOCOL_COMMIT,
        "protocol_unchanged": True,
        "production_diff_empty": True,
        "dev_only": True,
        "fresh_data_absent": True,
        "selected_score_contract_absent": True,
        "dev_positive_cell_count_per_backend": 220,
        "dev_negative_query_count_per_backend": 20,
        "content_position_count": 20,
        "p0_replay_case_count": len(p0_rows),
        "p0_replay_exact": True,
        "candidate_grid_check": {
            "candidate_count": candidate_count,
            "off_grid_count": off_grid_count,
        },
        "synthetic_reproducer_valid": synthetic["baseline_reproducer"]["valid"],
        "synthetic_replay_exact": True,
        "synthetic_eligible_policies": sorted(
            key for key, value in synthetic["policy_eligibility"].items() if value["eligible"]
        ),
        "a010_h264_safe_policies": sorted(
            key for key, value in a010_h264.items() if value["H264_REENCODE"]["safe"]
        ),
        "qualified_policies": qualified,
        "selected_experimental_policy": raw["selected_experimental_policy"],
        "eligibility_reproduced": True,
        "selection_reproduced": True,
        "gt_leakage_static_audit_passed": True,
        "aggregate_reproduction_exact": True,
        "sanitization_passed": True,
        "production_review_required_preserved": True,
    }
    validate_sanitized(result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", type=Path, default=ROOT / "workspace/phase2b5/dev.json")
    parser.add_argument("--synthetic", type=Path, default=ROOT / "workspace/phase2b5/synthetic.json")
    parser.add_argument(
        "--aggregate", type=Path,
        default=ROOT / "benchmark/visual/results/phase2b5-extraction-remediation-v0.1.json",
    )
    parser.add_argument("--dev", type=Path, default=ROOT / "workspace/phase2b3/dev.json")
    parser.add_argument(
        "--selected", type=Path,
        default=ROOT / "workspace/phase2b5/selected-experimental-policy.json",
    )
    parser.add_argument(
        "--output", type=Path,
        default=ROOT / "benchmark/visual/results/phase2b5-verification-v0.1.json",
    )
    args = parser.parse_args()
    _write(args.output, verify(args.raw, args.synthetic, args.aggregate, args.dev, args.selected))
    print(args.output)


if __name__ == "__main__":
    main()
