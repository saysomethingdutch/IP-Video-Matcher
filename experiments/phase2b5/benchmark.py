#!/usr/bin/env python3
"""Phase 2B-5 synthetic and existing-DEV extraction-policy benchmark."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import subprocess
import time
from typing import Any

from experiments.phase2b3 import benchmark as phase2b3
from experiments.phase2b4.benchmark import (
    _mechanism as dino_mechanism,
    _separability_comparison,
)
from experiments.phase2b4.metrics import (
    mapping_evaluation,
    oracle_band_diagnostics,
    segment_from_dict,
    temporal_separability_metrics,
)
from experiments.phase2b4.trace import candidates_from_audit
from ipvm.benchmarking import evaluate_segments
from ipvm.config import MatchingConfig
from ipvm.matching import AlignmentOutcome, TemporalAligner
from ipvm.models import CandidateMatch, MatchStatus
from ipvm.temporal_benchmark import (
    CASE_OUTCOMES,
    _metric_summary,
    classify_boundary_assertion,
    classify_case_outcome,
)

from .aligner import ExperimentalExtractionAligner
from .policies import POLICIES, POLICY_BY_ID, SIMPLICITY_ORDER, ExtractionPolicy
from .synthetic import FIXTURE_NAMES, SyntheticFixture, build_fixture


ROOT = Path(__file__).resolve().parents[2]
PROTOCOL_COMMIT = "54531ce7e268b20c1292c9b3721b1307eb072835"
START_COMMIT = "fb0e6066a6640e772d295f73ba3d0f7234e64a3e"
PRODUCTION_COMMIT = "93bf13b"
REFERENCE_BACKEND = "TAU_0.72"
BACKENDS = ("DCT", REFERENCE_BACKEND)
PRIMARY_CASES = tuple(
    f"SOURCE_A-POSITION_010-{name}"
    for name in ("DIRECT", "H264_REENCODE", "SPEED_0_8X")
)
def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_exclusive(path: Path, value: Any) -> None:
    resolved = path.resolve()
    if not resolved.is_relative_to((ROOT / "workspace").resolve()):
        raise ValueError("raw Phase 2B-5 outputs must remain under ignored workspace")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")


def _json_contract(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _status(outcome: AlignmentOutcome) -> str:
    if outcome.ambiguous or outcome.contradiction:
        return MatchStatus.AMBIGUOUS.value
    return MatchStatus.MATCH_FOUND.value if outcome.segments else MatchStatus.MATCH_NOT_FOUND.value


def _outcome_payload(outcome: AlignmentOutcome) -> dict[str, Any]:
    return {
        "status": _status(outcome),
        "predictions": [item.to_dict() for item in outcome.segments],
        "alternatives": [item.to_dict() for item in outcome.alternatives],
        "warnings": list(outcome.warnings),
        "truncated_episodes": list(outcome.truncated_episodes),
        "conflict_graph": outcome.conflict_graph,
        "ambiguous": outcome.ambiguous,
        "contradiction": outcome.contradiction,
    }


def verify_stage(dev_path: Path | None = None) -> None:
    if subprocess.run(
        ["git", "merge-base", "--is-ancestor", START_COMMIT, "HEAD"],
        cwd=ROOT,
        check=False,
    ).returncode:
        raise ValueError("Phase 2B-5 start commit is not an ancestor")
    paths = _git(
        "diff-tree", "--no-commit-id", "--name-only", "-r", PROTOCOL_COMMIT
    ).splitlines()
    if paths != ["experiments/phase2b5/PROTOCOL.md"]:
        raise ValueError("Phase 2B-5 protocol commit is not protocol-only")
    if _git("diff", PROTOCOL_COMMIT, "--", "experiments/phase2b5/PROTOCOL.md"):
        raise ValueError("pre-registered Phase 2B-5 protocol changed")
    if _git("diff", PRODUCTION_COMMIT, "--", "src/", "config/"):
        raise ValueError("frozen production/config changed")
    workspace = (dev_path or ROOT / "workspace/phase2b3/dev.json").resolve().parent
    forbidden = [
        path for path in workspace.rglob("*")
        if path.is_file() and any(
            token in path.name.lower()
            for token in ("selected-score-contract", "fresh", "held-out", "held_out")
        )
    ]
    if forbidden:
        raise ValueError("fresh/held-out/selected Phase 2B-3 artifact exists")


def validate_dev(dev: dict[str, Any]) -> list[dict[str, Any]]:
    if dev.get("stage") != "DEV":
        raise ValueError("Phase 2B-5 accepts DEV input only")
    rows = [item for item in dev.get("cases", []) if item.get("backend") in BACKENDS]
    positives = [item for item in rows if item.get("positive")]
    negatives = [item for item in rows if not item.get("positive")]
    expected_positive = {
        (position, transform, backend)
        for position in (
            *(f"SOURCE_A-POSITION_{index:03d}" for index in range(1, 11)),
            *(f"SOURCE_B-POSITION_{index:03d}" for index in range(1, 11)),
        )
        for transform in phase2b3.retrieval.TRANSFORMATIONS
        for backend in BACKENDS
    }
    actual_positive = [
        (item["position_id"], item["transformation"], item["backend"])
        for item in positives
    ]
    if len(positives) != 440 or len(actual_positive) != len(set(actual_positive)):
        raise ValueError("DEV positive rows are incomplete or duplicated")
    if set(actual_positive) != expected_positive:
        raise ValueError("DEV aliases/transformation/backend matrix differs from protocol")
    if len(negatives) != 40:
        raise ValueError("expected 20 DEV synthetic negatives per backend")
    for backend in BACKENDS:
        backend_negatives = [item for item in negatives if item["backend"] == backend]
        if len(backend_negatives) != 20 or len({item["case_id"] for item in backend_negatives}) != 20:
            raise ValueError("DEV negative matrix is incomplete or duplicated")
    if any(item.get("split") != "DEV" for item in rows):
        raise ValueError("non-DEV row present")
    return rows


def _mapping_key(value: dict[str, Any]) -> tuple[Any, ...]:
    return (
        value["source_episode"],
        round(float(value["accused_start"]), 6),
        round(float(value["accused_end"]), 6),
        round(float(value["source_start"]), 6),
        round(float(value["source_end"]), 6),
    )


def _labeled_trace(
    aligner: ExperimentalExtractionAligner,
    truth: dict[str, Any],
    final_values: list[dict[str, Any]],
) -> dict[str, Any]:
    proposal_records: list[dict[str, Any]] = []
    distinct: set[tuple[Any, ...]] = set()
    for item in aligner.proposal_trace:
        record = dict(item)
        if item["mapping"] is None:
            record["evaluator_complete"] = False
        else:
            evaluation = mapping_evaluation(segment_from_dict(item["mapping"]), truth)
            record["evaluation"] = evaluation
            record["evaluator_complete"] = bool(evaluation["complete"])
            if evaluation["complete"]:
                distinct.add(_mapping_key(item["mapping"]))
        proposal_records.append(record)

    rounds: list[dict[str, Any]] = []
    for item in aligner.round_trace:
        fits = []
        for fit in item["fits"]:
            evaluation = mapping_evaluation(segment_from_dict(fit["mapping"]), truth)
            fits.append({**fit, "evaluation": evaluation, "evaluator_complete": evaluation["complete"]})
        selected = next(value for value in fits if value["ordinal"] == item["selection"]["selected_ordinal"])
        rounds.append({
            **item,
            "fits": fits,
            "selected_fit": selected,
            "complete_proposal_count": len({_mapping_key(value["mapping"]) for value in fits if value["evaluator_complete"]}),
            "selected_evaluator_complete": bool(selected["evaluator_complete"]),
            "occupancy_count": len(item["removed_candidate_ordinals"]),
        })

    final_evaluations = [
        mapping_evaluation(segment_from_dict(item), truth) for item in final_values
    ]
    return {
        "proposal_records": proposal_records,
        "rounds": rounds,
        "complete_gt_free_proposal_count": len(distinct),
        "complete_final_mapping_count": sum(item["complete"] for item in final_evaluations),
        "final_evaluations": final_evaluations,
    }


def _run_one(
    candidates: list[CandidateMatch], policy: ExtractionPolicy
) -> tuple[AlignmentOutcome, ExperimentalExtractionAligner, float]:
    aligner = ExperimentalExtractionAligner(MatchingConfig(), policy, candidates)
    started = time.perf_counter()
    outcome = aligner.align_with_diagnostics(candidates, index_complete=True)
    return outcome, aligner, time.perf_counter() - started


def run_synthetic() -> dict[str, Any]:
    verify_stage()
    rows: list[dict[str, Any]] = []
    for fixture_name in FIXTURE_NAMES:
        fixture = build_fixture(fixture_name)
        production = TemporalAligner(MatchingConfig()).align_with_diagnostics(
            list(fixture.candidates), index_complete=True
        )
        for policy in POLICIES:
            candidates = list(fixture.candidates)
            outcome, aligner, runtime = _run_one(candidates, policy)
            payload = _outcome_payload(outcome)
            final_values = payload["predictions"] + payload["alternatives"]
            trace = _labeled_trace(aligner, fixture.truth, final_values)
            metrics = evaluate_segments(
                [fixture.truth], list(outcome.segments), payload["status"], list(outcome.alternatives)
            )
            taxonomy = classify_case_outcome(
                payload["status"], metrics, [fixture.truth], list(outcome.alternatives)
            )
            rows.append({
                "fixture": fixture_name,
                "policy_id": policy.policy_id,
                "epsilon": policy.epsilon,
                "occupancy_mode": policy.occupancy_mode,
                **payload,
                "truth": fixture.truth,
                "outcome": taxonomy,
                "metrics": metrics,
                "trace": trace,
                "runtime_seconds": runtime,
                "production_replay_equal": (
                    policy.policy_id != "P0"
                    or _json_contract(payload) == _json_contract(_outcome_payload(production))
                ),
            })

    baseline_loss = next(
        item for item in rows if item["fixture"] == "EXTRACTION_LOSS" and item["policy_id"] == "P0"
    )
    first_round = baseline_loss["trace"]["rounds"][0]
    registered_pair = [
        item for item in first_round["fits"]
        if item["evaluator_complete"]
        and abs(float(item["mapping"]["speed_ratio"] or 0.0) - 1.0) < 1e-9
        and abs(float(item["mapping"]["residual_rms_seconds"])) < 1e-9
    ]
    registered_gap = (
        first_round["selection"]["q_max"]
        - max(item["explanation_quality"] for item in registered_pair)
        if registered_pair else None
    )
    closest_gap = (
        first_round["selection"]["q_max"]
        - max(item["explanation_quality"] for item in first_round["fits"] if item["evaluator_complete"])
    )
    baseline_valid = bool(
        all(item["production_replay_equal"] for item in rows if item["policy_id"] == "P0")
        and first_round["complete_proposal_count"] > 0
        and not first_round["selected_evaluator_complete"]
        and baseline_loss["trace"]["complete_final_mapping_count"] == 0
        and registered_gap is not None
        and 0.0005 - 1e-12 <= registered_gap <= 0.0010 + 1e-12
    )
    if not baseline_valid:
        raise ValueError("P0 did not reproduce the pre-registered extraction-loss fixture")

    baseline_counts = {
        name: len(next(
            item for item in rows if item["fixture"] == name and item["policy_id"] == "P0"
        )["predictions"]) + len(next(
            item for item in rows if item["fixture"] == name and item["policy_id"] == "P0"
        )["alternatives"])
        for name in FIXTURE_NAMES if name != "EXTRACTION_LOSS"
    }
    eligibility: dict[str, Any] = {}
    for policy in POLICIES[1:]:
        indexed = {
            item["fixture"]: item for item in rows if item["policy_id"] == policy.policy_id
        }
        criteria = {
            "restores_extraction_loss": indexed["EXTRACTION_LOSS"]["trace"]["complete_final_mapping_count"] > 0,
            "clean_1x": indexed["CLEAN_1_0X"]["trace"]["complete_final_mapping_count"] > 0,
            "clean_0_8x": indexed["CLEAN_0_8X"]["trace"]["complete_final_mapping_count"] > 0,
            "clean_1_25x": indexed["CLEAN_1_25X"]["trace"]["complete_final_mapping_count"] > 0,
            "strong_rivals_ambiguous": (
                indexed["TWO_STRONG_RIVALS"]["status"] == MatchStatus.AMBIGUOUS.value
                and len(indexed["TWO_STRONG_RIVALS"]["predictions"])
                + len(indexed["TWO_STRONG_RIVALS"]["alternatives"]) >= 2
            ),
            "wrong_line_review_required": all(
                item["review_tier"] == "REVIEW_REQUIRED"
                for item in indexed["COHERENT_WRONG_LINE"]["predictions"]
                + indexed["COHERENT_WRONG_LINE"]["alternatives"]
            ),
            "no_catastrophic_mapping_explosion": all(
                len(indexed[name]["predictions"]) + len(indexed[name]["alternatives"])
                <= 2 * baseline_counts[name]
                for name in baseline_counts
            ),
        }
        eligibility[policy.policy_id] = {"eligible": all(criteria.values()), "criteria": criteria}
    return {
        "schema_version": "phase2b5-synthetic-v0.1",
        "completed_at": datetime.now(UTC).isoformat(),
        "protocol_commit": PROTOCOL_COMMIT,
        "baseline_reproducer": {
            "valid": baseline_valid,
            "round0_complete_proposal_count": first_round["complete_proposal_count"],
            "round0_selected_slope": first_round["selected_fit"]["mapping"]["speed_ratio"],
            "round0_selected_quality": first_round["selected_fit"]["explanation_quality"],
            "round0_selected_evaluator_complete": first_round["selected_evaluator_complete"],
            "registered_exact_line_quality_gap": registered_gap,
            "closest_complete_quality_gap": closest_gap,
            "final_complete_mapping_count": baseline_loss["trace"]["complete_final_mapping_count"],
        },
        "policy_eligibility": eligibility,
        "rows": rows,
    }


def _record_positive(
    source: dict[str, Any],
    outcome: AlignmentOutcome,
    aligner: ExperimentalExtractionAligner,
    runtime: float,
    oracle: dict[str, Any],
    comparison: dict[str, Any] | None,
) -> dict[str, Any]:
    truth = source["truth"][0]
    payload = _outcome_payload(outcome)
    final_values = payload["predictions"] + payload["alternatives"]
    trace = _labeled_trace(aligner, truth, final_values)
    metrics = evaluate_segments(
        source["truth"], list(outcome.segments), payload["status"], list(outcome.alternatives)
    )
    taxonomy = classify_case_outcome(
        payload["status"], metrics, source["truth"], list(outcome.alternatives)
    )
    fully_recalled = bool(
        metrics["source_aware_recalled_segments"] == metrics["ground_truth_segments"]
        and metrics["false_positive_segments"] == 0
    )
    final_complete = trace["complete_final_mapping_count"] > 0
    if source["backend"] == REFERENCE_BACKEND:
        assert comparison is not None
        mechanism = dino_mechanism(
            final_complete=final_complete,
            oracle=oracle,
            trace_summary={
                "complete_gt_free_proposal_count": trace["complete_gt_free_proposal_count"]
            },
            comparison=comparison,
        )
        extraction_loss = mechanism["extraction_loss"]
        representation_limit = mechanism["representation_separability_limit"]
    else:
        extraction_loss = bool(trace["complete_gt_free_proposal_count"] and not final_complete)
        representation_limit = bool(
            not final_complete
            and not oracle["nearest_per_frame_fit"].get("evaluation", {}).get("complete", False)
        )
        mechanism = {
            "extraction_loss": extraction_loss,
            "representation_separability_limit": representation_limit,
            "mechanism_cell": (
                "MIXED" if extraction_loss and representation_limit
                else "EXTRACTOR_COMPATIBILITY" if extraction_loss
                else "REPRESENTATION_SEPARABILITY" if representation_limit
                else "NEITHER_OR_UNRESOLVED"
            ),
        }
    return {
        "case_id": source["case_id"],
        "position_id": source["position_id"],
        "source_alias": source["source_alias"],
        "normalized_source_start": source["normalized_source_start"],
        "transformation": source["transformation"],
        "backend": source["backend"],
        "positive": True,
        "truth": source["truth"],
        **payload,
        "metrics": metrics,
        "outcome": taxonomy,
        "boundary_assertion_outcome": classify_boundary_assertion(
            list(outcome.segments), source["truth"], fully_recalled
        ),
        "complete_final_mapping_count": trace["complete_final_mapping_count"],
        "trace_summary": {
            "complete_gt_free_proposal_count": trace["complete_gt_free_proposal_count"],
            "complete_final_mapping_count": trace["complete_final_mapping_count"],
            "rounds": [
                {
                    "episode": item["episode"],
                    "round_index": item["round_index"],
                    "proposal_count": len(item["fits"]),
                    "complete_proposal_count": item["complete_proposal_count"],
                    "selected_slope": item["selected_fit"]["mapping"]["speed_ratio"],
                    "selected_quality": item["selected_fit"]["explanation_quality"],
                    "selected_rms_seconds": item["selected_fit"]["mapping"]["residual_rms_seconds"],
                    "selected_inlier_count": item["selected_fit"]["mapping"]["inlier_count"],
                    "selected_evaluator_complete": item["selected_evaluator_complete"],
                    "occupancy_count": item["occupancy_count"],
                    "shortlist_size": len(item["selection"]["shortlist_ordinals"]),
                }
                for item in trace["rounds"]
            ],
        },
        "mechanism": mechanism,
        "runtime_seconds": runtime,
        "_labeled_trace": trace,
    }


def _record_negative(
    source: dict[str, Any],
    outcome: AlignmentOutcome,
    aligner: ExperimentalExtractionAligner,
    runtime: float,
) -> dict[str, Any]:
    return {
        "case_id": source["case_id"],
        "position_id": source["position_id"],
        "transformation": source["transformation"],
        "backend": source["backend"],
        "positive": False,
        **_outcome_payload(outcome),
        "runtime_seconds": runtime,
        "round_trace": aligner.round_trace,
    }


def _baseline_equal(source: dict[str, Any], row: dict[str, Any]) -> bool:
    stored = {
        "status": source["status"],
        "predictions": source["predictions"],
        "alternatives": source["alternatives"],
        "warnings": source["warnings"],
        "truncated_episodes": source["truncated_episodes"],
        "conflict_graph": source["conflict_graph"],
    }
    replay = {key: row[key] for key in stored}
    return _json_contract(stored) == _json_contract(replay)


def _summary(positive: list[dict[str, Any]], negative: list[dict[str, Any]]) -> dict[str, Any]:
    metrics = _metric_summary(positive)
    taxonomy = {name: sum(item["outcome"] == name for item in positive) for name in CASE_OUTCOMES}
    if sum(taxonomy.values()) != len(positive):
        raise ValueError("taxonomy is not exhaustive")
    predictions = [item for row in positive for item in row["predictions"]]
    alternatives = [item for row in positive for item in row["alternatives"]]
    wrong_green = [
        item for row in positive for item in row["predictions"]
        if item["candidate_tier"] == "GREEN"
        and item["source_episode"] != row["truth"][0]["source_episode"]
    ]
    ambiguous_wrong_green = [
        item for row in positive if row["status"] == MatchStatus.AMBIGUOUS.value
        for item in row["predictions"]
        if item["candidate_tier"] == "GREEN"
        and item["source_episode"] != row["truth"][0]["source_episode"]
    ]
    unambiguous_wrong_green_cases = {
        row["case_id"] for row in positive
        if row["status"] == MatchStatus.MATCH_FOUND.value
        and row["predictions"]
        and row["predictions"][0]["candidate_tier"] == "GREEN"
        and row["predictions"][0]["source_episode"] != row["truth"][0]["source_episode"]
    }
    e_rows = [item for item in positive if item["mechanism"]["extraction_loss"]]
    r_rows = [item for item in positive if item["mechanism"]["representation_separability_limit"]]
    unresolved = [
        item for item in positive
        if not item["complete_final_mapping_count"]
        and not item["mechanism"]["extraction_loss"]
        and not item["mechanism"]["representation_separability_limit"]
    ]
    total_mappings = [len(item["predictions"]) + len(item["alternatives"]) for item in positive]
    negative_predictions = [item for row in negative for item in row["predictions"]]
    return {
        **metrics,
        "taxonomy": taxonomy,
        "safe": taxonomy["CORRECT_UNAMBIGUOUS_MATCH"] + taxonomy["SAFE_AMBIGUITY_CONTAINS_GT"],
        "unsafe": taxonomy["UNSAFE_WRONG_MATCH"],
        "no_match": taxonomy["NO_MATCH"],
        "status_counts": dict(Counter(item["status"] for item in positive)),
        "candidate_tiers": dict(Counter(item["candidate_tier"] for item in predictions)),
        "review_tiers": dict(Counter(item["review_tier"] for item in predictions)),
        "wrong_source_green_segment_count": len(wrong_green),
        "ambiguous_wrong_source_green_segment_count": len(ambiguous_wrong_green),
        "unambiguous_wrong_source_green_case_count": len(unambiguous_wrong_green_cases),
        "unambiguous_wrong_source_green_case_ids": sorted(unambiguous_wrong_green_cases),
        "primary_segment_count": len(predictions),
        "alternative_count": len(alternatives),
        "maximum_total_mappings_per_case": max(total_mappings, default=0),
        "cases_above_four_total_mappings": sorted(
            item["case_id"] for item, count in zip(positive, total_mappings, strict=True) if count > 4
        ),
        "complete_final_case_count": sum(item["complete_final_mapping_count"] > 0 for item in positive),
        "complete_final_mapping_count": sum(item["complete_final_mapping_count"] for item in positive),
        "extraction_loss_count": len(e_rows),
        "extraction_loss_content_position_count": len({item["position_id"] for item in e_rows}),
        "representation_limit_count": len(r_rows),
        "unresolved_count": len(unresolved),
        "negative_controls": {
            "query_count": len(negative),
            "emitted_segment_count": len(negative_predictions),
            "emitting_case_count": sum(bool(item["predictions"]) for item in negative),
            "status_counts": dict(Counter(item["status"] for item in negative)),
            "candidate_tiers": dict(Counter(item["candidate_tier"] for item in negative_predictions)),
            "review_tiers": dict(Counter(item["review_tier"] for item in negative_predictions)),
        },
    }


def _policy_summaries(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    overall: dict[str, Any] = {}
    by_transform: dict[str, Any] = {}
    for backend in BACKENDS:
        overall[backend] = {}
        by_transform[backend] = {}
        for policy in POLICIES:
            chosen = [item for item in rows if item["backend"] == backend and item["policy_id"] == policy.policy_id]
            positive = [item for item in chosen if item["positive"]]
            negative = [item for item in chosen if not item["positive"]]
            overall[backend][policy.policy_id] = _summary(positive, negative)
            by_transform[backend][policy.policy_id] = {
                transform: _summary(
                    [item for item in positive if item["transformation"] == transform], []
                )
                for transform in phase2b3.retrieval.TRANSFORMATIONS
            }
    return overall, by_transform


def _eligibility(
    overall: dict[str, Any], synthetic: dict[str, Any]
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    dino_base = overall[REFERENCE_BACKEND]["P0"]
    dct_base = overall["DCT"]["P0"]
    for policy in POLICIES[1:]:
        dino = overall[REFERENCE_BACKEND][policy.policy_id]
        dct = overall["DCT"][policy.policy_id]
        new_dino_wrong_green = set(dino["unambiguous_wrong_source_green_case_ids"]) - set(
            dino_base["unambiguous_wrong_source_green_case_ids"]
        )
        new_dct_wrong_green = set(dct["unambiguous_wrong_source_green_case_ids"]) - set(
            dct_base["unambiguous_wrong_source_green_case_ids"]
        )
        dino_inflation = dino["primary_segment_count"] / max(1, dino_base["primary_segment_count"])
        dct_inflation = dct["primary_segment_count"] / max(1, dct_base["primary_segment_count"])
        criteria = {
            "synthetic": synthetic["policy_eligibility"][policy.policy_id]["eligible"],
            "dino_safe": dino["safe"] >= 139,
            "dino_unsafe": dino["unsafe"] <= 56,
            "dino_no_match": dino["no_match"] <= 25,
            "dino_extraction_loss": dino["extraction_loss_count"] <= 27,
            "dino_extraction_loss_positions": dino["extraction_loss_content_position_count"] <= 10,
            "a010_h264": dino["a010"]["H264_REENCODE"]["safe"],
            "a010_direct": dino["a010"]["DIRECT"]["safe"],
            "a010_speed_0_8x": dino["a010"]["SPEED_0_8X"]["safe"],
            "dino_no_new_unambiguous_wrong_green": not new_dino_wrong_green,
            "dino_negative_controls": dino["negative_controls"]["emitted_segment_count"] == 0,
            "dct_safe": dct["safe"] >= 88,
            "dct_unsafe": dct["unsafe"] <= 58,
            "dct_no_new_unambiguous_wrong_green": not new_dct_wrong_green,
            "dino_segment_inflation": dino_inflation <= 1.25,
            "dct_segment_inflation": dct_inflation <= 1.25,
        }
        result[policy.policy_id] = {
            "eligible": all(criteria.values()),
            "criteria": criteria,
            "new_dino_unambiguous_wrong_green_case_ids": sorted(new_dino_wrong_green),
            "new_dct_unambiguous_wrong_green_case_ids": sorted(new_dct_wrong_green),
            "dino_segment_inflation_ratio": dino_inflation,
            "dct_segment_inflation_ratio": dct_inflation,
        }
    return result


def _selection_key(summary: dict[str, Any], policy_id: str) -> tuple[Any, ...]:
    return (
        -summary["safe"],
        summary["unsafe"],
        summary["extraction_loss_count"],
        summary["extraction_loss_content_position_count"],
        summary["no_match"],
        summary["segment_inflation_ratio"],
        SIMPLICITY_ORDER[policy_id],
    )


def _transition_matrix(
    rows: list[dict[str, Any]], backend: str, target_policy: str
) -> dict[str, Any]:
    baseline = {
        item["case_id"]: item for item in rows
        if item["positive"] and item["backend"] == backend and item["policy_id"] == "P0"
    }
    target = {
        item["case_id"]: item for item in rows
        if item["positive"] and item["backend"] == backend and item["policy_id"] == target_policy
    }
    matrix = {
        left: {right: 0 for right in CASE_OUTCOMES} for left in CASE_OUTCOMES
    }
    for case_id, left in baseline.items():
        matrix[left["outcome"]][target[case_id]["outcome"]] += 1
    e_ids = {
        case_id for case_id, item in baseline.items() if item["mechanism"]["extraction_loss"]
    }
    e_transitions = dict(Counter(target[case_id]["outcome"] for case_id in e_ids))
    return {
        "matrix": matrix,
        "baseline_extraction_loss_case_count": len(e_ids),
        "baseline_extraction_loss_transitions": e_transitions,
        "baseline_extraction_loss_still_e": sum(
            target[case_id]["mechanism"]["extraction_loss"] for case_id in e_ids
        ),
        "baseline_extraction_loss_remaining_position_count": len({
            target[case_id]["position_id"] for case_id in e_ids
            if target[case_id]["mechanism"]["extraction_loss"]
        }),
        "baseline_extraction_loss_to_no_match": sum(
            target[case_id]["outcome"] == "NO_MATCH" for case_id in e_ids
        ),
        "baseline_extraction_loss_to_new_unsafe": sum(
            baseline[case_id]["outcome"] != "UNSAFE_WRONG_MATCH"
            and target[case_id]["outcome"] == "UNSAFE_WRONG_MATCH"
            for case_id in e_ids
        ),
    }


def _mechanism_contrasts(overall: dict[str, Any]) -> dict[str, Any]:
    pairs = {
        "P0_TO_P1_RANKING_0_0005": ("P0", "P1"),
        "P0_TO_P2_RANKING_0_0010": ("P0", "P2"),
        "P0_TO_P3_RANKING_0_0020": ("P0", "P3"),
        "P0_TO_P4_OCCUPANCY_ONLY": ("P0", "P4"),
        "P2_TO_P5_OCCUPANCY_INTERACTION_0_0010": ("P2", "P5"),
        "P3_TO_P6_OCCUPANCY_INTERACTION_0_0020": ("P3", "P6"),
    }
    output: dict[str, Any] = {}
    for name, (left, right) in pairs.items():
        output[name] = {}
        for backend in BACKENDS:
            a, b = overall[backend][left], overall[backend][right]
            output[name][backend] = {
                key: b[key] - a[key]
                for key in (
                    "safe", "unsafe", "no_match", "extraction_loss_count",
                    "extraction_loss_content_position_count", "primary_segment_count",
                    "alternative_count",
                )
            }
    return output


def _fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_dev(dev_path: Path, synthetic_path: Path, selected_path: Path) -> dict[str, Any]:
    verify_stage(dev_path)
    dev = _read(dev_path)
    source_rows = validate_dev(dev)
    synthetic = _read(synthetic_path)
    if synthetic.get("protocol_commit") != PROTOCOL_COMMIT or not synthetic.get("baseline_reproducer", {}).get("valid"):
        raise ValueError("valid registered synthetic stage must precede DEV")

    positives = [item for item in source_rows if item["positive"]]
    metric_cache: dict[tuple[str, str], dict[str, Any]] = {}
    oracle_cache: dict[tuple[str, str], dict[str, Any]] = {}
    comparisons: dict[str, dict[str, Any]] = {}
    for source in positives:
        key = (source["case_id"], source["backend"])
        truth = source["truth"][0]
        metric_cache[key] = temporal_separability_metrics(source["candidate_audit"], truth)
        oracle_cache[key] = oracle_band_diagnostics(source["candidate_audit"], truth)
    for case_id in {item["case_id"] for item in positives}:
        comparisons[case_id] = _separability_comparison(
            metric_cache[(case_id, REFERENCE_BACKEND)], metric_cache[(case_id, "DCT")]
        )

    started = time.perf_counter()
    rows: list[dict[str, Any]] = []
    baseline_e_ids: set[str] = set()
    primary_traces: dict[str, Any] = {}
    for policy in POLICIES:
        for backend in BACKENDS:
            chosen = sorted(
                (item for item in source_rows if item["backend"] == backend),
                key=lambda item: item["case_id"],
            )
            for ordinal, source in enumerate(chosen, 1):
                candidates = candidates_from_audit(source["candidate_audit"])
                outcome, aligner, runtime = _run_one(candidates, policy)
                if source["positive"]:
                    row = _record_positive(
                        source,
                        outcome,
                        aligner,
                        runtime,
                        oracle_cache[(source["case_id"], backend)],
                        comparisons[source["case_id"]] if backend == REFERENCE_BACKEND else None,
                    )
                else:
                    row = _record_negative(source, outcome, aligner, runtime)
                row["policy_id"] = policy.policy_id
                row["epsilon"] = policy.epsilon
                row["occupancy_mode"] = policy.occupancy_mode
                if policy.policy_id == "P0" and not _baseline_equal(source, row):
                    raise ValueError(f"P0 replay differs from stored frozen output: {source['case_id']} {backend}")
                if (
                    policy.policy_id == "P0"
                    and backend == REFERENCE_BACKEND
                    and source["positive"]
                    and row["mechanism"]["extraction_loss"]
                ):
                    baseline_e_ids.add(source["case_id"])
                if source["positive"] and source["case_id"] in PRIMARY_CASES:
                    primary_traces[f"{backend}:{policy.policy_id}:{source['case_id']}"] = row.get(
                        "_labeled_trace"
                    )
                # Detailed proposal membership is needed only for the registered primary
                # cases and the 37 baseline-DINO E comparisons.  Discard it immediately
                # elsewhere so the 7 x 2 x 240 matrix remains bounded in memory.
                retain_trace = bool(
                    source["positive"]
                    and backend == REFERENCE_BACKEND
                    and (
                        (policy.policy_id == "P0")
                        or source["case_id"] in baseline_e_ids
                    )
                )
                if not retain_trace:
                    row.pop("_labeled_trace", None)
                rows.append(row)
                if ordinal % 80 == 0:
                    print(f"{policy.policy_id} {backend}: {ordinal}/{len(chosen)}", flush=True)

    if len(baseline_e_ids) != 37 or len({
        item["position_id"] for item in rows
        if item["policy_id"] == "P0" and item["backend"] == REFERENCE_BACKEND
        and item["case_id"] in baseline_e_ids
    }) != 13:
        raise ValueError("P0 extraction-loss baseline does not reproduce Phase 2B-4")

    overall, by_transform = _policy_summaries(rows)
    for backend in BACKENDS:
        for policy in POLICIES:
            current = overall[backend][policy.policy_id]
            current["segment_inflation_ratio"] = (
                current["primary_segment_count"]
                / max(1, overall[backend]["P0"]["primary_segment_count"])
            )
            current["a010"] = {}
            for transform in ("DIRECT", "H264_REENCODE", "SPEED_0_8X"):
                case_id = f"SOURCE_A-POSITION_010-{transform}"
                row = next(
                    item for item in rows
                    if item["positive"] and item["backend"] == backend
                    and item["policy_id"] == policy.policy_id and item["case_id"] == case_id
                )
                current["a010"][transform] = {
                    "outcome": row["outcome"],
                    "status": row["status"],
                    "safe": row["outcome"] in {
                        "CORRECT_UNAMBIGUOUS_MATCH", "SAFE_AMBIGUITY_CONTAINS_GT"
                    },
                    "complete_final_mapping_count": row["complete_final_mapping_count"],
                    "wrong_primary_green": bool(
                        row["predictions"]
                        and row["predictions"][0]["source_episode"] != row["truth"][0]["source_episode"]
                        and row["predictions"][0]["candidate_tier"] == "GREEN"
                    ),
                    "review_tiers": sorted({item["review_tier"] for item in row["predictions"]}),
                    "trace_summary": row["trace_summary"],
                }

    eligibility = _eligibility(overall, synthetic)
    qualified = [policy_id for policy_id, value in eligibility.items() if value["eligible"]]
    ranking_pool = qualified or [item.policy_id for item in POLICIES[1:]]
    best_policy = min(
        ranking_pool,
        key=lambda policy_id: _selection_key(overall[REFERENCE_BACKEND][policy_id], policy_id),
    )
    selected_policy = best_policy if qualified else None
    target_policy = selected_policy or best_policy
    transitions = _transition_matrix(rows, REFERENCE_BACKEND, target_policy)

    e_level = []
    for case_id in sorted(baseline_e_ids):
        pair = {}
        for policy_id in ("P0", target_policy):
            row = next(
                item for item in rows if item["case_id"] == case_id
                and item["backend"] == REFERENCE_BACKEND and item["policy_id"] == policy_id
            )
            labeled = row.pop("_labeled_trace")
            pair[policy_id] = {
                "trace": labeled,
                "final_complete_mapping_count": row["complete_final_mapping_count"],
                "outcome": row["outcome"],
                "still_extraction_loss": row["mechanism"]["extraction_loss"],
            }
        e_level.append({"case_id": case_id, "comparison": pair})
    for row in rows:
        row.pop("_labeled_trace", None)

    result = {
        "schema_version": "phase2b5-dev-v0.1",
        "completed_at": datetime.now(UTC).isoformat(),
        "protocol_commit": PROTOCOL_COMMIT,
        "start_commit": START_COMMIT,
        "production_commit": PRODUCTION_COMMIT,
        "reference_backend": REFERENCE_BACKEND,
        "data_policy": {
            "dev_only": True,
            "content_position_count": 20,
            "positive_cell_count_per_backend_policy": 220,
            "negative_query_count_per_backend_policy": 20,
            "fresh_data_exists": False,
            "selected_score_contract_exists": False,
        },
        "policies": [asdict(item) for item in POLICIES],
        "synthetic_eligibility": synthetic["policy_eligibility"],
        "rows": rows,
        "overall": overall,
        "by_transformation": by_transform,
        "eligibility": eligibility,
        "qualified_policies": qualified,
        "selected_experimental_policy": selected_policy,
        "best_policy_if_none_qualified": None if qualified else best_policy,
        "transition_target_policy": target_policy,
        "transitions": transitions,
        "mechanism_contrasts": _mechanism_contrasts(overall),
        "baseline_extraction_loss_case_ids": sorted(baseline_e_ids),
        "primary_traces": primary_traces,
        "e_level_traces": e_level,
        "runtime_seconds": time.perf_counter() - started,
    }
    if selected_policy is not None:
        if selected_path.exists():
            raise ValueError("selected experimental policy already exists")
        selected = POLICY_BY_ID[selected_policy]
        identity_files = [
            ROOT / "experiments/phase2b5/policies.py",
            ROOT / "experiments/phase2b5/aligner.py",
            ROOT / "experiments/phase2b5/synthetic.py",
            ROOT / "experiments/phase2b5/benchmark.py",
            ROOT / "src/ipvm/matching.py",
            ROOT / "src/ipvm/benchmarking.py",
            ROOT / "config/default.yaml",
            dev_path,
        ]
        selected_payload = {
            "schema_version": "phase2b5-selected-experimental-policy-v0.1",
            "protocol_commit": PROTOCOL_COMMIT,
            "policy": asdict(selected),
            "selection_key": list(_selection_key(overall[REFERENCE_BACKEND][selected_policy], selected_policy)),
            "selection_reason": "eligible constraints first, then the pre-registered lexicographic DEV key",
            "dino_dev_metrics": overall[REFERENCE_BACKEND][selected_policy],
            "dct_dev_metrics": overall["DCT"][selected_policy],
            "synthetic_metrics": synthetic["policy_eligibility"][selected_policy],
            "fingerprints": {str(path.relative_to(ROOT)): _fingerprint(path) for path in identity_files},
            "production_authorized": False,
            "fresh_validation_authorized": False,
        }
        _write_exclusive(selected_path, selected_payload)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    synthetic = sub.add_parser("synthetic")
    synthetic.add_argument(
        "--output", type=Path, default=ROOT / "workspace/phase2b5/synthetic.json"
    )
    dev = sub.add_parser("dev")
    dev.add_argument("--input", type=Path, default=ROOT / "workspace/phase2b3/dev.json")
    dev.add_argument(
        "--synthetic", type=Path, default=ROOT / "workspace/phase2b5/synthetic.json"
    )
    dev.add_argument("--output", type=Path, default=ROOT / "workspace/phase2b5/dev.json")
    dev.add_argument(
        "--selected", type=Path,
        default=ROOT / "workspace/phase2b5/selected-experimental-policy.json",
    )
    args = parser.parse_args()
    if args.command == "synthetic":
        _write_exclusive(args.output, run_synthetic())
        print(args.output)
    else:
        result = run_dev(args.input, args.synthetic, args.selected)
        _write_exclusive(args.output, result)
        print(args.output)


if __name__ == "__main__":
    main()
