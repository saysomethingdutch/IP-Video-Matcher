from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from experiments.phase2b4 import benchmark as b
from experiments.phase2b4 import metrics as m
from experiments.phase2b4 import reporting as r
from experiments.phase2b4 import synthetic as s
from experiments.phase2b4.trace import (
    _json_equivalent,
    classify_first_loss,
    sanitized_trace,
    trace_case,
)


def _audit(source_times: list[float], *, second: list[float] | None = None) -> list[dict]:
    rows = []
    for index, source_time in enumerate(source_times):
        accused = index * 0.5
        candidates = [("TRUE", source_time, 0.95)]
        if second is not None:
            candidates.append(("TRUE", second[index], 0.94))
        for rank, (episode, timestamp, score) in enumerate(candidates, 1):
            rows.append({
                "accused_time": accused, "source_id": episode, "source_time": timestamp,
                "rank": rank, "raw_cosine": score, "adapted_visual_score": score,
                "retained": True,
            })
    return rows


def _truth(duration: float) -> dict:
    return {
        "accused_start": 0.0, "accused_end": duration, "source_episode": "TRUE",
        "source_start": 100.0, "source_end": 100.0 + duration, "speed_ratio": 1.0,
    }


def _row(audit: list[dict], truth: dict) -> dict:
    from ipvm.config import MatchingConfig
    from ipvm.matching import TemporalAligner
    from experiments.phase2b4.trace import candidates_from_audit

    outcome = TemporalAligner(MatchingConfig()).align_with_diagnostics(
        candidates_from_audit(audit), index_complete=True
    )
    return {
        "case_id": "SYNTHETIC-TRACE", "backend": "SYNTHETIC", "positive": True,
        "truth": [truth], "candidate_audit": audit,
        "predictions": [item.to_dict() for item in outcome.segments],
        "alternatives": [item.to_dict() for item in outcome.alternatives],
        "status": "AMBIGUOUS" if outcome.ambiguous else (
            "MATCH_FOUND" if outcome.segments else "MATCH_NOT_FOUND"
        ),
    }


def test_metric_formulas_for_unique_rank_distance_and_margin() -> None:
    audit = _audit([100.0, 100.5, 101.0], second=[101.0, 101.5, 102.0])
    result = m.temporal_separability_metrics(audit, _truth(1.5), include_per_frame=True)
    assert result["frame_count"] == 3
    assert result["unique_source_frames_per_query"]["mean"] == 2.0
    assert result["gt_nearest_rank"]["median"] == 1.0
    assert result["gt_nearest_distance_seconds"]["max"] == 0.0
    assert result["gt_near_competition_margin"]["median"] == pytest.approx(0.01)


def test_duplicate_timestamp_concentration_and_entropy() -> None:
    base = s.build_cloud("distributed_duplicate_frames", 0)
    duplicate = s.build_cloud("distributed_duplicate_frames", 16)
    truth = s.synthetic_truth()
    base_metrics = m.temporal_separability_metrics(base, truth)
    duplicate_metrics = m.temporal_separability_metrics(duplicate, truth)
    assert duplicate_metrics["repetition_correct_source"]["maximum_multiplicity"] == 16
    assert duplicate_metrics["repetition_correct_source"]["repeat_surplus"] > base_metrics["repetition_correct_source"]["repeat_surplus"]
    assert duplicate_metrics["correct_source_local_entropy"]["mean_effective_diversity"] < base_metrics["correct_source_local_entropy"]["mean_effective_diversity"] * 2


def test_monotonicity_and_pairwise_inversions_are_distinct() -> None:
    audit = _audit([100.0, 101.0, 100.5, 100.5])
    result = m.temporal_separability_metrics(audit, _truth(2.0))
    assert result["top1_monotonicity"]["violation_count"] == 1
    assert result["top1_monotonicity"]["plateau_count"] == 1
    assert result["gt_nearest_order"]["inversion_count"] == 2
    assert result["gt_nearest_order"]["tie_count"] == 1
    assert result["gt_nearest_order"]["pair_count"] == 6


def test_oracle_band_recovers_registered_affine_line() -> None:
    audit = _audit([100.0 + index * 0.5 for index in range(16)])
    oracle = m.oracle_band_diagnostics(audit, _truth(8.0))
    assert oracle["frame_coverage"] == 1.0
    assert oracle["nearest_per_frame_fit"]["slope"] == pytest.approx(1.0)
    assert oracle["nearest_per_frame_fit"]["rms_residual_seconds"] < 1e-10
    assert oracle["nearest_per_frame_fit"]["evaluation"]["complete"] is True


def test_actual_vs_oracle_reports_physical_failure() -> None:
    oracle = m.oracle_band_diagnostics(
        _audit([100.0 + index * 0.5 for index in range(16)]), _truth(8.0)
    )["nearest_per_frame_fit"]
    value = {
        "accused_start": 0.0, "accused_end": 8.0, "source_episode": "TRUE",
        "source_start": 100.0, "source_end": 106.0, "speed_ratio": 0.75,
        "visual_score": 0.95, "audio_score": None, "temporal_score": 1.0,
        "match_score": 0.95, "inlier_count": 16,
    }
    result = m.actual_fit_diagnostics(value, _truth(8.0), oracle)
    assert result["slope"] == pytest.approx(0.75)
    assert result["evaluation"]["duration_slope_consistent"] is False
    assert result["evaluation"]["complete"] is False


def test_trace_is_deterministic_and_delegates_frozen_alignment() -> None:
    audit = s.build_cloud("distractor_density", 0)
    row = _row(audit, s.synthetic_truth())
    first = trace_case(row)
    second = trace_case(row)
    oracle = m.oracle_band_diagnostics(audit, s.synthetic_truth())
    assert sanitized_trace(first, oracle) == sanitized_trace(second, oracle)
    assert first["replay_predictions"] == second["replay_predictions"]
    assert classify_first_loss(first, oracle) == "NONE"


def test_replay_equality_uses_exact_json_contract() -> None:
    assert _json_equivalent({"values": (1, 2)}, {"values": [1, 2]})
    assert not _json_equivalent({"values": (1, 2)}, {"values": [1, 3]})


@pytest.mark.parametrize("axis,values", list(s.AXIS_GRID.items()))
def test_synthetic_cloud_grid_is_fixed_top8_and_source_grid(axis: str, values: tuple) -> None:
    for value in values:
        audit = s.build_cloud(axis, value)
        frames = m.grouped_frames(audit)
        assert len(frames) == 16
        assert all(len(frame) == 8 for frame in frames)
        assert all(abs(item["source_time"] * 2 - round(item["source_time"] * 2)) < 1e-9 for item in audit)


def test_distributed_and_local_plateaus_are_different_fixtures() -> None:
    distributed = s.build_cloud("distributed_duplicate_frames", 8)
    local = s.build_cloud("local_plateau_frames", 8)
    distributed_frames = {item["accused_time"] for item in distributed if item["kind"] == "plateau"}
    local_frames = {item["accused_time"] for item in local if item["kind"] == "plateau"}
    assert distributed_frames != local_frames
    assert len(distributed_frames) == len(local_frames) == 8


def test_production_diff_and_fresh_absence_guard() -> None:
    b.verify_stage(b.ROOT / "workspace/phase2b3/dev.json")


def test_fresh_artifact_guard_fails_loudly(tmp_path: Path) -> None:
    dev = tmp_path / "dev.json"
    dev.write_text("{}", encoding="utf-8")
    (tmp_path / "fresh-held-out.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="fresh/selected"):
        b.verify_stage(dev)


def test_sanitizer_rejects_exact_timelines_candidates_and_private_material() -> None:
    for payload in (
        {"source_time": 1.0}, {"per_frame": []}, {"candidate_audit": []},
        {"note": "/Users/person/private.mp4"}, {"sha256": "abc"},
        {"value": "a" * 64},
    ):
        with pytest.raises(ValueError):
            r.validate_sanitized(payload)
    r.validate_sanitized({"normalized_source_start": 0.5, "slope": 1.0})


def test_protocol_commits_precede_results_and_are_protocol_only() -> None:
    assert b._git("diff-tree", "--no-commit-id", "--name-only", "-r", b.INITIAL_PROTOCOL_COMMIT) == "experiments/phase2b4/PROTOCOL.md"
    assert b._git("diff-tree", "--no-commit-id", "--name-only", "-r", b.PROTOCOL_COMMIT) == "experiments/phase2b4/PROTOCOL.md"
    assert b._git("ls-tree", "-r", "--name-only", b.PROTOCOL_COMMIT, "workspace/phase2b4") == ""
    assert b._git("ls-tree", "-r", "--name-only", b.PROTOCOL_COMMIT, "benchmark/visual/results/phase2b4-temporal-separability-v0.1.json") == ""
