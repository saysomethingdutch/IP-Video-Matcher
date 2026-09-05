#!/usr/bin/env python3
"""Sanitized aggregate exporter for Phase 2B-4."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Any

from .benchmark import PRIMARY_CASES, PRODUCTION_COMMIT, PROTOCOL_COMMIT, ROOT
from .metrics import sanitized_fit
from .trace import sanitized_trace


FORBIDDEN_KEYS = {
    "path", "sha256", "frames", "per_frame", "truth", "predictions", "alternatives",
    "candidate_audit", "candidate_identity", "candidate_ids", "source_time", "accused_time",
    "source_start", "source_end", "intercept", "raw_candidates", "embedding", "model_path",
    "index_path", "primary_raw_traces", "rows",
}
FORBIDDEN_TEXT = ("/Users/", "real_test", ".mp4", ".mkv", ".mov", ".onnx", "workspace/phase2b")
PRIVATE_HASH = re.compile(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])", re.IGNORECASE)


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def _safe_actual(value: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "slope", "intercept_error_seconds", "rms_residual_seconds", "max_residual_seconds",
        "accused_start_error_seconds", "accused_end_error_seconds", "source_start_error_seconds",
        "source_end_error_seconds", "inlier_count", "distinct_accused_frames",
        "distinct_source_frames", "duplicate_source_concentration",
        "distinct_source_frames_estimate", "duplicate_source_concentration_estimate",
        "delta_vs_nearest_oracle",
    }
    result = {key: item for key, item in value.items() if key in allowed}
    evaluation = value["evaluation"]
    result["evaluation"] = {
        key: evaluation[key] for key in (
            "complete", "source_identity", "accused_overlap_ratio", "accused_assignment",
            "accused_start_error_seconds", "accused_end_error_seconds",
            "source_start_within_tolerance", "source_end_within_tolerance",
            "source_start_error_seconds", "source_end_error_seconds", "source_match",
            "duration_slope_consistent",
        )
    }
    return result


def _safe_oracle(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "band_seconds": value["band_seconds"],
        "query_frame_count": value["query_frame_count"],
        "candidate_count": value["candidate_count"],
        "covered_frame_count": value["covered_frame_count"],
        "frame_coverage": value["frame_coverage"],
        "distinct_source_frame_count": value["distinct_source_frame_count"],
        "repetition": value["repetition"],
        "nearest_order": value["nearest_order"],
        "all_band_fit": sanitized_fit(value["all_band_fit"]),
        "nearest_per_frame_fit": sanitized_fit(value["nearest_per_frame_fit"]),
    }


def _metric_excerpt(metrics: dict[str, Any]) -> dict[str, Any]:
    return {
        "frame_count": metrics["frame_count"],
        "candidate_count": metrics["candidate_count"],
        "unique_source_frames_per_query": metrics["unique_source_frames_per_query"],
        "unique_correct_source_timestamps_per_query": metrics["unique_correct_source_timestamps_per_query"],
        "correct_source_present_frames": metrics["correct_source_present_frames"],
        "gt_near_hit_frames": metrics["gt_near_hit_frames"],
        "gt_nearest_rank": metrics["gt_nearest_rank"],
        "gt_nearest_distance_seconds": metrics["gt_nearest_distance_seconds"],
        "maximum_episode_local_spread_seconds": metrics["maximum_episode_local_spread_seconds"],
        "correct_source_spread_seconds": metrics["correct_source_spread_seconds"],
        "repetition_all_sources": metrics["repetition_all_sources"],
        "repetition_correct_source": metrics["repetition_correct_source"],
        "shared_top1_fraction": metrics["shared_top1_fraction"],
        "top1_monotonicity": metrics["top1_monotonicity"],
        "gt_nearest_order": metrics["gt_nearest_order"],
        "local_entropy": metrics["local_entropy"],
        "correct_source_local_entropy": metrics["correct_source_local_entropy"],
        "correct_source_raw_cosine_spread": metrics["correct_source_raw_cosine_spread"],
        "gt_near_competition_margin": metrics["gt_near_competition_margin"],
    }


def _primary_summary(raw: dict[str, Any]) -> list[dict[str, Any]]:
    broader = {
        row["case_id"]: row for row in raw["broader_dev"]["rows"]
    }
    output = []
    for row in raw["primary"]:
        summary = {
            "case_id": row["case_id"], "backend": row["backend"],
            "outcome": row["outcome"], "status": row["status"],
            "metrics": _metric_excerpt(row["metrics"]),
            "oracle": _safe_oracle(row["oracle"]),
            "trace": row["trace_summary"],
            "actual_correct_source_fits": [
                _safe_actual(item) for item in row["actual_correct_source_alternatives"]
            ],
        }
        if row["backend"] == "TAU_0.72":
            summary["mechanism"] = broader[row["case_id"]]["mechanism"]
            summary["paired_dct_comparison"] = broader[row["case_id"]]["comparison"]
        output.append(summary)
    return output


def _synthetic_summary(raw: dict[str, Any]) -> dict[str, Any]:
    rows = []
    for row in raw["synthetic"]["rows"]:
        trace_summary = sanitized_trace(row["trace"], row["oracle"])
        rows.append({
            "axis": row["axis"], "value": row["value"], "status": row["status"],
            "complete_correct_mapping": row["complete_correct_mapping"],
            "first_loss_stage": row["first_loss_stage"],
            "extraction_loss": row["extraction_loss"],
            "metrics": _metric_excerpt(row["metrics"]),
            "oracle": _safe_oracle(row["oracle"]),
            "trace": trace_summary,
        })
    failure_points = {}
    for axis, values in raw["synthetic"]["axis_grid"].items():
        axis_rows = [row for row in rows if row["axis"] == axis]
        failed = [row for row in axis_rows if not row["complete_correct_mapping"]]
        failure_points[axis] = failed[0]["value"] if failed else None
    return {"axis_grid": raw["synthetic"]["axis_grid"], "settings": rows, "first_failure": failure_points}


def _recommendation(raw: dict[str, Any], synthetic: dict[str, Any]) -> dict[str, Any]:
    primary = next(
        row for row in raw["broader_dev"]["rows"]
        if row["case_id"] == "SOURCE_A-POSITION_010-H264_REENCODE"
    )
    mechanism = primary["mechanism"]["mechanism_cell"]
    outside = [
        row for row in raw["broader_dev"]["rows"]
        if row["case_id"] != primary["case_id"]
    ]
    extra_e = sum(row["mechanism"]["extraction_loss"] for row in outside)
    extra_r = sum(row["mechanism"]["representation_separability_limit"] for row in outside)
    synthetic_e = sum(row["extraction_loss"] for row in synthetic["settings"])
    diversity_axes = ("distributed_duplicate_frames", "timestamp_quantization_seconds", "local_plateau_frames")
    synthetic_diversity_failure = any(
        synthetic["first_failure"].get(axis) is not None for axis in diversity_axes
    )
    if mechanism == "EXTRACTOR_COMPATIBILITY" and (extra_e > 0 or synthetic_e > 0):
        choice = "A"
    elif mechanism == "REPRESENTATION_SEPARABILITY" and synthetic_diversity_failure:
        choice = "B"
    elif mechanism == "MIXED" or (
        extra_e > 0 and extra_r > 0 and synthetic_e > 0 and synthetic_diversity_failure
    ):
        choice = "C"
    else:
        choice = "D"
    labels = {
        "A": "NARROW_TEMPORAL_ALIGNMENT_ALTERNATIVE_EXTRACTION_COMPATIBILITY_REMEDIATION",
        "B": "SEQUENCE_TEMPORAL_CONTEXT_VISUAL_RETRIEVAL_EXPERIMENT",
        "C": "MIXED_SEPARATE_FUTURE_REMEDIATION",
        "D": "NO_ACTION_REMAIN_REVIEW_REQUIRED",
    }
    return {
        "choice": choice, "label": labels[choice], "primary_mechanism": mechanism,
        "additional_dev_extraction_loss_count": extra_e,
        "additional_dev_representation_limit_count": extra_r,
        "synthetic_extraction_loss_setting_count": synthetic_e,
        "synthetic_diversity_failure_reproduced": synthetic_diversity_failure,
    }


def build_aggregate(raw: dict[str, Any]) -> dict[str, Any]:
    if raw["protocol_commit"] != PROTOCOL_COMMIT or raw["production_commit"] != PRODUCTION_COMMIT:
        raise ValueError("raw result provenance mismatch")
    synthetic = _synthetic_summary(raw)
    broader = raw["broader_dev"]
    aggregate = {
        "schema_version": "phase2b4-temporal-separability-v0.1",
        "experiment_status": "DEV_CHARACTERIZATION_ONLY",
        "protocol_commit": raw["protocol_commit"],
        "start_commit": raw["start_commit"],
        "production_commit": raw["production_commit"],
        "reference_policy": raw["reference_policy"],
        "reference_policy_semantics": "diagnostic_only_not_selected_for_production",
        "data_policy": raw["data_policy"],
        "primary": _primary_summary(raw),
        "tau_invariance": raw["tau_invariance"],
        "broader_dev": {
            "overall": broader["overall"],
            "backend_metric_summaries": broader["backend_metric_summaries"],
            "by_outcome": broader["by_outcome"],
            "by_failure_attribution": broader["by_failure_attribution"],
            "by_transformation": broader["by_transformation"],
            "position_matrix": broader["position_matrix"],
        },
        "synthetic": synthetic,
        "safety_sidebar": [
            {
                "backend": row["backend"], "outcome": row["outcome"], "status": row["status"],
                "candidate_tiers": row["candidate_tiers"], "review_tiers": row["review_tiers"],
                "metrics": _metric_excerpt(row["metrics"]),
            }
            for row in raw["safety_sidebar"]
        ],
        "architecture_recommendation": _recommendation(raw, synthetic),
        "runtime_seconds": raw["runtime_seconds"],
        "privacy": {
            "contains_exact_real_timestamps": False,
            "contains_per_frame_records": False,
            "contains_raw_candidates": False,
            "contains_media_model_index_paths_or_hashes": False,
        },
    }
    validate_sanitized(aggregate)
    return aggregate


def validate_sanitized(value: Any, key: str | None = None) -> None:
    if key in FORBIDDEN_KEYS:
        raise ValueError(f"forbidden private key: {key}")
    if isinstance(value, dict):
        for child_key, child in value.items():
            validate_sanitized(child, child_key)
    elif isinstance(value, list):
        for child in value:
            validate_sanitized(child, key)
    elif isinstance(value, str):
        if any(token.lower() in value.lower() for token in FORBIDDEN_TEXT):
            raise ValueError("forbidden private text")
        if PRIVATE_HASH.search(value):
            raise ValueError("private 64-character fingerprint")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=ROOT / "workspace/phase2b4/raw.json")
    parser.add_argument(
        "--output", type=Path,
        default=ROOT / "benchmark/visual/results/phase2b4-temporal-separability-v0.1.json",
    )
    args = parser.parse_args()
    payload = build_aggregate(_read(args.input))
    _write(args.output, payload)
    print(args.output)


if __name__ == "__main__":
    main()
