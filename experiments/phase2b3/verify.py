"""Independent Phase 2B-3 integrity and anti-leak verification probes."""

from __future__ import annotations

import argparse
from collections import Counter
import copy
from dataclasses import asdict
import json
from pathlib import Path
from typing import Any

from experiments.phase2b3 import benchmark as b
from experiments.phase2b3.adapter import (
    ALIGNER_ADMISSION_SCORE,
    POLICY_BY_NAME,
    POLICY_GRID,
    adapt_candidate_frames,
    anchored_affine_score,
    candidate_identity,
)
from experiments.phase2b3.reporting import validate_sanitized
from ipvm.config import MatchingConfig
from ipvm.matching import TemporalAligner
from ipvm.models import CandidateMatch


def primary_matches(candidate: dict[str, Any], truth: dict[str, Any]) -> bool:
    """Reviewer-owned implementation of the frozen one-GT primary contract."""

    accused_span = candidate["accused_end"] - candidate["accused_start"]
    source_span = candidate["source_end"] - candidate["source_start"]
    overlap = max(
        0.0,
        min(candidate["accused_end"], truth["accused_end"])
        - max(candidate["accused_start"], truth["accused_start"]),
    )
    if overlap < 0.5 * (truth["accused_end"] - truth["accused_start"]):
        return False
    if candidate["source_episode"] != truth["source_episode"]:
        return False
    if max(abs(candidate[key] - truth[key]) for key in ("source_start", "source_end")) > 1.0:
        return False
    if accused_span <= 0 or source_span <= 0:
        return False
    if abs(source_span - accused_span * truth["speed_ratio"]) > 1.0:
        return False
    if min(accused_span, source_span) <= 3.0:
        return candidate["speed_ratio"] is None
    return (
        candidate["speed_ratio"] is not None
        and abs(source_span - accused_span * candidate["speed_ratio"]) <= 1.0
    )


def alternative_matches(candidate: dict[str, Any], truth: dict[str, Any]) -> bool:
    if candidate["source_episode"] != truth["source_episode"]:
        return False
    if max(
        abs(candidate[key] - truth[key])
        for key in ("source_start", "source_end", "accused_start", "accused_end")
    ) > 1.0:
        return False
    accused_span = candidate["accused_end"] - candidate["accused_start"]
    if accused_span <= 0 or abs(accused_span - (truth["accused_end"] - truth["accused_start"])) > 1.0:
        return False
    mapped_speed = (candidate["source_end"] - candidate["source_start"]) / accused_span
    return (
        candidate["speed_ratio"] is not None
        and abs(mapped_speed - truth["speed_ratio"]) <= 0.15
        and abs(candidate["speed_ratio"] - truth["speed_ratio"]) <= 0.15
    )


def classify(row: dict[str, Any]) -> str:
    predictions = row["predictions"]
    truth = row["truth"][0]
    fully_correct = len(predictions) == 1 and primary_matches(predictions[0], truth)
    if row["status"] == "AMBIGUOUS":
        if fully_correct or any(alternative_matches(item, truth) for item in row["alternatives"]):
            return "SAFE_AMBIGUITY_CONTAINS_GT"
        return "UNSAFE_WRONG_MATCH" if predictions else "NO_MATCH"
    if row["status"] == "MATCH_FOUND":
        return "CORRECT_UNAMBIGUOUS_MATCH" if fully_correct else "UNSAFE_WRONG_MATCH"
    return "UNSAFE_WRONG_MATCH" if predictions else "NO_MATCH"


def frame_input(row: dict[str, Any]) -> list[dict[str, Any]]:
    frames: dict[float, dict[str, Any]] = {}
    for item in row["candidate_audit"]:
        accused_time = item["accused_time"]
        frames.setdefault(accused_time, {"accused_time": accused_time, "top8": []})["top8"].append(
            {
                "accused_time": accused_time,
                "source_time": item["source_time"],
                "source_id": item["source_id"],
                "rank": item["rank"],
                "score": item["raw_cosine"],
            }
        )
    return [frames[key] for key in sorted(frames)]


def _identity_mismatch_count(dev: dict[str, Any]) -> int:
    rows = [row for row in dev["cases"] if row["backend"] != "DCT"]
    by_case: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_case.setdefault(row["case_id"], []).append(row)
    mismatches = 0
    for group in by_case.values():
        if {row["backend"] for row in group} != {policy.name for policy in POLICY_GRID}:
            mismatches += 1
            continue
        identities = {
            tuple(
                (
                    item["accused_time"],
                    item["source_time"],
                    item["source_id"],
                    item["rank"],
                    item["raw_cosine"],
                )
                for item in row["candidate_audit"]
            )
            for row in group
        }
        mismatches += len(identities) != 1
    return mismatches


def _mapping_audit(*stages: dict[str, Any]) -> dict[str, int]:
    checked = 0
    mismatched = 0
    threshold_mismatched = 0
    retained_false = 0
    for stage in stages:
        for row in stage["cases"]:
            if row["backend"] == "DCT":
                continue
            policy = POLICY_BY_NAME[row["backend"]]
            for item in row["candidate_audit"]:
                expected = anchored_affine_score(item["raw_cosine"], policy)
                checked += 1
                mismatched += abs(expected - item["adapted_visual_score"]) > 1e-12
                threshold_mismatched += (
                    (item["raw_cosine"] + 1e-12 >= policy.tau)
                    != (item["adapted_visual_score"] + 1e-12 >= ALIGNER_ADMISSION_SCORE)
                )
                retained_false += item["retained"] is not True
    return {
        "adapted_candidate_count": checked,
        "score_mismatch_count": mismatched,
        "effective_threshold_mismatch_count": threshold_mismatched,
        "not_retained_count": retained_false,
    }


def _anti_leak_probes(selected_name: str, dev: dict[str, Any]) -> dict[str, Any]:
    row = next(
        row
        for row in dev["cases"]
        if row["backend"] == selected_name and row["positive"] and row["transformation"] == "DIRECT"
    )
    frames = frame_input(row)
    original = adapt_candidate_frames(frames, POLICY_BY_NAME[selected_name])
    poisoned = copy.deepcopy(list(reversed(frames)))
    for frame in poisoned:
        frame["ground_truth_episode"] = "POISONED_GT"
        frame["expected_source_time"] = -999999.0
        frame["top8"].reverse()
    mutated = adapt_candidate_frames(poisoned, POLICY_BY_NAME[selected_name])

    aligner = TemporalAligner(MatchingConfig())
    aligned = aligner.align_with_diagnostics(list(original.candidates), index_complete=True)
    repeated = aligner.align_with_diagnostics(list(original.candidates), index_complete=True)
    permuted = aligner.align_with_diagnostics(list(reversed(original.candidates)), index_complete=True)

    correct_band = [CandidateMatch(index / 2, "LINE_A", 100 + index / 2, 0.99) for index in range(20)]
    full = aligner.align_with_diagnostics(correct_band, index_complete=True)
    removed = aligner.align_with_diagnostics(correct_band[6:], index_complete=True)
    full_span = sum(item.accused_end - item.accused_start for item in full.segments)
    reduced_span = sum(item.accused_end - item.accused_start for item in removed.segments)

    competing = [
        CandidateMatch(index / 2, source, offset + index / 2, 0.99)
        for index in range(20)
        for source, offset in (("LINE_A", 100), ("LINE_B", 200))
    ]
    contest = aligner.align_with_diagnostics(competing, index_complete=True)
    wrong = aligner.align_with_diagnostics(
        [item for item in competing if item.source_episode == "LINE_B"],
        index_complete=True,
    )
    return {
        "gt_mutation_and_adapter_permutation_stream_identical": original == mutated,
        "candidate_identity_identical_after_gt_mutation": (
            candidate_identity(original.audit) == candidate_identity(mutated.audit)
        ),
        "repeat_alignment_identical": asdict(aligned) == asdict(repeated),
        "direct_candidate_permutation_alignment_identical": asdict(aligned) == asdict(permuted),
        "correct_band_removal": {
            "before_segment_count": len(full.segments),
            "after_segment_count": len(removed.segments),
            "before_total_accused_span": full_span,
            "after_total_accused_span": reduced_span,
            "coverage_degraded": reduced_span < full_span,
        },
        "two_strong_cross_source_lines": {
            "ambiguous": contest.ambiguous,
            "alternative_count": len(contest.alternatives),
            "production_review_tiers": sorted({item.review_tier for item in contest.segments}),
        },
        "one_coherent_wrong_line": {
            "emitted_segment_count": len(wrong.segments),
            "candidate_green_count": sum(item.candidate_tier == "GREEN" for item in wrong.segments),
            "production_review_tiers": sorted({item.review_tier for item in wrong.segments}),
        },
    }


def verify(
    dev: dict[str, Any],
    selected: dict[str, Any],
    held: dict[str, Any],
    old_corpus: dict[str, Any],
    old_negatives: dict[str, Any],
    fresh_corpus: dict[str, Any],
    fresh_negatives: dict[str, Any],
) -> dict[str, Any]:
    b.verify_protocol()
    if selected["execution_identity"]["implementation"] != b.implementation_identity():
        raise ValueError("current implementation differs from frozen implementation")
    if dev["implementation_identity"] != selected["execution_identity"]:
        raise ValueError("DEV and selection identities differ")
    if held["implementation_identity"] != selected["execution_identity"]:
        raise ValueError("held-out and selection identities differ")
    b.validate_dev_inputs(old_corpus, old_negatives)
    b.validate_fresh_inputs(old_corpus, fresh_corpus, fresh_negatives, selected)
    selected_name = selected["selected_policy"]
    if set(held["summaries"]) != {"DCT", selected_name}:
        raise ValueError("fresh held-out contains unselected policy")

    positives = [
        row for stage in (dev, held) for row in stage["cases"] if row["positive"]
    ]
    taxonomy_mismatches = sum(classify(row) != row["outcome"] for row in positives)
    all_audits = [
        item
        for stage in (dev, held)
        for row in stage["cases"]
        for item in row["candidate_audit"]
    ]
    source_grid_off = sum(
        abs(item["source_time"] * 2 - round(item["source_time"] * 2)) > 1e-6
        for item in all_audits
    )
    release_violations = sum(
        prediction["review_tier"] != "REVIEW_REQUIRED"
        for stage in (dev, held)
        for row in stage["cases"]
        for prediction in row["predictions"]
    )
    mapping = _mapping_audit(dev, held)
    probes = _anti_leak_probes(selected_name, dev)

    old_by_source = {
        source: [
            float(item["source_start_seconds"])
            for item in old_corpus["positions"]
            if item["source_alias"] == source
        ]
        for source in ("SOURCE_A", "SOURCE_B")
    }
    fresh_by_source = {
        source: sorted(
            float(item["source_start_seconds"])
            for item in fresh_corpus["positions"]
            if item["source_alias"] == source
        )
        for source in ("SOURCE_A", "SOURCE_B")
    }
    old_gap = min(
        abs(fresh - old) - b.FRESH_SPAN_SECONDS
        for source in fresh_by_source
        for fresh in fresh_by_source[source]
        for old in old_by_source[source]
    )
    fresh_gap = min(
        right - left - b.FRESH_SPAN_SECONDS
        for starts in fresh_by_source.values()
        for left, right in zip(starts, starts[1:])
    )
    actual_lengths = Counter(
        str(length)
        for stage in (dev, held)
        for item in stage["inference"]
        for length in item["actual_batch_lengths"]
    )
    result = {
        "schema_version": "phase2b3-verification-v0.1",
        "protocol_commit": b.PROTOCOL_COMMIT,
        "lifecycle": {
            "dev_completed_before_policy_freeze": dev["completed_at"] <= selected["frozen_at"],
            "policy_frozen_before_fresh_generation": (
                selected["frozen_at"]
                <= fresh_corpus["provenance"]["fresh_generation_started_at"]
            ),
            "fresh_generation_before_held_out": (
                fresh_corpus["provenance"]["fresh_generation_completed_at"]
                <= held["completed_at"]
            ),
            "held_out_backend_count": len(held["summaries"]),
            "unselected_held_out_policy_count": len(set(held["summaries"]) - {"DCT", selected_name}),
        },
        "independent_taxonomy": {
            "positive_backend_case_count": len(positives),
            "mismatch_count": taxonomy_mismatches,
        },
        "candidate_identity": {
            "dev_case_family_count": len({row["case_id"] for row in dev["cases"]}),
            "cross_tau_mismatch_count": _identity_mismatch_count(dev),
        },
        "score_mapping": mapping,
        "mathematical_special_cases": {
            "tau_0_64_max_dct_affine_error": max(
                abs(anchored_affine_score(value, POLICY_BY_NAME["TAU_0.64"]) - (value + 1) / 2)
                for value in (-1.0, -0.5, 0.0, 0.64, 0.82, 1.0)
            ),
            "tau_0_82_max_identity_error_on_unit_interval": max(
                abs(anchored_affine_score(value, POLICY_BY_NAME["TAU_0.82"]) - value)
                for value in (0.0, 0.25, 0.55, 0.82, 0.95, 1.0)
            ),
            "all_policy_mappings_monotonic": all(
                all(
                    anchored_affine_score(left, policy) <= anchored_affine_score(right, policy)
                    for left, right in zip(
                        (-1.0, 0.0, 0.55, 0.64, 0.82, 0.95),
                        (0.0, 0.55, 0.64, 0.82, 0.95, 1.0),
                    )
                )
                for policy in POLICY_GRID
            ),
        },
        "source_grid": {
            "audited_candidate_count_including_policy_replays": len(all_audits),
            "off_grid_count": source_grid_off,
        },
        "batching": {
            "configured_batch_size": 16,
            "actual_batch_length_distribution": dict(sorted(actual_lengths.items(), key=lambda item: int(item[0]))),
            "oversized_batch_count": sum(int(size) > 16 for size in actual_lengths for _ in range(actual_lengths[size])),
        },
        "fresh_sampling": {
            "fresh_position_count": len(fresh_corpus["positions"]),
            "minimum_old_to_fresh_interval_gap_seconds": old_gap,
            "minimum_fresh_to_fresh_interval_gap_seconds": fresh_gap,
            "deterministic_selector_exact_match": fresh_corpus["positions"] == b.select_fresh_positions(old_corpus),
        },
        "anti_leak_probes": probes,
        "release_safety": {
            "non_review_required_prediction_count": release_violations,
            "candidate_green_remains_diagnostic_only": release_violations == 0,
        },
        "promotion_gate_recalculation": b.promotion_gate(held),
        "privacy": {
            "contains_media_identity": False,
            "contains_paths": False,
            "contains_private_hashes": False,
            "contains_per_frame_records": False,
            "contains_exact_timelines": False,
        },
    }
    required = (
        taxonomy_mismatches == 0,
        source_grid_off == 0,
        release_violations == 0,
        _identity_mismatch_count(dev) == 0,
        mapping["score_mismatch_count"] == 0,
        mapping["effective_threshold_mismatch_count"] == 0,
        mapping["not_retained_count"] == 0,
        probes["gt_mutation_and_adapter_permutation_stream_identical"],
        probes["candidate_identity_identical_after_gt_mutation"],
        probes["repeat_alignment_identical"],
        probes["direct_candidate_permutation_alignment_identical"],
        probes["correct_band_removal"]["coverage_degraded"],
        probes["two_strong_cross_source_lines"]["ambiguous"],
        probes["one_coherent_wrong_line"]["production_review_tiers"] == ["REVIEW_REQUIRED"],
        old_gap >= 30.0 - 1e-6,
        fresh_gap >= 30.0 - 1e-6,
    )
    if not all(required):
        raise ValueError("one or more Phase 2B-3 integrity probes failed")
    validate_sanitized(result)
    return result


def verify_dev_stop(dev: dict[str, Any], workspace: Path) -> dict[str, Any]:
    """Verify the registered early stop without requiring nonexistent fresh data."""

    b.verify_protocol()
    if dev.get("stage") != "DEV" or dev.get("protocol_commit") != b.PROTOCOL_COMMIT:
        raise ValueError("invalid DEV result provenance")
    if dev["implementation_identity"]["implementation"] != b.implementation_identity():
        raise ValueError("current implementation differs from DEV identity")
    selection = b.select_policy(dev)
    if selection["selected_policy"] is not None or selection["eligible_policies"]:
        raise ValueError("DEV early-stop verification requires zero eligible policies")
    forbidden = (
        workspace / "selected-score-contract.json",
        workspace / "fresh",
        workspace / "fresh-corpus.json",
        workspace / "fresh-negatives.json",
        workspace / "fresh-held-out.json",
    )
    if any(path.exists() for path in forbidden):
        raise ValueError("selection/fresh artifact exists after mandatory DEV stop")

    positives = [row for row in dev["cases"] if row["positive"]]
    taxonomy_mismatches = sum(classify(row) != row["outcome"] for row in positives)
    audits = [item for row in dev["cases"] for item in row["candidate_audit"]]
    source_grid_off = sum(
        abs(item["source_time"] * 2 - round(item["source_time"] * 2)) > 1e-6
        for item in audits
    )
    release_violations = sum(
        prediction["review_tier"] != "REVIEW_REQUIRED"
        for row in dev["cases"]
        for prediction in row["predictions"]
    )
    mapping = _mapping_audit(dev)
    probes = _anti_leak_probes("TAU_0.64", dev)
    batch_lengths = Counter(
        str(length)
        for item in dev["inference"]
        for length in item["actual_batch_lengths"]
    )
    negative_emissions = {
        backend: dev["summaries"][backend]["negatives"]["emitted"]
        for backend in dev["summaries"]
    }
    result = {
        "schema_version": "phase2b3-dev-stop-verification-v0.1",
        "protocol_commit": b.PROTOCOL_COMMIT,
        "lifecycle": {
            "stage_reached": "DEV_COMPLETE",
            "eligible_policy_count": 0,
            "selected_policy_exists": False,
            "fresh_data_exists": False,
            "fresh_held_out_executed": False,
        },
        "independent_taxonomy": {
            "positive_backend_case_count": len(positives),
            "mismatch_count": taxonomy_mismatches,
        },
        "candidate_identity": {
            "dev_case_family_count": len({row["case_id"] for row in dev["cases"]}),
            "cross_tau_mismatch_count": _identity_mismatch_count(dev),
        },
        "score_mapping": mapping,
        "mathematical_special_cases": {
            "tau_0_64_max_dct_affine_error": max(
                abs(anchored_affine_score(value, POLICY_BY_NAME["TAU_0.64"]) - (value + 1) / 2)
                for value in (-1.0, -0.5, 0.0, 0.64, 0.82, 1.0)
            ),
            "tau_0_82_max_identity_error_on_unit_interval": max(
                abs(anchored_affine_score(value, POLICY_BY_NAME["TAU_0.82"]) - value)
                for value in (0.0, 0.25, 0.55, 0.82, 0.95, 1.0)
            ),
            "all_policy_mappings_monotonic": all(
                all(
                    anchored_affine_score(left, policy) <= anchored_affine_score(right, policy)
                    for left, right in zip(
                        (-1.0, 0.0, 0.55, 0.64, 0.82, 0.95),
                        (0.0, 0.55, 0.64, 0.82, 0.95, 1.0),
                    )
                )
                for policy in POLICY_GRID
            ),
        },
        "source_grid": {
            "audited_candidate_count_including_policy_replays": len(audits),
            "off_grid_count": source_grid_off,
        },
        "batching": {
            "configured_batch_size": 16,
            "actual_batch_length_distribution": dict(
                sorted(batch_lengths.items(), key=lambda item: int(item[0]))
            ),
            "oversized_batch_count": sum(
                count for size, count in batch_lengths.items() if int(size) > 16
            ),
        },
        "negative_safety": {
            "emitted_segments_by_backend": negative_emissions,
            "all_zero": not any(negative_emissions.values()),
        },
        "a010_rival_regression": {
            backend: b.a010_regression(dev["cases"], backend)["passed"]
            for backend in dev["summaries"]
        },
        "anti_leak_probes": {"probe_policy": "TAU_0.64", **probes},
        "release_safety": {
            "non_review_required_prediction_count": release_violations,
            "candidate_green_remains_diagnostic_only": release_violations == 0,
        },
        "privacy": {
            "contains_media_identity": False,
            "contains_paths": False,
            "contains_private_hashes": False,
            "contains_per_frame_records": False,
            "contains_exact_timelines": False,
        },
    }
    required = (
        taxonomy_mismatches == 0,
        source_grid_off == 0,
        release_violations == 0,
        _identity_mismatch_count(dev) == 0,
        mapping["score_mismatch_count"] == 0,
        mapping["effective_threshold_mismatch_count"] == 0,
        mapping["not_retained_count"] == 0,
        not any(negative_emissions.values()),
        probes["gt_mutation_and_adapter_permutation_stream_identical"],
        probes["candidate_identity_identical_after_gt_mutation"],
        probes["repeat_alignment_identical"],
        probes["direct_candidate_permutation_alignment_identical"],
        probes["correct_band_removal"]["coverage_degraded"],
        probes["two_strong_cross_source_lines"]["ambiguous"],
        probes["one_coherent_wrong_line"]["production_review_tiers"] == ["REVIEW_REQUIRED"],
    )
    if not all(required):
        raise ValueError("one or more DEV-stop integrity probes failed")
    validate_sanitized(result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=b.ROOT / "workspace/phase2b3")
    parser.add_argument("--corpus", type=Path, default=b.ROOT / "workspace/phase2b1/raw/corpus.json")
    parser.add_argument("--negatives", type=Path, default=b.ROOT / "workspace/phase2b1/raw/negatives.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    dev = b.read(args.workspace / "dev.json")
    if not (args.workspace / "selected-score-contract.json").exists():
        result = verify_dev_stop(dev, args.workspace)
    else:
        result = verify(
            dev,
            b.read(args.workspace / "selected-score-contract.json"),
            b.read(args.workspace / "fresh-held-out.json"),
            b.read(args.corpus),
            b.read(args.negatives),
            b.read(args.workspace / "fresh-corpus.json"),
            b.read(args.workspace / "fresh-negatives.json"),
        )
    b.write(args.output, result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
