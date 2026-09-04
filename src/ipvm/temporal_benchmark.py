from __future__ import annotations

import json
import random
import tempfile
import time
from collections import defaultdict
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from .benchmarking import (
    _encoder_available,
    _ffmpeg,
    _make_clip,
    _make_platform_transcode_clip,
    evaluate_segments,
    resource_snapshot,
)
from .config import AppConfig, MatchingConfig
from .database import Database
from .matching import TemporalAligner, VideoMatcher
from .media import extract_grayscale_frames
from .models import CandidateMatch, MatchStatus, SegmentMatch
from .retrieval_benchmark import (
    PHASE_BUCKET_COUNT,
    TRANSCODE_PROFILES,
    _balanced_phase_assignments,
    _sample_off_grid_start,
)


IDEAL_SPEEDS = (0.75, 0.80, 0.90, 1.00, 1.10, 1.25, 1.40, 1.50)
SPEED_RATIO_TOLERANCE = 0.15
REAL_PROFILES = ("direct", "p1", "p2", "p3")


def _snap_to_source_grid(value: float, grid_period: float) -> float:
    """A real candidate's source_time is always an index frame timestamp."""
    return round(round(value / grid_period) * grid_period, 6)


def _line(
    episode: str,
    accused_start: float,
    source_start: float,
    speed: float,
    count: int,
    score: float = 0.98,
    jitter: tuple[float, ...] = (0.0,),
    grid_period: float = 0.5,
) -> list[CandidateMatch]:
    """Ideal candidates, snapped to the configured source grid.

    Phase 2A.1 item 9: retrieval can only ever return index frame timestamps, so a
    sub-grid source time is not a physically realizable candidate and must not be
    used to claim sub-grid boundary precision.
    """
    return [
        CandidateMatch(
            round(accused_start + index * grid_period, 6),
            episode,
            _snap_to_source_grid(
                source_start + speed * index * grid_period, grid_period
            )
            + jitter[index % len(jitter)],
            score,
        )
        for index in range(count)
    ]


def _truth(
    episode: str,
    accused_start: float,
    source_start: float,
    speed: float,
    duration: float,
) -> dict[str, Any]:
    return {
        "accused_start": accused_start,
        "accused_end": accused_start + duration,
        "source_episode": episode,
        "source_start": source_start,
        "source_end": source_start + speed * duration,
        "speed_ratio": speed,
    }


def _supported_run_statistics(
    supported_frames: set[int], frame_count: int, gap_seconds: float, fps: float
) -> dict[str, Any]:
    """Runs of ACTUAL supported frames, tolerating configured holes.

    A tolerated hole keeps a run alive but is never counted as a supporting frame:
    `longest_run_actual_hit_count` counts real hits only, while
    `longest_run_temporal_span_seconds` reports the wall time the run covers.
    """
    allowed_missing = max(0, int(round(gap_seconds * fps)) - 1)
    best = {
        "longest_run_actual_hit_count": 0,
        "longest_run_temporal_span_seconds": 0.0,
        "tolerated_hole_count": 0,
        "supported_accused_start": None,
        "supported_accused_end": None,
    }
    index = 0
    while index < frame_count:
        if index not in supported_frames:
            index += 1
            continue
        hits = 0
        holes = 0
        start = index
        last_hit = index
        missing = 0
        cursor = index
        while cursor < frame_count:
            if cursor in supported_frames:
                hits += 1
                holes += missing
                missing = 0
                last_hit = cursor
            else:
                missing += 1
                if missing > allowed_missing:
                    break
            cursor += 1
        span = (last_hit - start + 1) / fps
        candidate = {
                "longest_run_actual_hit_count": hits,
                "longest_run_temporal_span_seconds": round(span, 6),
                "tolerated_hole_count": holes,
                "supported_accused_start": round(start / fps, 6),
                "supported_accused_end": round((last_hit + 1) / fps, 6),
        }
        candidate_key = (hits, span, -holes)
        best_key = (
            best["longest_run_actual_hit_count"],
            best["longest_run_temporal_span_seconds"],
            -best["tolerated_hole_count"],
        )
        if candidate_key > best_key:
            best = candidate
        index = last_hit + 1
    return best


def _retrieval_sufficiency(
    stats: dict[str, Any], frame_count: int, minimum_support: int, fps: float
) -> tuple[bool, bool]:
    """Separate support for some match from support for both query boundaries."""
    sufficient_for_any = stats["longest_run_actual_hit_count"] >= minimum_support
    sufficient_for_full = bool(
        sufficient_for_any
        and stats["supported_accused_start"] == 0.0
        and stats["supported_accused_end"] is not None
        and stats["supported_accused_end"] >= frame_count / fps
    )
    return sufficient_for_any, sufficient_for_full


def _grid_invariant(cases: list[dict[str, Any]], grid_period: float) -> dict[str, Any]:
    """Machine invariant: every candidate claiming realism sits on the source grid."""
    total = off = 0
    offenders: list[str] = []
    for case in cases:
        case_off = 0
        for timestamp in case.get("candidate_source_times", []):
            total += 1
            scaled = timestamp / grid_period
            if abs(scaled - round(scaled)) > 1e-6:
                case_off += 1
        if case_off:
            offenders.append(case["case_id"])
        off += case_off
    return {
        "candidate_count": total,
        "off_grid_candidate_count": off,
        "all_candidates_on_grid": off == 0,
        "off_grid_cases": offenders,
    }


CASE_OUTCOMES = (
    "CORRECT_UNAMBIGUOUS_MATCH",
    "SAFE_AMBIGUITY_CONTAINS_GT",
    "UNSAFE_WRONG_MATCH",
    "NO_MATCH",
)


def _mapping_matches_truth(
    candidate: Any, expected: dict[str, Any], tolerance: float = 1.0
) -> bool:
    """Full mapping check, not just source endpoints.

    A candidate only counts as containing ground truth if the episode, the accused
    interval, the source mapping, the duration and the speed all agree. Matching source
    endpoints alone would let an alternative with a completely wrong accused timeline
    be scored SAFE.
    """
    if candidate.source_episode != expected["source_episode"]:
        return False
    if abs(candidate.source_start - expected["source_start"]) > tolerance:
        return False
    if abs(candidate.source_end - expected["source_end"]) > tolerance:
        return False
    if abs(candidate.accused_start - expected["accused_start"]) > tolerance:
        return False
    if abs(candidate.accused_end - expected["accused_end"]) > tolerance:
        return False
    expected_duration = expected["accused_end"] - expected["accused_start"]
    candidate_duration = candidate.accused_end - candidate.accused_start
    if abs(candidate_duration - expected_duration) > tolerance:
        return False
    expected_speed = expected.get("speed_ratio")
    if expected_speed is not None:
        if candidate_duration <= 1e-9:
            return False
        mapped_speed = (candidate.source_end - candidate.source_start) / candidate_duration
        if abs(mapped_speed - expected_speed) > SPEED_RATIO_TOLERANCE:
            return False
        if candidate.speed_ratio is None:
            return False
        if abs(candidate.speed_ratio - expected_speed) > SPEED_RATIO_TOLERANCE:
            return False
    return True


def _alternatives_contain_ground_truth(
    alternatives: list[Any], truth: list[dict[str, Any]], tolerance: float = 1.0
) -> bool:
    return any(
        _mapping_matches_truth(candidate, expected, tolerance)
        for expected in truth
        for candidate in alternatives
    )


def classify_case_outcome(
    status: str,
    metrics: dict[str, Any],
    truth: list[dict[str, Any]],
    alternatives: list[Any],
) -> str:
    """Mutually exclusive, machine-derived outcome for one benchmark case.

    A correct episode with a materially wrong source timeline is still an unsafe
    wrong match: `UNSAFE_WRONG_MATCH` covers any emitted MATCH_FOUND that fails the
    accepted ground-truth episode/timeline requirement.
    """
    recalled = metrics.get("source_aware_recalled_segments", 0)
    expected_count = metrics.get("ground_truth_segments", len(truth))
    fully_recalled = bool(expected_count) and recalled == expected_count
    no_false_positives = int(metrics.get("false_positive_segments", 0)) == 0
    fully_correct = fully_recalled and no_false_positives
    prediction_count = int(metrics.get("predicted_segments", 0))
    if status == MatchStatus.AMBIGUOUS.value:
        # An ambiguous result is never an automatic success, even when the primary
        # happens to be right: a human still has to resolve it.
        if fully_correct or _alternatives_contain_ground_truth(alternatives, truth):
            return "SAFE_AMBIGUITY_CONTAINS_GT"
        if prediction_count:
            return "UNSAFE_WRONG_MATCH"
        return "NO_MATCH"
    if status == MatchStatus.MATCH_FOUND.value:
        return "CORRECT_UNAMBIGUOUS_MATCH" if fully_correct else "UNSAFE_WRONG_MATCH"
    return "UNSAFE_WRONG_MATCH" if prediction_count else "NO_MATCH"


def classify_boundary_assertion(
    predictions: list[Any], truth: list[dict[str, Any]], fully_recalled: bool
) -> str:
    """Orthogonal safety label; it never changes recall or the four-way outcome.

    A contained partial segment marked INCOMPLETE is useful evidence, but it is not a
    recovered GT segment. Keeping this label separate prevents the safety improvement
    from silently inflating source-aware recall.
    """
    if not predictions:
        return "NO_OUTPUT"
    if fully_recalled:
        return "FULL_MAPPING_CORRECT"

    def contained_partial(candidate: Any, expected: dict[str, Any]) -> bool:
        if getattr(candidate, "boundary_completeness", "UNKNOWN") != "INCOMPLETE":
            return False
        if candidate.source_episode != expected["source_episode"]:
            return False
        tolerance = 1.0
        if candidate.accused_start < expected["accused_start"] - tolerance:
            return False
        if candidate.accused_end > expected["accused_end"] + tolerance:
            return False
        expected_speed = expected.get("speed_ratio")
        if expected_speed is None:
            return False
        expected_source_start = expected["source_start"] + expected_speed * (
            candidate.accused_start - expected["accused_start"]
        )
        expected_source_end = expected["source_start"] + expected_speed * (
            candidate.accused_end - expected["accused_start"]
        )
        return (
            abs(candidate.source_start - expected_source_start) <= tolerance
            and abs(candidate.source_end - expected_source_end) <= tolerance
        )

    if all(
        any(contained_partial(candidate, expected) for expected in truth)
        for candidate in predictions
    ):
        return "CONTAINED_PARTIAL_EVIDENCE"
    if any(
        getattr(candidate, "boundary_completeness", "UNKNOWN") == "FULL"
        for candidate in predictions
    ):
        return "INCORRECT_COMPLETE_ASSERTION"
    return "UNRESOLVED_OR_INCORRECT_MAPPING"


def _outcome_summary(cases: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {name: 0 for name in CASE_OUTCOMES}
    for case in cases:
        outcome = case.get("outcome")
        if outcome in counts:
            counts[outcome] += 1
    total = len(cases)
    assert sum(counts.values()) == total, "outcome counts must sum to case_count"
    rate = lambda name: counts[name] / total if total else None  # noqa: E731
    safe_coverage = (
        (counts["CORRECT_UNAMBIGUOUS_MATCH"] + counts["SAFE_AMBIGUITY_CONTAINS_GT"]) / total
        if total else None
    )
    return {
        "case_count": total,
        "counts": counts,
        "correct_unambiguous_match_rate": rate("CORRECT_UNAMBIGUOUS_MATCH"),
        "safe_ambiguity_rate": rate("SAFE_AMBIGUITY_CONTAINS_GT"),
        "unsafe_wrong_rate": rate("UNSAFE_WRONG_MATCH"),
        "no_match_rate": rate("NO_MATCH"),
        "safe_coverage": safe_coverage,
        "unsafe_wrong_cases": [
            case["case_id"] for case in cases if case.get("outcome") == "UNSAFE_WRONG_MATCH"
        ],
    }


def _metric_summary(cases: list[dict[str, Any]]) -> dict[str, Any]:
    metrics = [item["metrics"] for item in cases]
    truth_count = sum(int(item["ground_truth_segments"]) for item in metrics)
    prediction_count = sum(int(item["predicted_segments"]) for item in metrics)
    source_recalled = sum(int(item["source_aware_recalled_segments"]) for item in metrics)
    episode_recalled = sum(int(item["episode_recalled_segments"]) for item in metrics)
    false_positive_segments = sum(int(item["false_positive_segments"]) for item in metrics)
    accused_errors = [
        float(value) for item in metrics for value in item.get("raw_accused_boundary_errors", [])
    ]
    source_errors = [
        float(value) for item in metrics for value in item.get("raw_source_boundary_errors", [])
    ]
    speed_errors = [
        float(value) for item in metrics for value in item.get("raw_speed_ratio_errors", [])
    ]
    predictions = [
        prediction for item in cases for prediction in item.get("predictions", [])
    ]
    boundary_counts: dict[str, int] = defaultdict(int)
    for prediction in predictions:
        boundary_counts[str(prediction.get("boundary_completeness", "UNKNOWN"))] += 1
    boundary_assertion_counts: dict[str, int] = defaultdict(int)
    for item in cases:
        boundary_assertion_counts[str(item.get("boundary_assertion_outcome", "UNAVAILABLE"))] += 1
    status_counts: dict[str, int] = defaultdict(int)
    for item in cases:
        status_counts[str(item.get("status", "UNKNOWN"))] += 1
    retrieval_details = [
        item["retrieval_attribution_detail"]
        for item in cases
        if item.get("retrieval_attribution_detail")
    ]
    green_count = sum(int(item.get("candidate_green_predictions", 0)) for item in metrics)
    correct_green = sum(
        int(item.get("correct_candidate_green_predictions", 0)) for item in metrics
    )
    overlapping = sum(int(item.get("overlapping_predictions", 0)) for item in metrics)
    correct_episode = sum(int(item.get("correct_episode_predictions", 0)) for item in metrics)
    segment_count_errors = [
        abs(int(item["predicted_segments"]) - int(item["ground_truth_segments"]))
        for item in metrics
    ]
    return {
        "case_count": len(cases),
        "ground_truth_segments": truth_count,
        "predicted_segments": prediction_count,
        "true_positive_segments": source_recalled,
        "false_positive_segments": false_positive_segments,
        "false_negative_segments": truth_count - source_recalled,
        "source_aware_segment_recall_at_1s": (
            source_recalled / truth_count if truth_count else None
        ),
        "segment_precision_at_1s": (
            (prediction_count - false_positive_segments) / prediction_count
            if prediction_count else (1.0 if not truth_count else 0.0)
        ),
        "episode_recall": episode_recalled / truth_count if truth_count else None,
        "episode_correctness": correct_episode / overlapping if overlapping else None,
        "source_boundary_mae_seconds": (
            sum(source_errors) / len(source_errors) if source_errors else None
        ),
        "accused_boundary_mae_seconds": (
            sum(accused_errors) / len(accused_errors) if accused_errors else None
        ),
        "false_positive_segment_count": false_positive_segments,
        "false_positive_seconds": sum(float(item["false_positive_seconds"]) for item in metrics),
        "ambiguity_rate": (
            sum(item["status"] == MatchStatus.AMBIGUOUS.value for item in cases) / len(cases)
            if cases else None
        ),
        "mean_absolute_segment_count_error": (
            sum(segment_count_errors) / len(segment_count_errors)
            if segment_count_errors else None
        ),
        "speed_ratio_mae": sum(speed_errors) / len(speed_errors) if speed_errors else None,
        "mean_residual_rms_seconds": (
            sum(float(item["residual_rms_seconds"]) for item in predictions) / len(predictions)
            if predictions else None
        ),
        "mean_inlier_ratio": (
            sum(float(item["inlier_ratio"]) for item in predictions) / len(predictions)
            if predictions else None
        ),
        "candidate_green_precision_diagnostic": (
            correct_green / green_count if green_count else None
        ),
        "candidate_green_count": green_count,
        "boundary_completeness_distribution": dict(sorted(boundary_counts.items())),
        "boundary_assertion_outcome_distribution": dict(
            sorted(boundary_assertion_counts.items())
        ),
        "status_distribution": dict(sorted(status_counts.items())),
        "retrieval_sufficiency": {
            "evaluated_case_count": len(retrieval_details),
            "sufficient_for_any_match_count": sum(
                bool(item["retrieval_sufficient_for_any_match"])
                for item in retrieval_details
            ),
            "sufficient_for_full_boundary_count": sum(
                bool(item["retrieval_sufficient_for_full_boundary"])
                for item in retrieval_details
            ),
        },
        "runtime_seconds": sum(float(item["runtime_seconds"]) for item in cases),
        "failure_attribution": {
            "RETRIEVAL_INSUFFICIENT_FOR_ANY_MATCH": sum(
                item.get("failure_attribution")
                == "RETRIEVAL_INSUFFICIENT_FOR_ANY_MATCH"
                for item in cases
            ),
            "RETRIEVAL_INSUFFICIENT_FOR_FULL_BOUNDARY": sum(
                item.get("failure_attribution")
                == "RETRIEVAL_INSUFFICIENT_FOR_FULL_BOUNDARY"
                for item in cases
            ),
            "ALIGNMENT_LIMIT": sum(
                item.get("failure_attribution") == "ALIGNMENT_LIMIT" for item in cases
            ),
        },
    }


def _group_cases(cases: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in cases:
        grouped[str(case[key])].append(case)
    return {name: _metric_summary(items) for name, items in sorted(grouped.items())}


def _position_level_summary(cases: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in cases:
        grouped[str(case["interval_id"])].append(case)
    matrix: list[dict[str, Any]] = []
    for interval_id, items in sorted(grouped.items()):
        outcomes = {str(item["profile"]): str(item["outcome"]) for item in items}
        assertions = {
            str(item["profile"]): str(item["boundary_assertion_outcome"])
            for item in items
        }
        matrix.append({
            "interval_id": interval_id,
            "episode_alias": items[0]["episode_alias"],
            "normalized_source_start": items[0]["normalized_source_start"],
            "profile_outcomes": outcomes,
            "boundary_assertion_outcomes": assertions,
            "all_profiles_full_mapping_correct": all(
                value == "CORRECT_UNAMBIGUOUS_MATCH" for value in outcomes.values()
            ),
            "contains_unsafe_wrong_match": any(
                value == "UNSAFE_WRONG_MATCH" for value in outcomes.values()
            ),
            "contains_contained_partial_evidence": any(
                value == "CONTAINED_PARTIAL_EVIDENCE" for value in assertions.values()
            ),
        })
    return {
        "independent_content_position_count": len(matrix),
        "all_profiles_full_mapping_correct_count": sum(
            bool(item["all_profiles_full_mapping_correct"]) for item in matrix
        ),
        "positions_without_unsafe_wrong_match_count": sum(
            not bool(item["contains_unsafe_wrong_match"]) for item in matrix
        ),
        "positions_with_contained_partial_evidence_count": sum(
            bool(item["contains_contained_partial_evidence"]) for item in matrix
        ),
        "matrix": matrix,
    }


class IdealTemporalBenchmark:
    """Deterministic candidate-level benchmark, independent of visual retrieval."""

    def __init__(self, config: MatchingConfig | None = None):
        self.config = config or MatchingConfig()
        self.aligner = TemporalAligner(self.config)

    def _run_case(
        self,
        case_id: str,
        transformation: str,
        truth: list[dict[str, Any]],
        candidates: list[CandidateMatch],
    ) -> dict[str, Any]:
        started = time.perf_counter()
        outcome = self.aligner.align_with_diagnostics(candidates, index_complete=True)
        elapsed = time.perf_counter() - started
        status = (
            MatchStatus.AMBIGUOUS.value
            if outcome.ambiguous or outcome.contradiction
            else MatchStatus.MATCH_FOUND.value if outcome.segments
            else MatchStatus.MATCH_NOT_FOUND.value
        )
        metrics = evaluate_segments(
            truth, outcome.segments, status, outcome.alternatives
        )
        fully_recalled = bool(truth) and (
            metrics["source_aware_recalled_segments"] == metrics["ground_truth_segments"]
            and metrics["false_positive_segments"] == 0
        )
        return {
            "case_id": case_id,
            "transformation": transformation,
            "status": status,
            "outcome": classify_case_outcome(
                status, metrics, truth, outcome.alternatives
            ),
            "ambiguity_contains_ground_truth": _alternatives_contain_ground_truth(
                outcome.alternatives, truth
            ),
            "boundary_assertion_outcome": classify_boundary_assertion(
                outcome.segments, truth, fully_recalled
            ),
            "truncated_episodes": list(outcome.truncated_episodes),
            "candidate_source_times": [
                round(candidate.source_time, 6) for candidate in candidates
            ],
            "alternatives": [segment.to_dict() for segment in outcome.alternatives],
            "metrics": metrics,
            "runtime_seconds": elapsed,
            "predictions": [item.to_dict() for item in outcome.segments],
            "alternative_candidates": [item.to_dict() for item in outcome.alternatives],
        }

    def run(self) -> dict[str, Any]:
        cases: list[dict[str, Any]] = []
        for speed in IDEAL_SPEEDS:
            accused_start, source_start, duration = 2.0, 40.0, 10.0
            candidates = _line(
                "EP07", accused_start, source_start, speed, 20, score=0.97
            )
            cases.append(self._run_case(
                f"IDEAL_SPEED_{speed:.2f}", "ideal_speed_sweep",
                [_truth("EP07", accused_start, source_start, speed, duration)],
                candidates,
            ))

        jitter_candidates: list[CandidateMatch] = []
        jitter = (-0.5, 0.0, 0.5, 0.0)
        for index in range(24):
            accused = round(index * 0.5, 6)
            # +/-1 grid-step jitter, still ON the 2 FPS source grid: this is the form real
            # retrieval can actually return.
            jitter_candidates.append(CandidateMatch(
                accused, "EP01",
                _snap_to_source_grid(25.0 + 1.25 * accused, 0.5) + jitter[index % 4],
                0.96,
            ))
            jitter_candidates.append(CandidateMatch(
                accused, "EP01", 80.0 + ((index * 7) % 19) * 0.5, 1.0
            ))
            jitter_candidates.append(CandidateMatch(
                accused, "EP99", 140.0 + ((index * 11) % 23) * 0.5, 0.999
            ))
        cases.append(self._run_case(
            "IDEAL_GRID_JITTER", "grid_jitter_outlier_wrong_episode",
            [_truth("EP01", 0.0, 25.0, 1.25, 12.0)], jitter_candidates,
        ))

        specifications = [
            ("EP01", 0.0, 10.0, 1.0),
            ("EP02", 8.0, 30.0, 1.25),
            ("EP01", 14.0, 50.0, 0.8),
            ("EP03", 22.0, 70.0, 1.1),
            ("EP01", 30.0, 10.0, 1.0),
        ]
        piecewise_candidates: list[CandidateMatch] = []
        piecewise_truth: list[dict[str, Any]] = []
        for episode, accused_start, source_start, speed in specifications:
            piecewise_candidates.extend(_line(
                episode, accused_start, source_start, speed, 12, score=0.98
            ))
            for index in range(12):
                accused = accused_start + index * 0.5
                piecewise_candidates.append(CandidateMatch(
                    accused, episode,
                    150.0 + ((index * 7 + int(accused_start)) % 29) * 0.5,
                    0.995,
                ))
            piecewise_truth.append(_truth(
                episode, accused_start, source_start, speed, 6.0
            ))
        cases.append(self._run_case(
            "IDEAL_PIECEWISE_5", "piecewise_five_segments",
            piecewise_truth, piecewise_candidates,
        ))
        # --- Phase 2A.1 item 9: additional required ideal coverage ---
        cases.append(self._run_case(
            "IDEAL_NEGATIVE_OFFSET", "negative_and_zero_offset",
            [_truth("EP01", 12.0, 0.0, 1.0, 8.0)],
            _line("EP01", 12.0, 0.0, 1.0, 16, score=0.97),
        ))

        noise_random = random.Random(20260903)
        noise_candidates = [
            CandidateMatch(
                round(index * 0.5, 6),
                noise_random.choice(("EP01", "EP02", "EP03")),
                _snap_to_source_grid(noise_random.uniform(0.0, 2400.0), 0.5),
                round(noise_random.uniform(0.83, 0.99), 4),
            )
            for index in range(160)
            for _ in range(8)
        ]
        cases.append(self._run_case(
            "IDEAL_PURE_NOISE", "pure_noise_negative", [], noise_candidates,
        ))

        competing = (
            _line("EP01", 0.0, 300.0, 1.0, 20, score=0.90)
            + _line("EP02", 0.0, 900.0, 1.0, 20, score=0.899)
        )
        cases.append(self._run_case(
            "IDEAL_COMPETING_LINES", "competing_coherent_lines",
            [_truth("EP01", 0.0, 300.0, 1.0, 10.0)], competing,
        ))

        # accused span clears minimum_segment_seconds but the SOURCE span is under 3s,
        # so no measured speed may be claimed.
        cases.append(self._run_case(
            "IDEAL_SHORT_SOURCE_SPAN", "short_span_null_speed",
            [_truth("EP01", 0.0, 600.0, 0.75, 3.5)],
            _line("EP01", 0.0, 600.0, 0.75, 8, score=0.98),
        ))

        discontinuity = (
            _line("EP01", 0.0, 100.0, 0.8, 24, score=0.99)
            + _line("EP01", 12.0, 109.5, 1.25, 24, score=0.99)
        )
        discontinuity_truth = [
            _truth("EP01", 0.0, 100.0, 0.8, 12.0),
            _truth("EP01", 12.0, 109.5, 1.25, 12.0),
        ]
        discontinuity_case = self._run_case(
            "IDEAL_SPEED_DISCONTINUITY", "speed_discontinuity_merge_negative",
            discontinuity_truth, discontinuity,
        )
        # Source-aware: segment count, both slopes recovered, and no segment whose slope
        # sits between the two regimes (a fictitious mixed segment).
        predictions = discontinuity_case["predictions"]
        slopes = [p["speed_ratio"] for p in predictions if p["speed_ratio"] is not None]
        discontinuity_case["change_point_source_aware"] = bool(
            discontinuity_case["metrics"]["source_aware_recalled_segments"]
            == len(discontinuity_truth)
            and discontinuity_case["metrics"]["predicted_segments"]
            == len(discontinuity_truth)
        )
        discontinuity_case["change_point_detail"] = {
            "ground_truth": discontinuity_truth,
            "predicted_slopes": slopes,
            "predicted_boundaries": [
                (p["accused_start"], p["accused_end"], p["source_start"], p["source_end"])
                for p in predictions
            ],
        }
        cases.append(discontinuity_case)

        # Out-of-range speeds have no recoverable ground-truth speed by definition, so
        # these are behavioural cases: the requirement is that no clamped endpoint is
        # ever reported as a measurement. They carry no recall ground truth.
        for out_of_range in (0.65, 1.70):
            case = self._run_case(
                f"IDEAL_SPEED_OUT_OF_RANGE_{out_of_range:.2f}", "speed_out_of_range",
                [], _line("EP01", 0.0, 800.0, out_of_range, 40, score=0.99),
            )
            predictions = case["predictions"]
            case["no_clamped_speed_reported"] = all(
                prediction["speed_ratio"] not in (
                    self.config.minimum_speed_ratio, self.config.maximum_speed_ratio
                )
                for prediction in predictions
            )
            # Full out-of-range semantics, not just the absence of a clamped endpoint.
            case["out_of_range_semantics_complete"] = bool(predictions) and all(
                prediction["speed_ratio"] is None
                and prediction["speed_out_of_range"] is True
                and prediction["unconstrained_speed_ratio"] is not None
                and prediction["candidate_tier"] != "GREEN"
                and prediction["review_tier"] == "REVIEW_REQUIRED"
                for prediction in predictions
            )
            cases.append(case)

        # Phase 2A.1 item 9: the added coverage includes deliberately adversarial and
        # negative cases. Gating recall/precision is computed over the recoverable
        # cases only; the adversarial cases are reported as behavioural diagnostics so
        # they are neither hidden nor allowed to distort a pass/fail aggregate.
        diagnostic_transformations = {
            "pure_noise_negative",
            "speed_out_of_range",
            "speed_discontinuity_merge_negative",
            "grid_jitter_outlier_wrong_episode",
        }
        gating_cases = [
            case for case in cases
            if case["transformation"] not in diagnostic_transformations
        ]
        diagnostic_cases = [
            case for case in cases
            if case["transformation"] in diagnostic_transformations
        ]
        aggregate = _metric_summary(gating_cases)
        return {
            "schema_version": "temporal-alignment-characterization-v0.2",
            "created_at": datetime.now(UTC).isoformat(),
            "scope": "ideal_candidate_temporal_alignment",
            "visual_retrieval_involved": False,
            "configuration": asdict(self.config),
            "speed_sweep": list(IDEAL_SPEEDS),
            "source_grid_realistic": _grid_invariant(cases, 0.5)["all_candidates_on_grid"],
            "outcome_taxonomy": _outcome_summary(cases),
            "aggregate": aggregate,
            "gating_case_ids": [case["case_id"] for case in gating_cases],
            "diagnostic_aggregate": _metric_summary(diagnostic_cases),
            "diagnostic_case_ids": [case["case_id"] for case in diagnostic_cases],
            "source_grid_invariant": _grid_invariant(cases, 0.5),
            "behavioural_assertions": {
                "pure_noise_produces_no_segments": all(
                    case["metrics"]["predicted_segments"] == 0
                    for case in cases
                    if case["transformation"] == "pure_noise_negative"
                ),
                "no_clamped_speed_reported": all(
                    case.get("no_clamped_speed_reported", True) for case in cases
                ),
                "out_of_range_semantics_complete": all(
                    case.get("out_of_range_semantics_complete", True) for case in cases
                ),
                "speed_discontinuity_source_aware": all(
                    case.get("change_point_source_aware", True) for case in cases
                ),
                "competing_lines_are_ambiguous": all(
                    case["status"] == MatchStatus.AMBIGUOUS.value
                    for case in cases
                    if case["transformation"] == "competing_coherent_lines"
                ),
                "short_source_span_speed_is_null": all(
                    prediction["speed_ratio"] is None
                    for case in cases
                    if case["transformation"] == "short_span_null_speed"
                    for prediction in case["predictions"]
                ),
            },
            "by_transformation": _group_cases(cases, "transformation"),
            "passed": (
                aggregate["source_aware_segment_recall_at_1s"] == 1.0
                and aggregate["segment_precision_at_1s"] == 1.0
            ),
            "cases": cases,
        }


def _make_lossless_direct_clip(
    source: Path, target: Path, start: float, duration: float
) -> None:
    _ffmpeg([
        "-ss", f"{start:.6f}", "-i", str(source),
        "-vf", f"trim=duration={duration:.6f},setpts=PTS-STARTPTS",
        "-an", "-c:v", "ffv1", str(target),
    ])


class RealSegmentBenchmark:
    """Real-media segment benchmark; reports aliases and normalized positions only."""

    def __init__(self, config: AppConfig, database: Database):
        if config.matching.coarse_fps != 2.0 or config.matching.top_k != 8:
            raise ValueError("Phase 2A real segment baseline requires 2 FPS and top-k=8")
        self.config = config
        self.database = database

    def _retrieval_attribution(
        self,
        matcher: VideoMatcher,
        query_path: Path,
        episode: str,
        source_start: float,
        speed: float,
    ) -> tuple[str | None, dict[str, Any]]:
        frames = extract_grayscale_frames(
            query_path,
            fps=self.config.matching.coarse_fps,
            width=self.config.matching.frame_width,
            height=self.config.matching.frame_height,
        )
        features = matcher.encoder.encode_images(frame for _, frame in frames)
        candidates = matcher.retriever.retrieve(
            np.asarray([item[0] for item in frames], dtype=np.float32),
            features,
        )
        hits: set[int] = set()
        tolerance = 1.0 / self.config.matching.coarse_fps
        for candidate in candidates:
            expected = source_start + speed * candidate.accused_time
            if (
                candidate.source_episode == episode
                and abs(candidate.source_time - expected) <= tolerance + 1e-6
            ):
                hits.add(round(candidate.accused_time * self.config.matching.coarse_fps))
        minimum = int(
            self.config.matching.minimum_segment_seconds * self.config.matching.coarse_fps
        )
        # Phase 2A.2 item 7: split attribution. A tolerated hole keeps a run alive but
        # is never counted as a supporting frame.
        stats = _supported_run_statistics(
            hits,
            len(frames),
            self.config.matching.maximum_frame_gap_seconds,
            self.config.matching.coarse_fps,
        )
        sufficient_for_any, sufficient_for_full = _retrieval_sufficiency(
            stats,
            len(frames),
            minimum,
            self.config.matching.coarse_fps,
        )
        # Full-boundary sufficiency is a BENCHMARK-only judgement: it may use ground
        # truth. Production boundary_completeness must never depend on ground truth.
        attribution = (
            None
            if sufficient_for_any
            else "RETRIEVAL_INSUFFICIENT_FOR_ANY_MATCH"
        )
        detail = {
            "actual_supported_frame_count": len(hits),
            "query_frame_count": len(frames),
            "retrieval_sufficient_for_any_match": sufficient_for_any,
            "retrieval_sufficient_for_full_boundary": sufficient_for_full,
            **stats,
        }
        return attribution, detail

    def run(
        self,
        samples_per_episode: int = 10,
        source_duration_seconds: float = 6.0,
        modes: tuple[str, ...] = REAL_PROFILES,
        include_speed_profiles: bool = False,
        seed: int = 20260903,
    ) -> dict[str, Any]:
        allowed = set(REAL_PROFILES) | {"speed_0.8x", "speed_1.25x"}
        requested_modes = list(modes)
        if include_speed_profiles:
            requested_modes.extend(["speed_0.8x", "speed_1.25x"])
        if set(requested_modes) - allowed:
            raise ValueError("Unsupported real temporal benchmark profile")
        project_id = self.database.initialize(self.config.paths.root)
        health = self.database.index_health(project_id)
        if health.get("status") != "COMPLETE":
            raise ValueError("A COMPLETE 2 FPS source index is required")
        rows = self.database.list_original_indexes(project_id)
        aliases = {
            str(row["episode"]): f"E{index:03d}" for index, row in enumerate(rows, start=1)
        }
        randomizer = random.Random(seed)
        cases: list[dict[str, Any]] = []
        profile_status: dict[str, dict[str, str]] = {}
        active_modes: list[str] = []
        for mode in requested_modes:
            if mode == "p3" and not _encoder_available("libx265"):
                profile_status[mode] = {
                    "status": "SKIPPED",
                    "reason": "libx265 HEVC encoder unavailable in local FFmpeg",
                }
            else:
                profile_status[mode] = {"status": "COMPLETED"}
                active_modes.append(mode)
        matcher = VideoMatcher(self.config, self.database)
        grid_period = 1.0 / self.config.matching.coarse_fps
        memory_before = resource_snapshot()
        temporary_root = self.config.paths.workspace / "temporal-benchmark-tmp"
        temporary_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temporary_root) as temporary_directory:
            temporary = Path(temporary_directory)
            for row in rows:
                episode = str(row["episode"])
                alias = aliases[episode]
                source_path = Path(row["file_path"])
                media_duration = float(row["duration"])
                usable = media_duration - source_duration_seconds - 0.5
                phases = _balanced_phase_assignments(randomizer, samples_per_episode)
                for sample_index in range(samples_per_episode):
                    bin_start = 0.25 + usable * sample_index / samples_per_episode
                    bin_end = 0.25 + usable * (sample_index + 1) / samples_per_episode
                    start = _sample_off_grid_start(
                        randomizer, bin_start, bin_end,
                        phases[sample_index] % PHASE_BUCKET_COUNT,
                    )
                    interval_id = f"{alias}-I{sample_index + 1:03d}"
                    for mode in active_modes:
                        speed = (
                            0.8 if mode == "speed_0.8x"
                            else 1.25 if mode == "speed_1.25x" else 1.0
                        )
                        suffix = ".mkv" if mode == "direct" else ".mp4"
                        query_path = temporary / f"{interval_id}-{mode}{suffix}"
                        if mode == "direct":
                            _make_lossless_direct_clip(
                                source_path, query_path, start, source_duration_seconds
                            )
                        elif mode in TRANSCODE_PROFILES:
                            profile = TRANSCODE_PROFILES[mode]
                            _make_platform_transcode_clip(
                                source_path, query_path, start, source_duration_seconds,
                                profile["codec"], profile["scale"], profile["bitrate"],
                                profile["maxrate"], profile["bufsize"],
                                profile.get("codec_params_flag"), profile.get("codec_params"),
                            )
                        else:
                            _make_clip(
                                source_path, query_path, start, source_duration_seconds,
                                f"setpts=PTS/{speed}",
                            )
                        accused_duration = source_duration_seconds / speed
                        truth = [_truth(
                            episode, 0.0, start, speed, accused_duration
                        )]
                        started = time.perf_counter()
                        result = matcher.analyze_file(
                            f"REAL_{interval_id}_{mode}", query_path, persist=False
                        )
                        elapsed = time.perf_counter() - started
                        metrics = evaluate_segments(
                            truth, result.segments, result.status, result.alternatives
                        )
                        attribution, retrieval_detail = self._retrieval_attribution(
                            matcher, query_path, episode, start, speed
                        )
                        if metrics["source_aware_recalled_segments"] == 0:
                            if attribution is None:
                                attribution = (
                                    "ALIGNMENT_LIMIT"
                                    if retrieval_detail[
                                        "retrieval_sufficient_for_full_boundary"
                                    ]
                                    else "RETRIEVAL_INSUFFICIENT_FOR_FULL_BOUNDARY"
                                )
                        else:
                            attribution = None
                        outcome = classify_case_outcome(
                            result.status, metrics, truth, result.alternatives
                        )
                        fully_recalled = (
                            metrics["source_aware_recalled_segments"]
                            == metrics["ground_truth_segments"]
                            and metrics["false_positive_segments"] == 0
                        )
                        cases.append({
                            "outcome": outcome,
                            "retrieval_attribution_detail": retrieval_detail,
                            "alternatives": [
                                segment.to_dict() for segment in result.alternatives
                            ],
                            "ambiguity_contains_ground_truth": _alternatives_contain_ground_truth(
                                result.alternatives, truth
                            ),
                            "boundary_assertion_outcome": classify_boundary_assertion(
                                result.segments, truth, fully_recalled
                            ),
                            "truncated_episodes": list(result.truncated_episodes),
                            "warnings": list(result.warnings),
                            "case_id": f"{interval_id}-{mode}",
                            "interval_id": interval_id,
                            "episode_alias": alias,
                            "profile": mode,
                            "normalized_source_start": round(start / media_duration, 8),
                            "source_subframe_phase_seconds": round(start % grid_period, 6),
                            "status": result.status,
                            "metrics": metrics,
                            "predictions": [item.to_dict() for item in result.segments],
                            "runtime_seconds": elapsed,
                            "failure_attribution": attribution,
                            "alignment_usable_candidate_frame_count": retrieval_detail.get(
                                "actual_supported_frame_count", 0
                            ),
                            "query_frame_count": retrieval_detail.get("query_frame_count", 0),
                        })
        memory_after = resource_snapshot()
        return {
            "schema_version": "temporal-alignment-characterization-v0.2",
            "created_at": datetime.now(UTC).isoformat(),
            "scope": "real_media_segment_level_temporal_alignment",
            "contains_media_identity": False,
            "episode_count": len(rows),
            "metric_semantics": {
                "outcome_taxonomy": (
                    "Four-way full-GT functional outcome; contained partial evidence "
                    "does not increase source-aware recall"
                ),
                "boundary_assertion_outcome": (
                    "Orthogonal safety label distinguishing an incorrect complete "
                    "assertion from explicitly incomplete contained evidence"
                ),
                "candidate_green": "Internal alignment-quality diagnostic only",
                "production_review_tier": "REVIEW_REQUIRED",
            },
            "configuration": {
                "source_fps": self.config.matching.coarse_fps,
                "query_fps": self.config.matching.coarse_fps,
                "retrieval_top_k": self.config.matching.top_k,
                "samples_per_episode": samples_per_episode,
                "source_duration_seconds": source_duration_seconds,
                "profiles": requested_modes,
                "timestamp_basis": "nominal_grid_time",
            },
            "profile_status": profile_status,
            "aggregate": _metric_summary(cases),
            "outcome_taxonomy": _outcome_summary(cases),
            "by_profile": _group_cases(cases, "profile"),
            "by_episode": _group_cases(cases, "episode_alias"),
            "by_interval": _group_cases(cases, "interval_id"),
            "position_level": _position_level_summary(cases),
            "performance": {
                "memory_before": memory_before,
                "memory_after": memory_after,
                "observed_peak_lower_bound_bytes": max(memory_after.values()),
                "conservative_combined_upper_bound_bytes": sum(memory_after.values()),
            },
            "cases": cases,
        }


def write_temporal_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
