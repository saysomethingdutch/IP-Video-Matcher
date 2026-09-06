#!/usr/bin/env python3
"""Build the sanitized Phase 2B-5 aggregate from ignored DEV results."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import re
from typing import Any, Iterable

from .benchmark import BACKENDS, POLICIES, PRODUCTION_COMMIT, PROTOCOL_COMMIT, ROOT


FORBIDDEN_KEYS = {
    "truth", "predictions", "alternatives", "candidate_audit", "candidate_ids",
    "candidate_identity", "raw_candidates", "input_candidate_ordinals",
    "inlier_ordinals", "removed_candidate_ordinals", "remaining_candidate_ordinals",
    "proposal_records", "primary_traces", "e_level_traces", "rows", "source_time",
    "accused_time", "source_start", "source_end", "accused_start", "accused_end",
    "fitted_offset_seconds", "intercept", "embedding", "model_path", "index_path",
    "path", "sha256", "fingerprints",
}
FORBIDDEN_TEXT = (
    "/Users/", "real_test", ".mp4", ".mkv", ".mov", ".onnx",
    "workspace/phase2b", "selected-score-contract", "held-out", "held_out",
)
PRIVATE_HASH = re.compile(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])", re.IGNORECASE)


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _quantiles(values: Iterable[float | int | None]) -> dict[str, float | int | None]:
    ordered = sorted(float(value) for value in values if value is not None and math.isfinite(float(value)))
    if not ordered:
        return {"n": 0, "minimum": None, "median": None, "p90": None, "maximum": None}

    def at(fraction: float) -> float:
        position = (len(ordered) - 1) * fraction
        lower = math.floor(position)
        upper = math.ceil(position)
        if lower == upper:
            return ordered[lower]
        return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)

    return {
        "n": len(ordered),
        "minimum": ordered[0],
        "median": at(0.5),
        "p90": at(0.9),
        "maximum": ordered[-1],
    }


def _synthetic_summary(value: dict[str, Any]) -> dict[str, Any]:
    rows = []
    for row in value["rows"]:
        first_round = row["trace"]["rounds"][0] if row["trace"]["rounds"] else None
        rows.append({
            "fixture": row["fixture"],
            "policy_id": row["policy_id"],
            "status": row["status"],
            "outcome": row["outcome"],
            "primary_mapping_count": len(row["predictions"]),
            "alternative_mapping_count": len(row["alternatives"]),
            "complete_proposal_count": row["trace"]["complete_gt_free_proposal_count"],
            "complete_final_mapping_count": row["trace"]["complete_final_mapping_count"],
            "production_replay_equal": row["production_replay_equal"],
            "review_tiers": sorted({
                item["review_tier"] for item in row["predictions"] + row["alternatives"]
            }),
            "round0": None if first_round is None else {
                "proposal_count": len(first_round["fits"]),
                "complete_proposal_count": first_round["complete_proposal_count"],
                "selected_slope": first_round["selected_fit"]["mapping"]["speed_ratio"],
                "selected_quality": first_round["selected_fit"]["explanation_quality"],
                "selected_inlier_count": first_round["selected_fit"]["mapping"]["inlier_count"],
                "selected_inlier_ratio": first_round["selected_fit"]["mapping"]["inlier_ratio"],
                "selected_rms_seconds": first_round["selected_fit"]["mapping"]["residual_rms_seconds"],
                "selected_complete": first_round["selected_evaluator_complete"],
                "shortlist_size": len(first_round["selection"]["shortlist_ordinals"]),
                "occupancy_count": first_round["occupancy_count"],
            },
        })
    return {
        "baseline_reproducer": value["baseline_reproducer"],
        "policy_eligibility": value["policy_eligibility"],
        "fixture_matrix": rows,
    }


def _a010_summary(raw: dict[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for backend in BACKENDS:
        output[backend] = {}
        for policy in POLICIES:
            controls = raw["overall"][backend][policy.policy_id]["a010"]
            output[backend][policy.policy_id] = {}
            for transformation, value in controls.items():
                correct_episode_rounds = [
                    item for item in value["trace_summary"]["rounds"]
                    if item["episode"] == "SOURCE_A"
                ]
                output[backend][policy.policy_id][transformation] = {
                    "outcome": value["outcome"],
                    "status": value["status"],
                    "safe": value["safe"],
                    "complete_final_mapping_count": value["complete_final_mapping_count"],
                    "wrong_primary_green": value["wrong_primary_green"],
                    "review_tiers": value["review_tiers"],
                    "correct_episode_rounds": correct_episode_rounds[:2],
                }
    return output


def _position_matrix(rows: list[dict[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    positions = sorted({item["position_id"] for item in rows if item["positive"]})
    for backend in BACKENDS:
        output[backend] = {}
        for policy in POLICIES:
            output[backend][policy.policy_id] = {}
            for position in positions:
                chosen = [
                    item for item in rows
                    if item["positive"] and item["backend"] == backend
                    and item["policy_id"] == policy.policy_id
                    and item["position_id"] == position
                ]
                output[backend][policy.policy_id][position] = {
                    "transformation_cell_count": len(chosen),
                    "outcomes": dict(Counter(item["outcome"] for item in chosen)),
                    "safe_count": sum(item["outcome"] in {
                        "CORRECT_UNAMBIGUOUS_MATCH", "SAFE_AMBIGUITY_CONTAINS_GT"
                    } for item in chosen),
                    "extraction_loss_count": sum(
                        item["mechanism"]["extraction_loss"] for item in chosen
                    ),
                }
    return output


def _e_trace_aggregate(raw: dict[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for policy_id in ("P0", raw["transition_target_policy"]):
        records = [item["comparison"][policy_id] for item in raw["e_level_traces"]]
        round0 = [item["trace"]["rounds"][0] for item in records if item["trace"]["rounds"]]
        round1 = [item["trace"]["rounds"][1] for item in records if len(item["trace"]["rounds"]) > 1]
        output[policy_id] = {
            "case_count": len(records),
            "round0_complete_proposals": _quantiles(item["complete_proposal_count"] for item in round0),
            "round0_selected_slope": _quantiles(
                item["selected_fit"]["mapping"]["speed_ratio"] for item in round0
            ),
            "round0_selected_rms_seconds": _quantiles(
                item["selected_fit"]["mapping"]["residual_rms_seconds"] for item in round0
            ),
            "round0_selected_inlier_count": _quantiles(
                item["selected_fit"]["mapping"]["inlier_count"] for item in round0
            ),
            "round0_occupancy_count": _quantiles(item["occupancy_count"] for item in round0),
            "round1_complete_proposals": _quantiles(item["complete_proposal_count"] for item in round1),
            "final_complete_case_count": sum(item["final_complete_mapping_count"] > 0 for item in records),
            "still_extraction_loss_count": sum(item["still_extraction_loss"] for item in records),
            "outcomes": dict(Counter(item["outcome"] for item in records)),
        }
    return output


def build_aggregate(raw: dict[str, Any], synthetic: dict[str, Any]) -> dict[str, Any]:
    if raw["protocol_commit"] != PROTOCOL_COMMIT or synthetic["protocol_commit"] != PROTOCOL_COMMIT:
        raise ValueError("protocol provenance mismatch")
    if raw["production_commit"] != PRODUCTION_COMMIT:
        raise ValueError("production provenance mismatch")
    overall = raw["overall"]
    aggregate = {
        "schema_version": "phase2b5-extraction-remediation-v0.1",
        "experiment_status": "DEV_CHARACTERIZATION_ONLY_NO_ELIGIBLE_POLICY",
        "protocol_commit": PROTOCOL_COMMIT,
        "start_commit": raw["start_commit"],
        "production_commit": PRODUCTION_COMMIT,
        "reference_backend": raw["reference_backend"],
        "reference_backend_semantics": "diagnostic_only_not_selected_for_production",
        "data_policy": raw["data_policy"],
        "policies": raw["policies"],
        "synthetic": _synthetic_summary(synthetic),
        "overall": overall,
        "by_transformation": raw["by_transformation"],
        "position_matrix": _position_matrix(raw["rows"]),
        "a010_controls": _a010_summary(raw),
        "eligibility": raw["eligibility"],
        "qualified_policies": raw["qualified_policies"],
        "selected_experimental_policy": raw["selected_experimental_policy"],
        "best_policy_if_none_qualified": raw["best_policy_if_none_qualified"],
        "transition_target_policy": raw["transition_target_policy"],
        "transitions": raw["transitions"],
        "mechanism_contrasts": raw["mechanism_contrasts"],
        "e_trace_aggregate": _e_trace_aggregate(raw),
        "runtime_seconds": raw["runtime_seconds"],
        "architecture_recommendation": {
            "choice": "C",
            "label": "ANOTHER_NARROW_EXTRACTION_REMEDIATION_MAY_BE_JUSTIFIED",
            "reason": (
                "No registered policy passed all gates; occupancy reduced extraction loss "
                "materially, but no synthetic-eligible policy also met A010 and E-position gates."
            ),
            "fresh_validation_authorized": False,
            "production_change_authorized": False,
        },
        "privacy": {
            "contains_exact_real_timestamps": False,
            "contains_per_frame_records": False,
            "contains_raw_candidates_or_candidate_ids": False,
            "contains_media_model_index_paths_or_private_hashes": False,
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
    parser.add_argument("--dev", type=Path, default=ROOT / "workspace/phase2b5/dev.json")
    parser.add_argument("--synthetic", type=Path, default=ROOT / "workspace/phase2b5/synthetic.json")
    parser.add_argument(
        "--output", type=Path,
        default=ROOT / "benchmark/visual/results/phase2b5-extraction-remediation-v0.1.json",
    )
    args = parser.parse_args()
    _write(args.output, build_aggregate(_read(args.dev), _read(args.synthetic)))
    print(args.output)


if __name__ == "__main__":
    main()
