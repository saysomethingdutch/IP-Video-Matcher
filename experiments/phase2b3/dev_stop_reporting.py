"""Sanitized DEV-only export for the protocol-mandated no-eligible-policy stop."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from experiments.phase2b3 import benchmark as b
from experiments.phase2b3.reporting import (
    candidate_diagnostics,
    negative_matrix,
    position_matrix,
    run_summary,
    validate_sanitized,
)


def aggregate(dev: dict[str, Any], workspace: Path) -> dict[str, Any]:
    b.verify_protocol()
    if dev.get("stage") != "DEV" or dev.get("protocol_commit") != b.PROTOCOL_COMMIT:
        raise ValueError("invalid DEV result provenance")
    if dev["implementation_identity"]["implementation"] != b.implementation_identity():
        raise ValueError("current experimental implementation differs from DEV identity")
    recalculated = b.policy_eligibility(dev)
    if recalculated != dev["eligibility"]:
        raise ValueError("stored DEV eligibility differs from deterministic recalculation")
    selection = b.select_policy(dev)
    if selection["selected_policy"] is not None or selection["eligible_policies"]:
        raise ValueError("DEV-only stop report is only valid when no policy is eligible")
    forbidden_outputs = (
        workspace / "selected-score-contract.json",
        workspace / "fresh",
        workspace / "fresh-corpus.json",
        workspace / "fresh-negatives.json",
        workspace / "fresh-held-out.json",
    )
    if any(path.exists() for path in forbidden_outputs):
        raise ValueError("selection or fresh output exists despite no eligible policy")
    backends = ("DCT", *(policy.name for policy in b.POLICY_GRID))
    wrong_green = {}
    for backend in backends:
        rows = [
            row
            for row in dev["cases"]
            if row["backend"] == backend and row["positive"]
        ]
        wrong_green[backend] = {
            "wrong_source_green_segment_count": sum(
                prediction["candidate_tier"] == "GREEN"
                and prediction["source_episode"] != row["truth"][0]["source_episode"]
                for row in rows
                for prediction in row["predictions"]
            ),
            "unambiguous_wrong_source_primary_green_case_count": sum(
                row["status"] == "MATCH_FOUND"
                and bool(row["predictions"])
                and row["predictions"][0]["source_episode"]
                != row["truth"][0]["source_episode"]
                and row["predictions"][0]["candidate_tier"] == "GREEN"
                for row in rows
            ),
        }
    result = {
        "schema_version": "phase2b3-dino-score-contract-dev-stop-v0.1",
        "protocol_commit": b.PROTOCOL_COMMIT,
        "production_commit": b.PRODUCTION_COMMIT,
        "phase2b1_commit": b.PHASE2B1_COMMIT,
        "phase2b2_commit": b.PHASE2B2_COMMIT,
        "experiment_outcome": {
            "stage_reached": "DEV_COMPLETE",
            "termination": "NO_ELIGIBLE_GLOBAL_SCORE_CONTRACT",
            "selected_policy": None,
            "fresh_data_generated": False,
            "fresh_held_out_executed": False,
            "architecture_recommendation": (
                "C. RETRIEVAL REMAINS STRONG BUT SCORE/RIVAL SAFETY STILL UNRESOLVED"
            ),
        },
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
            "all_fixed_top8_candidates_adapted": True,
            "manual_pre_filter": False,
            "auto_release_enabled": False,
            "candidate_green": "internal alignment quality diagnostic only",
            "adapted_score_is_probability": False,
        },
        "sample_scope": {
            "episode_n": 2,
            "dev_content_positions": 20,
            "dev_positive_queries": 220,
            "dev_synthetic_negatives": 20,
            "transformations_per_position": 11,
            "independence_unit": "content position; transformation cells are paired",
            "synthetic_negative_claim": "safety constraint only, not a real-world false-positive estimate",
            "fresh_content_positions": 0,
            "fresh_positive_queries": 0,
            "fresh_synthetic_negatives": 0,
        },
        "selection": {
            "selected_policy": None,
            "selected_tau": None,
            "eligible_policies": [],
            "selection_source": "DEV_ONLY",
            "selection_reason": selection["selection_reason"],
            "fresh_metrics_used": False,
            "eligibility": recalculated,
        },
        "dev": {
            "completed_at": dev["completed_at"],
            "backends": {
                backend: run_summary(
                    [row for row in dev["cases"] if row["backend"] == backend]
                )
                for backend in backends
            },
            "candidate_and_rival_diagnostics": {
                backend: candidate_diagnostics(dev["summaries"][backend])
                for backend in backends
            },
            "a010": {
                backend: b.a010_regression(dev["cases"], backend)
                for backend in backends
            },
            "wrong_green": wrong_green,
            "positions": position_matrix(dev["cases"], backends),
            "negatives": negative_matrix(dev["cases"]),
        },
        "runtime": {
            "dev_seconds": dev["runtime_seconds"],
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
    result = aggregate(b.read(args.workspace / "dev.json"), args.workspace)
    b.write(args.output, result)
    print(json.dumps({"output": str(args.output), **result["experiment_outcome"]}, indent=2))


if __name__ == "__main__":
    main()
