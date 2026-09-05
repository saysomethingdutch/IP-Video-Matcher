"""Backend-neutral temporal-separability and oracle diagnostics.

Ground truth is used only by explicitly named diagnostic functions.  Nothing in this
module participates in production retrieval, candidate admission, or alignment.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import math
from typing import Any, Iterable

import numpy as np

from ipvm.benchmarking import (
    _duration_slope_consistent,
    _overlap,
    _source_match,
    evaluate_segments,
)
from ipvm.models import MatchStatus, SegmentMatch


GRID_SECONDS = 0.5
EPSILON = 1e-6


def grouped_frames(audit: Iterable[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Return rank-ordered candidate records grouped by accused grid time."""

    by_time: dict[float, list[dict[str, Any]]] = defaultdict(list)
    for item in audit:
        by_time[float(item["accused_time"])].append(dict(item))
    frames = []
    for accused_time in sorted(by_time):
        frame = sorted(by_time[accused_time], key=lambda item: int(item["rank"]))
        if [int(item["rank"]) for item in frame] != list(range(1, len(frame) + 1)):
            raise ValueError("candidate ranks must be contiguous within each frame")
        identities = {(str(item["source_id"]), float(item["source_time"])) for item in frame}
        if len(identities) != len(frame):
            raise ValueError("duplicate source-frame identity within one query frame")
        frames.append(frame)
    return frames


def expected_source_time(truth: dict[str, Any], accused_time: float) -> float:
    return float(truth["source_start"]) + float(truth.get("speed_ratio", 1.0)) * (
        float(accused_time) - float(truth["accused_start"])
    )


def is_gt_near(item: dict[str, Any], truth: dict[str, Any]) -> bool:
    return (
        str(item["source_id"]) == str(truth["source_episode"])
        and abs(
            float(item["source_time"])
            - expected_source_time(truth, float(item["accused_time"]))
        )
        <= GRID_SECONDS + EPSILON
    )


def _identity(item: dict[str, Any]) -> tuple[str, float]:
    return str(item["source_id"]), float(item["source_time"])


def _summary(values: Iterable[float]) -> dict[str, Any]:
    array = np.asarray(list(values), dtype=np.float64)
    if not len(array):
        return {"count": 0, "mean": None, "median": None, "p10": None, "p90": None,
                "min": None, "max": None}
    return {
        "count": int(len(array)),
        "mean": float(np.mean(array)),
        "median": float(np.median(array)),
        "p10": float(np.percentile(array, 10)),
        "p90": float(np.percentile(array, 90)),
        "min": float(np.min(array)),
        "max": float(np.max(array)),
    }


def _order_metrics(sequence: list[tuple[float, float]]) -> dict[str, Any]:
    """Pairwise order statistics for (accused_time, source_time)."""

    ordered = sorted(sequence)
    inversions = ties = 0
    for left_index, (_, left_source) in enumerate(ordered):
        for _, right_source in ordered[left_index + 1 :]:
            if left_source > right_source + EPSILON:
                inversions += 1
            elif abs(left_source - right_source) <= EPSILON:
                ties += 1
    denominator = len(ordered) * (len(ordered) - 1) // 2
    return {
        "frame_count": len(ordered),
        "pair_count": denominator,
        "inversion_count": inversions,
        "tie_count": ties,
        "inversion_rate": inversions / denominator if denominator else None,
        "tie_rate": ties / denominator if denominator else None,
        "inversion_plus_tie_rate": (
            (inversions + ties) / denominator if denominator else None
        ),
    }


def _repeat_metrics(frame_candidates: list[list[dict[str, Any]]]) -> dict[str, Any]:
    membership: dict[tuple[str, float], set[int]] = defaultdict(set)
    for frame_index, candidates in enumerate(frame_candidates):
        for item in candidates:
            membership[_identity(item)].add(frame_index)
    multiplicities = [len(indices) for indices in membership.values()]
    return {
        "identity_count": len(membership),
        "repeated_identity_count": sum(value >= 2 for value in multiplicities),
        "repeat_surplus": sum(max(0, value - 1) for value in multiplicities),
        "maximum_multiplicity": max(multiplicities, default=0),
    }


def _entropy(values: list[tuple[str, float]]) -> dict[str, float] | None:
    if not values:
        return None
    counts = np.asarray(list(Counter(values).values()), dtype=np.float64)
    shares = counts / counts.sum()
    entropy = float(-np.sum(shares * np.log(shares)))
    slots = int(counts.sum())
    normalized = entropy / math.log(slots) if slots > 1 else 0.0
    return {
        "entropy": entropy,
        "effective_diversity": float(math.exp(entropy)),
        "normalized_entropy": normalized,
        "candidate_slots": slots,
    }


def temporal_separability_metrics(
    audit: Iterable[dict[str, Any]],
    truth: dict[str, Any],
    *,
    include_per_frame: bool = False,
) -> dict[str, Any]:
    frames = grouped_frames(audit)
    correct_episode = str(truth["source_episode"])
    per_frame: list[dict[str, Any]] = []
    nearest_sequence: list[tuple[float, float]] = []
    top1: list[dict[str, Any]] = []
    correct_frame_candidates: list[list[dict[str, Any]]] = []
    cosine_spreads: list[float] = []
    margins: list[float] = []

    for frame in frames:
        accused_time = float(frame[0]["accused_time"])
        correct = [item for item in frame if str(item["source_id"]) == correct_episode]
        correct_frame_candidates.append(correct)
        near = [item for item in frame if is_gt_near(item, truth)]
        nearest = min(
            correct,
            key=lambda item: (
                abs(float(item["source_time"]) - expected_source_time(truth, accused_time)),
                int(item["rank"]),
            ),
            default=None,
        )
        if nearest is not None:
            nearest_sequence.append((accused_time, float(nearest["source_time"])))
        all_episode_ranges = []
        for episode in {str(item["source_id"]) for item in frame}:
            values = [float(item["source_time"]) for item in frame if str(item["source_id"]) == episode]
            all_episode_ranges.append(max(values) - min(values))
        correct_values = [float(item["source_time"]) for item in correct]
        if correct:
            cosine_values = [float(item["raw_cosine"]) for item in correct]
            cosine_spreads.append(max(cosine_values) - min(cosine_values))
        outside = [item for item in frame if item not in near]
        if near and outside:
            margin = max(float(item["raw_cosine"]) for item in near) - max(
                float(item["raw_cosine"]) for item in outside
            )
            margins.append(margin)
        top1.append(frame[0])
        nearest_distance = (
            abs(float(nearest["source_time"]) - expected_source_time(truth, accused_time))
            if nearest is not None else None
        )
        per_frame.append({
            "accused_time": accused_time,
            "unique_source_frame_count": len({_identity(item) for item in frame}),
            "unique_correct_source_timestamp_count": len(set(correct_values)),
            "top1_source_id": str(frame[0]["source_id"]),
            "top1_source_time": float(frame[0]["source_time"]),
            "gt_nearest_rank": int(nearest["rank"]) if nearest is not None else None,
            "gt_nearest_distance_seconds": nearest_distance,
            "gt_nearest_in_band": bool(nearest is not None and nearest_distance <= GRID_SECONDS + EPSILON),
            "maximum_episode_local_spread_seconds": max(all_episode_ranges, default=0.0),
            "correct_source_spread_seconds": (
                max(correct_values) - min(correct_values) if correct_values else None
            ),
            "correct_source_raw_cosine_spread": (
                max(float(item["raw_cosine"]) for item in correct)
                - min(float(item["raw_cosine"]) for item in correct)
                if correct else None
            ),
            "gt_near_competition_margin": margin if near and outside else None,
        })

    top1_counts = Counter(_identity(item) for item in top1)
    monotonic_eligible = monotonic_violations = plateaus = 0
    for left, right in zip(top1, top1[1:]):
        if str(left["source_id"]) != str(right["source_id"]):
            continue
        monotonic_eligible += 1
        delta = float(right["source_time"]) - float(left["source_time"])
        if delta < -EPSILON:
            monotonic_violations += 1
        elif abs(delta) <= EPSILON:
            plateaus += 1

    windows: list[dict[str, float]] = []
    correct_windows: list[dict[str, float]] = []
    for index in range(len(frames)):
        start, end = max(0, index - 1), min(len(frames), index + 2)
        values = [_identity(item) for frame in frames[start:end] for item in frame]
        correct_values = [
            _identity(item)
            for frame in correct_frame_candidates[start:end]
            for item in frame
        ]
        entropy = _entropy(values)
        correct_entropy = _entropy(correct_values)
        if entropy is not None:
            windows.append(entropy)
        if correct_entropy is not None:
            correct_windows.append(correct_entropy)

    result = {
        "frame_count": len(frames),
        "candidate_count": sum(len(frame) for frame in frames),
        "unique_source_frames_per_query": _summary(
            item["unique_source_frame_count"] for item in per_frame
        ),
        "unique_correct_source_timestamps_per_query": _summary(
            item["unique_correct_source_timestamp_count"] for item in per_frame
        ),
        "correct_source_present_frames": sum(
            item["gt_nearest_rank"] is not None for item in per_frame
        ),
        "gt_near_hit_frames": sum(item["gt_nearest_in_band"] for item in per_frame),
        "gt_nearest_rank": _summary(
            item["gt_nearest_rank"] for item in per_frame if item["gt_nearest_rank"] is not None
        ),
        "gt_nearest_distance_seconds": _summary(
            item["gt_nearest_distance_seconds"]
            for item in per_frame if item["gt_nearest_distance_seconds"] is not None
        ),
        "maximum_episode_local_spread_seconds": _summary(
            item["maximum_episode_local_spread_seconds"] for item in per_frame
        ),
        "correct_source_spread_seconds": _summary(
            item["correct_source_spread_seconds"]
            for item in per_frame if item["correct_source_spread_seconds"] is not None
        ),
        "repetition_all_sources": _repeat_metrics(frames),
        "repetition_correct_source": _repeat_metrics(correct_frame_candidates),
        "shared_top1_fraction": (
            max(top1_counts.values(), default=0) / len(frames) if frames else None
        ),
        "top1_monotonicity": {
            "eligible_adjacent_pair_count": monotonic_eligible,
            "violation_count": monotonic_violations,
            "plateau_count": plateaus,
            "violation_rate": (
                monotonic_violations / monotonic_eligible if monotonic_eligible else None
            ),
            "plateau_rate": plateaus / monotonic_eligible if monotonic_eligible else None,
        },
        "gt_nearest_order": _order_metrics(nearest_sequence),
        "local_entropy": {
            "window_count": len(windows),
            "mean_entropy": float(np.mean([item["entropy"] for item in windows])) if windows else None,
            "mean_effective_diversity": (
                float(np.mean([item["effective_diversity"] for item in windows])) if windows else None
            ),
            "mean_normalized_entropy": (
                float(np.mean([item["normalized_entropy"] for item in windows])) if windows else None
            ),
        },
        "correct_source_local_entropy": {
            "window_count": len(correct_windows),
            "mean_entropy": (
                float(np.mean([item["entropy"] for item in correct_windows])) if correct_windows else None
            ),
            "mean_effective_diversity": (
                float(np.mean([item["effective_diversity"] for item in correct_windows]))
                if correct_windows else None
            ),
            "mean_normalized_entropy": (
                float(np.mean([item["normalized_entropy"] for item in correct_windows]))
                if correct_windows else None
            ),
        },
        "correct_source_raw_cosine_spread": _summary(cosine_spreads),
        "gt_near_competition_margin": {
            **_summary(margins),
            "non_positive_count": sum(value <= 0 for value in margins),
            "non_positive_fraction": (
                sum(value <= 0 for value in margins) / len(margins) if margins else None
            ),
        },
    }
    if include_per_frame:
        result["per_frame"] = per_frame
    return result


def segment_from_mapping(
    *, episode: str, accused_start: float, accused_end: float,
    source_start: float, source_end: float, slope: float | None,
) -> SegmentMatch:
    return SegmentMatch(
        accused_start=accused_start,
        accused_end=accused_end,
        source_episode=episode,
        source_start=source_start,
        source_end=source_end,
        speed_ratio=slope if min(accused_end - accused_start, source_end - source_start) > 3.0 else None,
        visual_score=1.0,
        audio_score=None,
        temporal_score=1.0,
        match_score=1.0,
    )


def segment_from_dict(value: dict[str, Any]) -> SegmentMatch:
    fields = SegmentMatch.__dataclass_fields__
    return SegmentMatch(**{name: value[name] for name in fields if name in value})


def mapping_evaluation(segment: SegmentMatch, truth: dict[str, Any]) -> dict[str, Any]:
    metrics = evaluate_segments([truth], [segment], MatchStatus.MATCH_FOUND.value)
    expected_duration = float(truth["accused_end"]) - float(truth["accused_start"])
    overlap_ratio = _overlap(
        segment.accused_start, segment.accused_end,
        float(truth["accused_start"]), float(truth["accused_end"]),
    ) / max(expected_duration, 1e-9)
    return {
        "complete": (
            metrics["source_aware_recalled_segments"] == 1
            and metrics["false_positive_segments"] == 0
        ),
        "source_identity": segment.source_episode == truth["source_episode"],
        "accused_overlap_ratio": overlap_ratio,
        "accused_assignment": overlap_ratio >= 0.5,
        "accused_start_error_seconds": abs(segment.accused_start - float(truth["accused_start"])),
        "accused_end_error_seconds": abs(segment.accused_end - float(truth["accused_end"])),
        "source_start_within_tolerance": (
            segment.source_episode == truth["source_episode"]
            and abs(segment.source_start - float(truth["source_start"])) <= 1.0
        ),
        "source_end_within_tolerance": (
            segment.source_episode == truth["source_episode"]
            and abs(segment.source_end - float(truth["source_end"])) <= 1.0
        ),
        "source_start_error_seconds": abs(segment.source_start - float(truth["source_start"])),
        "source_end_error_seconds": abs(segment.source_end - float(truth["source_end"])),
        "source_match": _source_match(truth, segment, 1.0),
        "duration_slope_consistent": _duration_slope_consistent(truth, segment, 1.0),
        "frozen_metrics": metrics,
    }


def _fit_points(points: list[dict[str, Any]], truth: dict[str, Any]) -> dict[str, Any]:
    if len({float(item["accused_time"]) for item in points}) < 2:
        return {"available": False, "candidate_count": len(points)}
    accused = np.asarray([float(item["accused_time"]) for item in points], dtype=np.float64)
    source = np.asarray([float(item["source_time"]) for item in points], dtype=np.float64)
    design = np.column_stack([accused, np.ones(len(accused))])
    slope, intercept = np.linalg.lstsq(design, source, rcond=None)[0]
    residuals = source - (slope * accused + intercept)
    accused_start = float(np.min(accused))
    accused_end = float(np.max(accused)) + GRID_SECONDS
    source_start = float(slope * accused_start + intercept)
    source_end = float(slope * accused_end + intercept)
    segment = segment_from_mapping(
        episode=str(truth["source_episode"]), accused_start=accused_start,
        accused_end=accused_end, source_start=source_start, source_end=source_end,
        slope=float(slope),
    )
    distinct_sources = len({float(item["source_time"]) for item in points})
    return {
        "available": True,
        "candidate_count": len(points),
        "distinct_accused_frames": len(set(accused.tolist())),
        "distinct_source_frames": distinct_sources,
        "duplicate_source_concentration": 1.0 - distinct_sources / len(points),
        "slope": float(slope),
        "intercept": float(intercept),
        "intercept_error_seconds": float(
            intercept
            - (float(truth["source_start"]) - float(truth.get("speed_ratio", 1.0)) * float(truth["accused_start"]))
        ),
        "rms_residual_seconds": float(np.sqrt(np.mean(np.square(residuals)))),
        "max_residual_seconds": float(np.max(np.abs(residuals))),
        "accused_start": accused_start,
        "accused_end": accused_end,
        "source_start": source_start,
        "source_end": source_end,
        "evaluation": mapping_evaluation(segment, truth),
    }


def oracle_band_diagnostics(
    audit: Iterable[dict[str, Any]], truth: dict[str, Any]
) -> dict[str, Any]:
    frames = grouped_frames(audit)
    band: list[dict[str, Any]] = []
    nearest: list[dict[str, Any]] = []
    for frame in frames:
        selected = [item for item in frame if is_gt_near(item, truth)]
        band.extend(selected)
        if selected:
            accused_time = float(frame[0]["accused_time"])
            nearest.append(min(
                selected,
                key=lambda item: (
                    abs(float(item["source_time"]) - expected_source_time(truth, accused_time)),
                    int(item["rank"]),
                ),
            ))
    by_frame: dict[float, list[dict[str, Any]]] = defaultdict(list)
    for item in band:
        by_frame[float(item["accused_time"])].append(item)
    repeat = _repeat_metrics(list(by_frame.values()))
    nearest_order = _order_metrics([
        (float(item["accused_time"]), float(item["source_time"])) for item in nearest
    ])
    return {
        "band_seconds": GRID_SECONDS,
        "query_frame_count": len(frames),
        "candidate_count": len(band),
        "covered_frame_count": len(by_frame),
        "frame_coverage": len(by_frame) / len(frames) if frames else None,
        "distinct_source_frame_count": len({float(item["source_time"]) for item in band}),
        "repetition": repeat,
        "nearest_order": nearest_order,
        "all_band_fit": _fit_points(band, truth),
        "nearest_per_frame_fit": _fit_points(nearest, truth),
    }


def sanitized_fit(fit: dict[str, Any]) -> dict[str, Any]:
    if not fit.get("available"):
        return dict(fit)
    evaluation = fit["evaluation"]
    return {
        key: fit[key] for key in (
            "available", "candidate_count", "distinct_accused_frames",
            "distinct_source_frames", "duplicate_source_concentration", "slope",
            "intercept_error_seconds", "rms_residual_seconds", "max_residual_seconds",
        )
    } | {
        "evaluation": {
            key: evaluation[key] for key in (
                "complete", "source_identity", "accused_overlap_ratio", "accused_assignment",
                "accused_start_error_seconds", "accused_end_error_seconds",
                "source_start_within_tolerance", "source_end_within_tolerance",
                "source_start_error_seconds", "source_end_error_seconds", "source_match",
                "duration_slope_consistent",
            )
        }
    }


def actual_fit_diagnostics(
    segment_value: dict[str, Any], truth: dict[str, Any], oracle_fit: dict[str, Any]
) -> dict[str, Any]:
    segment = segment_from_dict(segment_value)
    slope = (
        (segment.source_end - segment.source_start) / (segment.accused_end - segment.accused_start)
        if segment.accused_end > segment.accused_start else None
    )
    distinct_source = min(segment.inlier_count, max(0, round(
        (segment.source_end - segment.source_start) / GRID_SECONDS
    ) + 1))
    actual = {
        "slope": slope,
        "intercept": segment.source_start - (slope or 0.0) * segment.accused_start,
        "intercept_error_seconds": (
            segment.source_start - (slope or 0.0) * segment.accused_start
            - (float(truth["source_start"]) - float(truth.get("speed_ratio", 1.0)) * float(truth["accused_start"]))
        ),
        "rms_residual_seconds": segment.residual_rms_seconds,
        "max_residual_seconds": segment.residual_max_seconds,
        "accused_start_error_seconds": abs(segment.accused_start - float(truth["accused_start"])),
        "accused_end_error_seconds": abs(segment.accused_end - float(truth["accused_end"])),
        "source_start_error_seconds": abs(segment.source_start - float(truth["source_start"])),
        "source_end_error_seconds": abs(segment.source_end - float(truth["source_end"])),
        "inlier_count": segment.inlier_count,
        "distinct_accused_frames": segment.inlier_count,
        "distinct_source_frames_estimate": distinct_source,
        "duplicate_source_concentration_estimate": (
            1.0 - distinct_source / segment.inlier_count if segment.inlier_count else None
        ),
        "evaluation": mapping_evaluation(segment, truth),
    }
    if oracle_fit.get("available"):
        actual["delta_vs_nearest_oracle"] = {
            "slope": actual["slope"] - oracle_fit["slope"] if actual["slope"] is not None else None,
            "intercept_error_seconds": actual["intercept_error_seconds"] - oracle_fit["intercept_error_seconds"],
            "rms_residual_seconds": actual["rms_residual_seconds"] - oracle_fit["rms_residual_seconds"],
            "max_residual_seconds": actual["max_residual_seconds"] - oracle_fit["max_residual_seconds"],
            "source_start_absolute_error_seconds": actual["source_start_error_seconds"] - oracle_fit["evaluation"]["source_start_error_seconds"],
            "source_end_absolute_error_seconds": actual["source_end_error_seconds"] - oracle_fit["evaluation"]["source_end_error_seconds"],
        }
    return actual
