from __future__ import annotations

import json

from ipvm.models import MatchStatus, SegmentMatch
from ipvm.temporal_benchmark import (
    IdealTemporalBenchmark,
    REQUIRED_IDEAL_BEHAVIOURAL_ASSERTIONS,
    _alternatives_contain_ground_truth,
    _ideal_gate_passed,
    _retrieval_sufficiency,
    _supported_run_statistics,
    classify_boundary_assertion,
    classify_case_outcome,
    write_temporal_report,
)


def test_ideal_gate_requires_aggregate_all_required_assertions_and_grid() -> None:
    aggregate = {
        "source_aware_segment_recall_at_1s": 1.0,
        "segment_precision_at_1s": 1.0,
    }
    assertions = {
        name: True for name in REQUIRED_IDEAL_BEHAVIOURAL_ASSERTIONS
    }
    grid = {"all_candidates_on_grid": True}
    assert _ideal_gate_passed(aggregate, assertions, grid) is True

    failed_discontinuity = dict(assertions)
    failed_discontinuity["speed_discontinuity_source_aware"] = False
    assert _ideal_gate_passed(aggregate, failed_discontinuity, grid) is False

    assert _ideal_gate_passed(
        aggregate, assertions, {"all_candidates_on_grid": False}
    ) is False

    missing_required = dict(assertions)
    missing_required.pop("pure_noise_produces_no_segments")
    assert _ideal_gate_passed(aggregate, missing_required, grid) is False


def test_ideal_temporal_benchmark_covers_speed_jitter_and_piecewise(tmp_path) -> None:
    report = IdealTemporalBenchmark().run()
    assert report["passed"] is True
    assert report["speed_sweep"] == [0.75, 0.8, 0.9, 1.0, 1.1, 1.25, 1.4, 1.5]
    assert report["aggregate"]["source_aware_segment_recall_at_1s"] == 1.0
    assert report["aggregate"]["segment_precision_at_1s"] == 1.0
    assert report["aggregate"]["false_positive_segment_count"] == 0
    assert report["source_grid_realistic"] is True
    invariant = report["source_grid_invariant"]
    assert invariant["off_grid_candidate_count"] == 0, invariant["off_grid_cases"]
    behavioural = report["behavioural_assertions"]
    assert behavioural["pure_noise_produces_no_segments"] is True
    assert behavioural["no_clamped_speed_reported"] is True
    assert behavioural["out_of_range_semantics_complete"] is True
    assert behavioural["speed_discontinuity_source_aware"] is True
    assert behavioural["competing_lines_are_ambiguous"] is True
    assert behavioural["short_source_span_speed_is_null"] is True
    taxonomy = report["outcome_taxonomy"]
    assert sum(taxonomy["counts"].values()) == taxonomy["case_count"]
    piecewise = next(item for item in report["cases"] if item["case_id"] == "IDEAL_PIECEWISE_5")
    assert piecewise["metrics"]["predicted_segments"] == 5
    assert sum(item["source_reuse"] for item in piecewise["predictions"]) == 2
    jitter = next(item for item in report["cases"] if item["case_id"] == "IDEAL_GRID_JITTER")
    assert jitter["metrics"]["source_aware_recall_at_1s"] == 1.0
    discontinuity = next(
        item for item in report["cases"]
        if item["case_id"] == "IDEAL_SPEED_DISCONTINUITY"
    )
    assert discontinuity["metrics"]["source_aware_recalled_segments"] == 2
    assert discontinuity["metrics"]["predicted_segments"] == 2

    path = tmp_path / "ideal.json"
    write_temporal_report(report, path)
    parsed = json.loads(path.read_text(encoding="utf-8"))
    assert parsed["visual_retrieval_involved"] is False
    assert parsed["configuration"]["candidate_timestamp_uncertainty_grid_steps"] == 1


def _segment(**overrides):
    values = {
        "accused_start": 0.0,
        "accused_end": 6.0,
        "source_episode": "EP01",
        "source_start": 10.0,
        "source_end": 16.0,
        "speed_ratio": 1.0,
        "visual_score": 0.99,
        "audio_score": None,
        "temporal_score": 1.0,
        "match_score": 0.99,
    }
    values.update(overrides)
    return SegmentMatch(**values)


def test_supported_run_statistics_never_counts_tolerated_holes_as_hits() -> None:
    stats = _supported_run_statistics({0, 3, 6}, 7, 1.5, 2.0)
    assert stats == {
        "longest_run_actual_hit_count": 3,
        "longest_run_temporal_span_seconds": 3.5,
        "tolerated_hole_count": 4,
        "supported_accused_start": 0.0,
        "supported_accused_end": 3.5,
    }


def test_partial_retrieval_support_is_not_full_boundary_support() -> None:
    stats = _supported_run_statistics({0, 1, *range(5, 12)}, 12, 1.5, 2.0)
    any_match, full_boundary = _retrieval_sufficiency(stats, 12, 6, 2.0)
    assert stats["longest_run_actual_hit_count"] == 7
    assert stats["supported_accused_start"] == 2.5
    assert stats["supported_accused_end"] == 6.0
    assert any_match is True
    assert full_boundary is False


def test_alternative_ground_truth_check_requires_complete_mapping() -> None:
    truth = [{
        "accused_start": 0.0,
        "accused_end": 6.0,
        "source_episode": "EP01",
        "source_start": 10.0,
        "source_end": 16.0,
        "speed_ratio": 1.0,
    }]
    assert _alternatives_contain_ground_truth([_segment()], truth)
    assert not _alternatives_contain_ground_truth(
        [_segment(accused_start=100.0, accused_end=106.0)], truth
    )
    assert not _alternatives_contain_ground_truth(
        [_segment(source_end=13.0, speed_ratio=0.5)], truth
    )
    assert not _alternatives_contain_ground_truth(
        [_segment(speed_ratio=None)], truth
    )


def test_outcome_taxonomy_treats_ambiguous_primary_correct_as_safe_not_automatic() -> None:
    truth = [{
        "accused_start": 0.0, "accused_end": 6.0, "source_episode": "EP01",
        "source_start": 10.0, "source_end": 16.0, "speed_ratio": 1.0,
    }]
    correct_metrics = {
        "source_aware_recalled_segments": 1,
        "ground_truth_segments": 1,
        "predicted_segments": 1,
    }
    assert classify_case_outcome(
        MatchStatus.AMBIGUOUS.value, correct_metrics, truth, []
    ) == "SAFE_AMBIGUITY_CONTAINS_GT"
    assert classify_case_outcome(
        MatchStatus.MATCH_FOUND.value, correct_metrics, truth, []
    ) == "CORRECT_UNAMBIGUOUS_MATCH"
    assert classify_case_outcome(
        MatchStatus.MATCH_NOT_FOUND.value,
        {**correct_metrics, "source_aware_recalled_segments": 0},
        truth,
        [],
    ) == "UNSAFE_WRONG_MATCH"
    assert classify_case_outcome(
        MatchStatus.MATCH_NOT_FOUND.value,
        {"source_aware_recalled_segments": 0, "ground_truth_segments": 1, "predicted_segments": 0},
        truth,
        [],
    ) == "NO_MATCH"


def test_partial_boundary_safety_label_does_not_change_recall_taxonomy() -> None:
    truth = [{
        "accused_start": 0.0, "accused_end": 6.0, "source_episode": "EP01",
        "source_start": 10.0, "source_end": 16.0, "speed_ratio": 1.0,
    }]
    partial = _segment(
        accused_start=2.5,
        source_start=12.5,
        boundary_completeness="INCOMPLETE",
    )
    assert classify_boundary_assertion([partial], truth, False) == "CONTAINED_PARTIAL_EVIDENCE"
    metrics = {
        "source_aware_recalled_segments": 0,
        "ground_truth_segments": 1,
        "predicted_segments": 1,
    }
    assert classify_case_outcome(
        MatchStatus.MATCH_FOUND.value, metrics, truth, []
    ) == "UNSAFE_WRONG_MATCH"


def test_duplicate_extra_prediction_prevents_correct_unambiguous_outcome() -> None:
    metrics = {
        "source_aware_recalled_segments": 1,
        "ground_truth_segments": 1,
        "predicted_segments": 2,
        "false_positive_segments": 1,
    }
    assert classify_case_outcome(
        MatchStatus.MATCH_FOUND.value,
        metrics,
        [{"source_episode": "EP01"}],
        [],
    ) == "UNSAFE_WRONG_MATCH"
