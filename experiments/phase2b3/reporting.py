"""Build the aggregate-only, privacy-checked Phase 2B-3 result artifact."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
from typing import Any, Iterable

from experiments.phase2b1 import benchmark as retrieval
from experiments.phase2b3 import benchmark as b


FORBIDDEN_KEYS = {
    "path",
    "file_path",
    "sha256",
    "frames",
    "embedding",
    "embeddings",
    "candidate_audit",
    "source_time",
    "accused_time",
    "source_start",
    "source_end",
    "accused_start",
    "accused_end",
    "source_start_seconds",
    "source_end_seconds",
    "truth",
    "predictions",
    "alternatives",
    "implementation_identity",
    "execution_identity",
    "model_local_identity",
    "source_indexes",
    "input_manifests",
}
FORBIDDEN_TEXT = ("/Users/", "real_test", ".mp4", ".mov", "EP01", "EP02")


def validate_sanitized(payload: Any) -> None:
    """Reject raw timelines, media identity, local paths and private fingerprints."""

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            leaking = FORBIDDEN_KEYS.intersection(value)
            if leaking:
                raise ValueError(f"raw/private key in aggregate: {sorted(leaking)}")
            for nested in value.values():
                visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)
        elif isinstance(value, str):
            if any(token in value for token in FORBIDDEN_TEXT):
                raise ValueError("private path/media identity in aggregate")
            if re.search(r"\b[0-9a-f]{64}\b", value):
                raise ValueError("private 64-character fingerprint in aggregate")

    visit(payload)


def _positive(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if row["positive"]]


def run_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "overall": b.extended_summary(rows),
        "by_transformation": {
            transform: b.extended_summary(
                [row for row in rows if row["transformation"] == transform]
            )
            for transform in retrieval.TRANSFORMATIONS
        },
        "by_source": {
            source: b.extended_summary(
                [row for row in rows if row.get("source_alias") == source]
            )
            for source in ("SOURCE_A", "SOURCE_B")
        },
    }


def position_matrix(rows: list[dict[str, Any]], backends: tuple[str, ...]) -> dict[str, Any]:
    positives = _positive(rows)
    matrix: list[dict[str, Any]] = []
    for position in sorted({row["position_id"] for row in positives}):
        position_rows = [row for row in positives if row["position_id"] == position]
        matrix.append(
            {
                "position_alias": position,
                "source_alias": position_rows[0]["source_alias"],
                "normalized_source_position": position_rows[0]["normalized_source_start"],
                "backends": {
                    backend: {
                        "taxonomy": b.extended_summary(
                            [row for row in position_rows if row["backend"] == backend]
                        )["taxonomy"],
                        "safe_success": b.extended_summary(
                            [row for row in position_rows if row["backend"] == backend]
                        )["safe_success"],
                        "outcomes": {
                            row["transformation"]: row["outcome"]
                            for row in position_rows
                            if row["backend"] == backend
                        },
                    }
                    for backend in backends
                },
            }
        )
    return {
        "independent_position_count": len(matrix),
        "transformations_per_position": len(retrieval.TRANSFORMATIONS),
        "matrix": matrix,
        "all_transformations_safe_position_count": {
            backend: sum(
                all(
                    outcome in {
                        "CORRECT_UNAMBIGUOUS_MATCH",
                        "SAFE_AMBIGUITY_CONTAINS_GT",
                    }
                    for outcome in item["backends"][backend]["outcomes"].values()
                )
                for item in matrix
            )
            for backend in backends
        },
    }


def paired_transitions(
    rows: list[dict[str, Any]], baseline: str, selected: str
) -> dict[str, Any]:
    left = {
        row["case_id"]: row
        for row in rows
        if row["positive"] and row["backend"] == baseline
    }
    right = {
        row["case_id"]: row
        for row in rows
        if row["positive"] and row["backend"] == selected
    }
    if left.keys() != right.keys():
        raise ValueError("paired backend cells differ")

    def counts(case_ids: Iterable[str]) -> dict[str, int]:
        return dict(
            sorted(
                Counter(
                    f"{left[case_id]['outcome']} -> {right[case_id]['outcome']}"
                    for case_id in case_ids
                ).items()
            )
        )

    return {
        "paired_cell_count": len(left),
        "overall": counts(left),
        "by_transformation": {
            transform: counts(
                case_id
                for case_id, row in left.items()
                if row["transformation"] == transform
            )
            for transform in retrieval.TRANSFORMATIONS
        },
    }


def negative_matrix(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "negative_alias": row["case_id"],
            "backend": row["backend"],
            "status": row["status"],
            "emitted_segment_count": len(row["predictions"]),
            "candidate_tiers": dict(
                Counter(item["candidate_tier"] for item in row["predictions"])
            ),
            "production_review_tier": row["review_tier"],
            "input_candidate_count": row["input_candidate_count"],
            "effective_candidate_count": row["effective_candidate_count"],
            "best_adapted_visual_score": row["best_visual_score"],
            "maximum_support": row["maximum_support"],
            "maximum_temporal_score": row["maximum_temporal_score"],
            "source_concentration": row["source_concentration"],
            "warning_codes": [warning["code"] for warning in row["warnings"]],
        }
        for row in rows
        if not row["positive"]
    ]


def candidate_diagnostics(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        key: summary[key]
        for key in (
            "input_candidate_count",
            "effective_candidate_count",
            "raw_gt_supported_frame_count",
            "effective_gt_supported_frame_count",
            "raw_maximum_wrong_source_line_frames",
            "effective_maximum_wrong_source_line_frames",
            "total_alternatives",
            "ambiguous_case_count",
            "wrong_primary_but_safe_alternative_count",
            "wrong_primary_without_correct_alternative_count",
            "safe_success",
            "unsafe_rate",
            "taxonomy",
        )
    }


def _batch_distribution(stage: dict[str, Any]) -> dict[str, int]:
    return dict(
        sorted(
            Counter(
                str(length)
                for item in stage["inference"]
                for length in item["actual_batch_lengths"]
            ).items(),
            key=lambda item: int(item[0]),
        )
    )


def aggregate(
    dev: dict[str, Any], selected: dict[str, Any], held: dict[str, Any]
) -> dict[str, Any]:
    if dev.get("stage") != "DEV" or held.get("stage") != "FRESH_HELD_OUT":
        raise ValueError("wrong lifecycle stage supplied to aggregate")
    expected_dev_digest = b.digest(
        {"summaries": dev["summaries"], "eligibility": dev["eligibility"]}
    )
    if expected_dev_digest != selected["dev_aggregate_digest"]:
        raise ValueError("DEV metrics changed after policy freeze")
    if b.digest(selected) != held["selected_policy_digest"]:
        raise ValueError("fresh held-out used a different selected policy")
    generation = held["fresh_generation_provenance"]
    if not (
        dev["completed_at"] <= selected["frozen_at"]
        <= generation["fresh_generation_started_at"]
        <= generation["fresh_generation_completed_at"]
        <= held["completed_at"]
    ):
        raise ValueError("invalid DEV/freeze/fresh/held-out chronology")
    name = selected["selected_policy"]
    if set(held["summaries"]) != {"DCT", name}:
        raise ValueError("fresh held-out contains an unselected policy")
    gate = b.promotion_gate(held)
    dev_backends = ("DCT", *(policy.name for policy in b.POLICY_GRID))
    fresh_backends = ("DCT", name)
    result = {
        "schema_version": "phase2b3-dino-score-contract-v0.1",
        "protocol_commit": b.PROTOCOL_COMMIT,
        "production_commit": b.PRODUCTION_COMMIT,
        "phase2b1_commit": b.PHASE2B1_COMMIT,
        "phase2b2_commit": b.PHASE2B2_COMMIT,
        "configuration": {
            "source_fps": 2.0,
            "query_fps": 2.0,
            "top_k": 8,
            "dino_batch_size": b.DINO_BATCH_SIZE,
            "final_batch": "natural short tail; no padding",
            "model": "dinov2-small-int8",
            "retrieval": "exact NumPy cosine",
            "score_mapping": "global anchored affine",
            "tau_grid": [policy.tau for policy in b.POLICY_GRID],
            "aligner_visual_admission": b.ALIGNER_ADMISSION_SCORE,
            "auto_release_enabled": False,
            "candidate_green": "internal alignment quality diagnostic only",
            "adapted_score_is_probability": False,
        },
        "sample_scope": {
            "episode_n": 2,
            "dev_content_positions": 20,
            "dev_positive_queries": 220,
            "dev_synthetic_negatives": 20,
            "fresh_content_positions": 10,
            "fresh_positive_queries": 110,
            "fresh_synthetic_negatives": 10,
            "transformations_per_position": 11,
            "independence_unit": "content position; transformation cells are paired",
            "synthetic_negative_claim": "safety smoke test, not real-world false-positive rate",
        },
        "selection": {
            "selected_policy": name,
            "selected_tau": selected["selected_tau"],
            "selection_source": selected["selection_source"],
            "selection_reason": selected["selection_reason"],
            "eligible_policies": selected["eligible_policies"],
            "fresh_metrics_used": selected["fresh_metrics_used"],
            "eligibility": selected["eligibility"],
        },
        "chronology": {
            "dev_completed": dev["completed_at"],
            "policy_frozen": selected["frozen_at"],
            "fresh_generation_started": generation["fresh_generation_started_at"],
            "fresh_generation_completed": generation["fresh_generation_completed_at"],
            "fresh_held_out_completed": held["completed_at"],
            "implementation_identity_verified": True,
            "policy_frozen_before_fresh_generation": True,
        },
        "dev": {
            "backends": {
                backend: run_summary(
                    [row for row in dev["cases"] if row["backend"] == backend]
                )
                for backend in dev_backends
            },
            "candidate_and_rival_diagnostics": {
                backend: candidate_diagnostics(dev["summaries"][backend])
                for backend in dev_backends
            },
            "a010": {
                backend: b.a010_regression(dev["cases"], backend)
                for backend in dev_backends
            },
            "positions": position_matrix(dev["cases"], dev_backends),
            "negatives": negative_matrix(dev["cases"]),
        },
        "fresh_held_out": {
            "backends": {
                backend: run_summary(
                    [row for row in held["cases"] if row["backend"] == backend]
                )
                for backend in fresh_backends
            },
            "positions": position_matrix(held["cases"], fresh_backends),
            "paired_transitions": paired_transitions(held["cases"], "DCT", name),
            "negatives": negative_matrix(held["cases"]),
            "promotion_gate": gate,
        },
        "runtime": {
            "dev_seconds": dev["runtime_seconds"],
            "fresh_held_out_seconds": held["runtime_seconds"],
            "dev_inference_batch_lengths": _batch_distribution(dev),
            "fresh_inference_batch_lengths": _batch_distribution(held),
            "source_index": "reused validated Phase 2B-1 arrays",
        },
        "privacy": {
            "contains_media_identity": False,
            "contains_paths": False,
            "contains_private_hashes": False,
            "contains_per_frame_records": False,
            "contains_exact_timelines": False,
        },
    }
    validate_sanitized(result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=b.ROOT / "workspace/phase2b3")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = aggregate(
        b.read(args.workspace / "dev.json"),
        b.read(args.workspace / "selected-score-contract.json"),
        b.read(args.workspace / "fresh-held-out.json"),
    )
    b.write(args.output, result)
    print(json.dumps({"output": str(args.output), "promotion_gate": result["fresh_held_out"]["promotion_gate"]}, indent=2))


if __name__ == "__main__":
    main()
