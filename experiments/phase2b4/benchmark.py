#!/usr/bin/env python3
"""Phase 2B-4 DEV-only temporal-separability characterization."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import UTC, datetime
import json
from pathlib import Path
import subprocess
import time
from typing import Any, Iterable

import numpy as np

from ipvm.config import MatchingConfig

from .metrics import (
    actual_fit_diagnostics,
    mapping_evaluation,
    oracle_band_diagnostics,
    sanitized_fit,
    segment_from_dict,
    temporal_separability_metrics,
)
from .synthetic import run_synthetic_grid
from .trace import classify_first_loss, sanitized_trace, trace_case


ROOT = Path(__file__).resolve().parents[2]
PROTOCOL_COMMIT = "79decff3270c028056f34fdd11902545fad0b74a"
INITIAL_PROTOCOL_COMMIT = "59a834a"
START_COMMIT = "78123a295a3f025cb728c0ace84a9c52520a9941"
PRODUCTION_COMMIT = "93bf13b"
REFERENCE_POLICY = "TAU_0.72"
PRIMARY_TRANSFORMS = ("DIRECT", "H264_REENCODE", "SPEED_0_8X")
PRIMARY_CASES = tuple(f"SOURCE_A-POSITION_010-{name}" for name in PRIMARY_TRANSFORMS)
SIDEBAR_CASE = "SOURCE_A-POSITION_001-VERTICAL_9_16"


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def verify_stage(dev_path: Path) -> None:
    if subprocess.run(
        ["git", "merge-base", "--is-ancestor", START_COMMIT, "HEAD"], cwd=ROOT,
        check=False,
    ).returncode:
        raise ValueError("Phase 2B-4 start commit is not an ancestor")
    paths = _git("diff-tree", "--no-commit-id", "--name-only", "-r", PROTOCOL_COMMIT).splitlines()
    if paths != ["experiments/phase2b4/PROTOCOL.md"]:
        raise ValueError("protocol correction commit must contain only PROTOCOL.md")
    initial_paths = _git(
        "diff-tree", "--no-commit-id", "--name-only", "-r", INITIAL_PROTOCOL_COMMIT
    ).splitlines()
    if initial_paths != ["experiments/phase2b4/PROTOCOL.md"]:
        raise ValueError("initial protocol commit must contain only PROTOCOL.md")
    if _git("diff", PROTOCOL_COMMIT, "--", "experiments/phase2b4/PROTOCOL.md"):
        raise ValueError("pre-registered Phase 2B-4 protocol changed")
    if _git("diff", PRODUCTION_COMMIT, "--", "src/", "config/"):
        raise ValueError("frozen Phase 2A production/config changed")
    phase2b3_workspace = dev_path.resolve().parent
    forbidden = [
        path for path in phase2b3_workspace.rglob("*")
        if path.is_file() and any(
            token in path.name.lower()
            for token in ("selected-score-contract", "fresh", "held-out", "held_out")
        )
    ]
    if forbidden:
        raise ValueError("fresh/selected Phase 2B-3 artifact exists; characterization refused")


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_exclusive(path: Path, payload: Any) -> None:
    resolved = path.resolve()
    if not resolved.is_relative_to((ROOT / "workspace").resolve()):
        raise ValueError("raw Phase 2B-4 output must remain under ignored workspace")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")


def _complete_final(row: dict[str, Any]) -> bool:
    truth = row["truth"][0]
    return any(
        mapping_evaluation(segment_from_dict(item), truth)["complete"]
        for item in row["predictions"] + row["alternatives"]
    )


def _indicator_values(metrics: dict[str, Any]) -> dict[str, float | None]:
    order = metrics["gt_nearest_order"]
    return {
        "correct_source_maximum_multiplicity": metrics["repetition_correct_source"]["maximum_multiplicity"],
        "shared_top1_fraction": metrics["shared_top1_fraction"],
        "correct_source_effective_diversity": metrics["correct_source_local_entropy"]["mean_effective_diversity"],
        "gt_nearest_inversion_plus_tie_rate": order["inversion_plus_tie_rate"],
    }


def _separability_comparison(dino: dict[str, Any], dct: dict[str, Any]) -> dict[str, Any]:
    dv, cv = _indicator_values(dino), _indicator_values(dct)
    directions = {
        "correct_source_maximum_multiplicity": "higher",
        "shared_top1_fraction": "higher",
        "correct_source_effective_diversity": "lower",
        "gt_nearest_inversion_plus_tie_rate": "higher",
    }
    worse: dict[str, bool | None] = {}
    strict = 0
    for key, direction in directions.items():
        left, right = dv[key], cv[key]
        if left is None or right is None:
            worse[key] = None
            continue
        is_worse = left > right if direction == "higher" else left < right
        worse[key] = bool(is_worse)
        strict += int(is_worse)
    return {
        "dino": dv, "dct": cv, "directionally_worse": worse,
        "worse_indicator_count": strict,
        "materially_directionally_worse": strict >= 3,
    }


def _mechanism(
    *, final_complete: bool, oracle: dict[str, Any], trace_summary: dict[str, Any],
    comparison: dict[str, Any],
) -> dict[str, Any]:
    oracle_complete = bool(
        oracle["nearest_per_frame_fit"].get("evaluation", {}).get("complete")
    )
    proposal_complete = trace_summary["complete_gt_free_proposal_count"] > 0
    extraction_loss = proposal_complete and not final_complete
    representation_limit = bool(
        not final_complete and (
            not oracle_complete
            or (
                oracle_complete and not proposal_complete
                and comparison["materially_directionally_worse"]
            )
        )
    )
    label = (
        "MIXED" if extraction_loss and representation_limit
        else "EXTRACTOR_COMPATIBILITY" if extraction_loss
        else "REPRESENTATION_SEPARABILITY" if representation_limit
        else "NEITHER_OR_UNRESOLVED"
    )
    return {
        "extraction_loss": extraction_loss,
        "representation_separability_limit": representation_limit,
        "mechanism_cell": label,
        "oracle_nearest_complete": oracle_complete,
        "gt_free_complete_proposal": proposal_complete,
        "final_complete": final_complete,
    }


def _quartiles(values: Iterable[float]) -> dict[str, Any]:
    array = np.asarray(list(values), dtype=np.float64)
    if not len(array):
        return {"count": 0, "median": None, "q1": None, "q3": None}
    return {
        "count": int(len(array)), "median": float(np.median(array)),
        "q1": float(np.percentile(array, 25)), "q3": float(np.percentile(array, 75)),
    }


def _group_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def vals(path: tuple[str, ...]) -> list[float]:
        output = []
        for row in rows:
            value: Any = row
            for key in path:
                value = value.get(key) if isinstance(value, dict) else None
            if value is not None:
                output.append(float(value))
        return output

    return {
        "case_count": len(rows),
        "content_position_count": len({row["position_id"] for row in rows}),
        "outcomes": dict(Counter(row["outcome"] for row in rows)),
        "first_loss_stages": dict(Counter(row["first_loss_stage"] for row in rows)),
        "mechanism_cells": dict(Counter(row["mechanism"]["mechanism_cell"] for row in rows)),
        "extraction_loss_count": sum(row["mechanism"]["extraction_loss"] for row in rows),
        "representation_limit_count": sum(
            row["mechanism"]["representation_separability_limit"] for row in rows
        ),
        "metrics": {
            "correct_source_maximum_multiplicity": _quartiles(vals(("indicators", "correct_source_maximum_multiplicity"))),
            "shared_top1_fraction": _quartiles(vals(("indicators", "shared_top1_fraction"))),
            "correct_source_effective_diversity": _quartiles(vals(("indicators", "correct_source_effective_diversity"))),
            "gt_nearest_inversion_plus_tie_rate": _quartiles(vals(("indicators", "gt_nearest_inversion_plus_tie_rate"))),
            "oracle_nearest_slope": _quartiles(vals(("oracle_nearest_slope",))),
            "oracle_nearest_rms_seconds": _quartiles(vals(("oracle_nearest_rms_seconds",))),
        },
    }


def _measurement_group_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def values(key: str) -> list[float]:
        return [float(row["indicators"][key]) for row in rows if row["indicators"].get(key) is not None]

    return {
        "case_count": len(rows),
        "content_position_count": len({row["position_id"] for row in rows}),
        "outcomes": dict(Counter(row["outcome"] for row in rows)),
        "metrics": {
            key: _quartiles(values(key))
            for key in (
                "correct_source_maximum_multiplicity", "shared_top1_fraction",
                "correct_source_effective_diversity", "gt_nearest_inversion_plus_tie_rate",
            )
        },
        "oracle_nearest_slope": _quartiles(
            float(row["oracle_nearest_slope"]) for row in rows
            if row.get("oracle_nearest_slope") is not None
        ),
        "oracle_nearest_rms_seconds": _quartiles(
            float(row["oracle_nearest_rms_seconds"]) for row in rows
            if row.get("oracle_nearest_rms_seconds") is not None
        ),
    }


def _actual_alternatives(
    row: dict[str, Any], trace: dict[str, Any], oracle: dict[str, Any]
) -> list[dict[str, Any]]:
    truth = row["truth"][0]
    candidate_identity = {
        item["candidate_id"]: item for item in trace["candidate_identity"]
    }
    output = []
    for item in trace["alternative_membership"]:
        alternative = item["alternative"]
        if alternative["source_episode"] != truth["source_episode"]:
            continue
        diagnostic = actual_fit_diagnostics(
            alternative, truth, oracle["nearest_per_frame_fit"]
        )
        proposal_matches = item["proposal_matches"]
        if proposal_matches:
            ids = proposal_matches[0]["inlier_candidate_ids"]
            members = [candidate_identity[candidate_id] for candidate_id in ids]
            diagnostic["distinct_accused_frames"] = len({member["accused_time"] for member in members})
            diagnostic["distinct_source_frames"] = len({member["source_time"] for member in members})
            diagnostic["duplicate_source_concentration"] = (
                1.0 - diagnostic["distinct_source_frames"] / len(members) if members else None
            )
            diagnostic["candidate_ids"] = ids
        output.append(diagnostic)
    return output


def run(dev_path: Path) -> dict[str, Any]:
    verify_stage(dev_path)
    dev = _read(dev_path)
    eligibility = dev.get("eligibility", {})
    if (
        dev.get("stage") != "DEV"
        or len(eligibility) != 8
        or any(item.get("eligible") is not False for item in eligibility.values())
    ):
        raise ValueError("expected closed no-eligible-policy Phase 2B-3 DEV input")
    positives = [row for row in dev["cases"] if row.get("positive")]
    selected = [row for row in positives if row["backend"] in {"DCT", REFERENCE_POLICY}]
    if len(selected) != 440:
        raise ValueError("expected 220 paired DCT/reference-policy positive cells")
    indexed = {(row["case_id"], row["backend"]): row for row in selected}

    started = time.perf_counter()
    metric_cache: dict[tuple[str, str], dict[str, Any]] = {}
    oracle_cache: dict[tuple[str, str], dict[str, Any]] = {}
    for row in selected:
        key = (row["case_id"], row["backend"])
        truth = row["truth"][0]
        metric_cache[key] = temporal_separability_metrics(row["candidate_audit"], truth)
        oracle_cache[key] = oracle_band_diagnostics(row["candidate_audit"], truth)

    # Full raw traces are retained only for the six pre-registered primary controls.
    primary_traces: dict[str, dict[str, Any]] = {}
    primary: list[dict[str, Any]] = []
    for case_id in PRIMARY_CASES:
        for backend in ("DCT", REFERENCE_POLICY):
            row = indexed[(case_id, backend)]
            trace = trace_case(row)
            oracle = oracle_cache[(case_id, backend)]
            key = f"{case_id}::{backend}"
            primary_traces[key] = trace
            primary.append({
                "case_id": case_id,
                "backend": backend,
                "outcome": row["outcome"],
                "status": row["status"],
                "metrics": metric_cache[(case_id, backend)],
                "oracle": oracle,
                "trace_summary": sanitized_trace(trace, oracle),
                "actual_correct_source_alternatives": _actual_alternatives(row, trace, oracle),
            })

    # All DINO reference cases are externally replayed; only bounded summaries persist.
    broader: list[dict[str, Any]] = []
    for ordinal, row in enumerate(
        sorted((item for item in selected if item["backend"] == REFERENCE_POLICY), key=lambda item: item["case_id"]),
        1,
    ):
        case_id = row["case_id"]
        dct = indexed[(case_id, "DCT")]
        dino_metrics = metric_cache[(case_id, REFERENCE_POLICY)]
        dct_metrics = metric_cache[(case_id, "DCT")]
        oracle = oracle_cache[(case_id, REFERENCE_POLICY)]
        trace = primary_traces.get(f"{case_id}::{REFERENCE_POLICY}") or trace_case(row)
        trace_summary = sanitized_trace(trace, oracle)
        comparison = _separability_comparison(dino_metrics, dct_metrics)
        final_complete = _complete_final(row)
        mechanism = _mechanism(
            final_complete=final_complete, oracle=oracle,
            trace_summary=trace_summary, comparison=comparison,
        )
        broader.append({
            "case_id": case_id,
            "source_alias": row["source_alias"],
            "position_id": row["position_id"],
            "normalized_source_start": row["normalized_source_start"],
            "transformation": row["transformation"],
            "outcome": row["outcome"],
            "failure_attribution": row.get("phase2b3_failure_category"),
            "boundary_assertion_outcome": row.get("boundary_assertion_outcome"),
            "indicators": _indicator_values(dino_metrics),
            "dct_indicators": _indicator_values(dct_metrics),
            "comparison": comparison,
            "oracle_nearest_slope": oracle["nearest_per_frame_fit"].get("slope"),
            "oracle_nearest_rms_seconds": oracle["nearest_per_frame_fit"].get("rms_residual_seconds"),
            "first_loss_stage": classify_first_loss(trace, oracle),
            "mechanism": mechanism,
            "dct_outcome": dct["outcome"],
        })
        if ordinal % 40 == 0:
            print(f"traced {ordinal}/220 DEV DINO cells", flush=True)

    by_outcome = {
        name: _group_summary([row for row in broader if row["outcome"] == name])
        for name in sorted({row["outcome"] for row in broader})
    }
    by_failure = {
        str(name): _group_summary([row for row in broader if row["failure_attribution"] == name])
        for name in sorted({row["failure_attribution"] for row in broader}, key=lambda value: str(value))
    }
    by_transform = {
        name: _group_summary([row for row in broader if row["transformation"] == name])
        for name in sorted({row["transformation"] for row in broader})
    }
    backend_metric_summaries: dict[str, Any] = {}
    for backend in ("DCT", REFERENCE_POLICY):
        measurement_rows = []
        for row in selected:
            if row["backend"] != backend:
                continue
            oracle = oracle_cache[(row["case_id"], backend)]
            measurement_rows.append({
                "case_id": row["case_id"], "position_id": row["position_id"],
                "transformation": row["transformation"], "outcome": row["outcome"],
                "indicators": _indicator_values(metric_cache[(row["case_id"], backend)]),
                "oracle_nearest_slope": oracle["nearest_per_frame_fit"].get("slope"),
                "oracle_nearest_rms_seconds": oracle["nearest_per_frame_fit"].get("rms_residual_seconds"),
            })
        backend_metric_summaries[backend] = {
            "overall": _measurement_group_summary(measurement_rows),
            "by_outcome": {
                name: _measurement_group_summary([
                    item for item in measurement_rows if item["outcome"] == name
                ])
                for name in sorted({item["outcome"] for item in measurement_rows})
            },
            "by_transformation": {
                name: _measurement_group_summary([
                    item for item in measurement_rows if item["transformation"] == name
                ])
                for name in sorted({item["transformation"] for item in measurement_rows})
            },
        }
    positions = []
    for position_id in sorted({row["position_id"] for row in broader}):
        rows = [row for row in broader if row["position_id"] == position_id]
        positions.append({
            "position_id": position_id,
            "source_alias": rows[0]["source_alias"],
            "normalized_source_start": rows[0]["normalized_source_start"],
            "cell_count": len(rows),
            "failed_cell_count": sum(not row["mechanism"]["final_complete"] for row in rows),
            "extraction_loss_cell_count": sum(row["mechanism"]["extraction_loss"] for row in rows),
            "representation_limit_cell_count": sum(
                row["mechanism"]["representation_separability_limit"] for row in rows
            ),
            "mixed_cell_count": sum(row["mechanism"]["mechanism_cell"] == "MIXED" for row in rows),
        })

    # Known invariance and safety sidebar use existing DEV rows only.
    invariant_rows = [
        row for row in positives
        if row["case_id"] == "SOURCE_A-POSITION_010-H264_REENCODE"
        and row["backend"] in {"TAU_0.68", "TAU_0.72", "TAU_0.82"}
    ]
    invariance = [{
        "backend": row["backend"], "outcome": row["outcome"], "status": row["status"],
        "candidate_count": row["input_candidate_count"],
        "effective_candidate_count": row["effective_candidate_count"],
        "correct_support": row["post_threshold_support"]["actual_supported_frame_count"],
        "alternative_slopes": [
            (item["source_end"] - item["source_start"]) / (item["accused_end"] - item["accused_start"])
            for item in row["alternatives"] if item["source_episode"] == row["truth"][0]["source_episode"]
        ],
    } for row in invariant_rows]
    sidebar = []
    for backend in ("DCT", "TAU_0.68", "TAU_0.72"):
        row = next(item for item in positives if item["case_id"] == SIDEBAR_CASE and item["backend"] == backend)
        sidebar.append({
            "backend": backend, "outcome": row["outcome"], "status": row["status"],
            "candidate_tiers": [item["candidate_tier"] for item in row["predictions"]],
            "review_tiers": [item["review_tier"] for item in row["predictions"]],
            "metrics": temporal_separability_metrics(row["candidate_audit"], row["truth"][0]),
        })

    synthetic = run_synthetic_grid()
    return {
        "schema_version": "phase2b4-raw-v0.1",
        "completed_at": datetime.now(UTC).isoformat(),
        "protocol_commit": PROTOCOL_COMMIT,
        "start_commit": START_COMMIT,
        "production_commit": PRODUCTION_COMMIT,
        "reference_policy": REFERENCE_POLICY,
        "data_policy": {
            "dev_only": True, "positive_cell_count": 220,
            "content_position_count": 20, "source_episode_count": 2,
            "fresh_data_exists": False, "selected_policy_exists": False,
        },
        "primary": primary,
        "primary_raw_traces": primary_traces,
        "tau_invariance": invariance,
        "broader_dev": {
            "rows": broader, "overall": _group_summary(broader),
            "backend_metric_summaries": backend_metric_summaries,
            "by_outcome": by_outcome, "by_failure_attribution": by_failure,
            "by_transformation": by_transform, "position_matrix": positions,
        },
        "synthetic": synthetic,
        "safety_sidebar": sidebar,
        "runtime_seconds": time.perf_counter() - started,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dev", type=Path, default=ROOT / "workspace/phase2b3/dev.json")
    parser.add_argument("--output", type=Path, default=ROOT / "workspace/phase2b4/raw.json")
    args = parser.parse_args()
    _write_exclusive(args.output, run(args.dev))
    print(args.output)


if __name__ == "__main__":
    main()
