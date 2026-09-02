from __future__ import annotations

import json
import math
import random
import statistics
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from .benchmarking import _make_clip
from .config import AppConfig
from .database import Database
from .features import DCTVisualEncoder, ImageEncoder
from .media import extract_grayscale_frames


TOP_K_VALUES = (1, 8, 32, 128)
SOURCE_GRID_PERIOD_SECONDS = 0.5
PHASE_BUCKET_COUNT = 10
POSITION_BUCKET_COUNT = 5


@dataclass(frozen=True)
class SourceIndex:
    episode_alias: str
    timestamps: np.ndarray
    features: np.ndarray


def characterize_query_frame(
    query_feature: np.ndarray,
    source_indexes: Iterable[SourceIndex],
    expected_episode_alias: str,
    expected_source_timestamp: float,
    tolerance_seconds: float,
) -> dict[str, Any]:
    ranked: list[tuple[float, str, float]] = []
    for source in source_indexes:
        scores = source.features @ query_feature
        ranked.extend(
            (float(score), source.episode_alias, float(timestamp))
            for score, timestamp in zip(scores, source.timestamps, strict=True)
        )
    ranked.sort(key=lambda item: (-item[0], item[1], item[2]))
    correct_ranks = [
        rank for rank, (_, episode, timestamp) in enumerate(ranked, start=1)
        if episode == expected_episode_alias
        and abs(timestamp - expected_source_timestamp) <= tolerance_seconds
    ]
    correct_rank = min(correct_ranks) if correct_ranks else None
    correct_scores = [
        score for score, episode, timestamp in ranked
        if episode == expected_episode_alias
        and abs(timestamp - expected_source_timestamp) <= tolerance_seconds
    ]
    incorrect_scores = [
        score for score, episode, timestamp in ranked
        if not (
            episode == expected_episode_alias
            and abs(timestamp - expected_source_timestamp) <= tolerance_seconds
        )
    ]
    score_margin = None
    if correct_scores and incorrect_scores:
        score_margin = max(correct_scores) - max(incorrect_scores)
    return {
        "correct_source_rank": correct_rank,
        "score_margin": round(score_margin, 8) if score_margin is not None else None,
        "hits": {str(k): bool(correct_rank is not None and correct_rank <= k) for k in TOP_K_VALUES},
    }


def aggregate_observations(observations: list[dict[str, Any]]) -> dict[str, Any]:
    ranks = [int(item["correct_source_rank"]) for item in observations if item["correct_source_rank"]]
    margins = [float(item["score_margin"]) for item in observations if item["score_margin"] is not None]
    count = len(observations)
    distribution = {"1": 0, "2-8": 0, "9-32": 0, "33-128": 0, ">128": 0, "not_found": 0}
    for item in observations:
        rank = item["correct_source_rank"]
        if rank is None:
            distribution["not_found"] += 1
        elif rank == 1:
            distribution["1"] += 1
        elif rank <= 8:
            distribution["2-8"] += 1
        elif rank <= 32:
            distribution["9-32"] += 1
        elif rank <= 128:
            distribution["33-128"] += 1
        else:
            distribution[">128"] += 1
    return {
        "query_frame_count": count,
        "retrieval_recall_at_k": {
            str(k): (
                sum(bool(item["hits"][str(k)]) for item in observations) / count
                if count else None
            )
            for k in TOP_K_VALUES
        },
        "correct_source_rank_distribution": distribution,
        "median_rank": statistics.median(ranks) if ranks else None,
        "p90_rank": (
            float(np.percentile(np.asarray(ranks), 90, method="higher")) if ranks else None
        ),
        "score_margin": {
            "count": len(margins),
            "mean": statistics.fmean(margins) if margins else None,
            "median": statistics.median(margins) if margins else None,
            "p10": (
                float(np.percentile(np.asarray(margins), 10, method="lower"))
                if margins else None
            ),
            "p90": (
                float(np.percentile(np.asarray(margins), 90, method="higher"))
                if margins else None
            ),
            "minimum": min(margins) if margins else None,
            "maximum": max(margins) if margins else None,
        },
    }


def _position_bucket(normalized_position: float) -> str:
    index = min(POSITION_BUCKET_COUNT - 1, int(normalized_position * POSITION_BUCKET_COUNT))
    lower = index * 100 // POSITION_BUCKET_COUNT
    upper = (index + 1) * 100 // POSITION_BUCKET_COUNT
    return f"P{index + 1}_{lower:02d}-{upper:02d}%"


def _phase_bucket(phase_seconds: float) -> str:
    index = min(
        PHASE_BUCKET_COUNT - 1,
        int((phase_seconds / SOURCE_GRID_PERIOD_SECONDS) * PHASE_BUCKET_COUNT),
    )
    lower = index * SOURCE_GRID_PERIOD_SECONDS / PHASE_BUCKET_COUNT
    upper = (index + 1) * SOURCE_GRID_PERIOD_SECONDS / PHASE_BUCKET_COUNT
    return f"B{index + 1:02d}_{lower:.3f}-{upper:.3f}s"


def _sample_off_grid_start(
    randomizer: random.Random,
    bin_start: float,
    bin_end: float,
    phase_bucket_index: int,
) -> float:
    """Sample a random start in one time stratum and one source-grid phase stratum."""
    phase_width = SOURCE_GRID_PERIOD_SECONDS / PHASE_BUCKET_COUNT
    phase_low = phase_bucket_index * phase_width
    phase_high = (phase_bucket_index + 1) * phase_width
    # Keep every query measurably off the 2 FPS source grid, including edge buckets.
    phase_low = max(phase_low, 0.021)
    phase_high = min(phase_high, SOURCE_GRID_PERIOD_SECONDS - 0.021)
    if phase_low >= phase_high:
        raise ValueError("Phase bucket cannot produce an off-grid sample")
    phase = randomizer.uniform(phase_low, phase_high)
    first_grid = math.ceil((bin_start - phase) / SOURCE_GRID_PERIOD_SECONDS)
    last_grid = math.floor((bin_end - phase) / SOURCE_GRID_PERIOD_SECONDS)
    if first_grid > last_grid:
        raise ValueError("Time stratum is too narrow for phase-stratified sampling")
    grid_index = randomizer.randint(first_grid, last_grid)
    return round(grid_index * SOURCE_GRID_PERIOD_SECONDS + phase, 6)


def _balanced_phase_assignments(
    randomizer: random.Random, sample_count: int
) -> list[int]:
    assignments = [index % PHASE_BUCKET_COUNT for index in range(sample_count)]
    randomizer.shuffle(assignments)
    return assignments


def _grouped_aggregates(
    observations: list[dict[str, Any]], key: str
) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for observation in observations:
        groups[str(observation[key])].append(observation)
    return {
        group: aggregate_observations(items)
        for group, items in sorted(groups.items())
    }


def _episode_aggregates(observations: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    episodes: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for observation in observations:
        episodes[str(observation["episode"])].append(observation)
    result: dict[str, dict[str, Any]] = {}
    for episode, items in sorted(episodes.items()):
        result[episode] = {
            **aggregate_observations(items),
            "by_query_mode": _grouped_aggregates(items, "query_mode"),
        }
    return result


def _interval_aggregates(observations: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    intervals: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for observation in observations:
        intervals[str(observation["interval_id"])].append(observation)
    result: dict[str, dict[str, Any]] = {}
    for interval_id, items in sorted(intervals.items()):
        first = items[0]
        result[interval_id] = {
            "episode": first["episode"],
            "normalized_source_start": first["normalized_source_start"],
            "source_position_bucket": first["source_position_bucket"],
            "source_subframe_phase_seconds": first["interval_start_phase_seconds"],
            "aggregate": aggregate_observations(items),
            "by_query_mode": _grouped_aggregates(items, "query_mode"),
        }
    return result


def _observed_threshold_band(aggregate: dict[str, Any]) -> str:
    recall = aggregate["retrieval_recall_at_k"]
    if recall["8"] is not None and recall["8"] >= 0.95:
        return "RECALL_AT_8_GTE_95_PERCENT"
    if any(recall[str(k)] is not None and recall[str(k)] >= 0.95 for k in (32, 128)):
        return "RECALL_AT_8_LT_95_BUT_AT_32_OR_128_GTE_95_PERCENT"
    return "RECALL_AT_128_LT_95_PERCENT"


def anonymized_aggregate_report(report: dict[str, Any]) -> dict[str, Any]:
    """Remove frame-level observations while retaining anonymous aggregate evidence."""
    return {
        key: value
        for key, value in report.items()
        if key != "observations"
    }


class RetrievalBenchmark:
    """Phase 2A-0 measurement harness. It does not change matcher behavior."""

    def __init__(
        self,
        config: AppConfig,
        database: Database,
        encoder: ImageEncoder | None = None,
    ):
        self.config = config
        self.database = database
        self.encoder = encoder or DCTVisualEncoder()
        if config.matching.coarse_fps != 2.0:
            raise ValueError("Phase 2A-0 baseline requires source/query sampling at exactly 2 FPS")

    def run(
        self,
        samples_per_episode: int = 3,
        query_duration_seconds: float = 6.0,
        modes: tuple[str, ...] = ("direct", "reencode"),
        seed: int = 20260902,
        tolerance_seconds: float = 0.26,
    ) -> dict[str, Any]:
        invalid_modes = set(modes) - {"direct", "reencode"}
        if invalid_modes:
            raise ValueError(f"Unsupported query modes: {sorted(invalid_modes)}")
        if not modes or len(set(modes)) != len(modes):
            raise ValueError("Query modes must be non-empty and unique")
        project_id = self.database.initialize(self.config.paths.root)
        health = self.database.index_health(project_id)
        if health.get("status") != "COMPLETE":
            raise ValueError("A COMPLETE 2 FPS original index is required before retrieval benchmark")
        rows = self.database.list_original_indexes(project_id)
        if not rows:
            raise ValueError("No indexed original episodes are available")
        aliases = {str(row["episode"]): f"E{index:03d}" for index, row in enumerate(rows, start=1)}
        source_indexes: list[SourceIndex] = []
        for row in rows:
            alias = aliases[str(row["episode"])]
            expected_encoder = f"{self.encoder.name}@{self.encoder.version}"
            if str(row["encoder"]) != expected_encoder:
                raise ValueError(
                    f"{alias} index encoder does not match retrieval harness; rebuild originals"
                )
            with np.load(Path(row["index_path"])) as payload:
                try:
                    target_fps = float(payload["target_fps"][0])
                    timestamps = payload["timestamps"].astype(np.float32)
                    features = payload["features"].astype(np.float32)
                except KeyError as exc:
                    raise ValueError(
                        f"{alias} index lacks Phase 2A-0 identity fields; rebuild originals"
                    ) from exc
                if target_fps != 2.0:
                    raise ValueError(f"{alias} source index is not 2 FPS")
                source_indexes.append(SourceIndex(
                    alias,
                    timestamps,
                    features,
                ))

        randomizer = random.Random(seed)
        observations: list[dict[str, Any]] = []
        interval_count = 0
        query_variant_count = 0
        temporary_root = self.config.paths.workspace / "retrieval-benchmark-tmp"
        temporary_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temporary_root) as temporary_directory:
            temp = Path(temporary_directory)
            for row in rows:
                episode = str(row["episode"])
                alias = aliases[episode]
                duration = float(row["duration"])
                if duration <= query_duration_seconds + 1.0:
                    raise ValueError(
                        f"{alias} is too short for the requested off-grid query duration"
                    )
                source_path = Path(row["file_path"])
                usable = duration - query_duration_seconds - 0.5
                phase_assignments = _balanced_phase_assignments(
                    randomizer, samples_per_episode
                )
                for sample_index in range(samples_per_episode):
                    bin_start = 0.25 + usable * sample_index / samples_per_episode
                    bin_end = 0.25 + usable * (sample_index + 1) / samples_per_episode
                    phase_bucket_index = phase_assignments[sample_index]
                    start = _sample_off_grid_start(
                        randomizer, bin_start, bin_end, phase_bucket_index
                    )
                    normalized_start = start / duration
                    interval_id = f"{alias}-I{sample_index + 1:03d}"
                    interval_start_phase = start % SOURCE_GRID_PERIOD_SECONDS
                    interval_count += 1
                    for mode in modes:
                        if mode == "direct":
                            frames = extract_grayscale_frames(
                                source_path,
                                fps=2.0,
                                width=self.config.matching.frame_width,
                                height=self.config.matching.frame_height,
                                start_seconds=start,
                                duration_seconds=query_duration_seconds,
                                input_seek=True,
                                reset_timestamps=True,
                            )
                        else:
                            query_path = temp / f"{alias}-{sample_index:03d}.mp4"
                            _make_clip(
                                source_path, query_path, start, query_duration_seconds
                            )
                            frames = extract_grayscale_frames(
                                query_path,
                                fps=2.0,
                                width=self.config.matching.frame_width,
                                height=self.config.matching.frame_height,
                            )
                        features = self.encoder.encode_images(frame for _, frame in frames)
                        query_variant_count += 1
                        for (query_timestamp, _), feature in zip(frames, features, strict=True):
                            expected_source_timestamp = start + float(query_timestamp)
                            measurement = characterize_query_frame(
                                feature, source_indexes, alias,
                                expected_source_timestamp, tolerance_seconds,
                            )
                            observations.append({
                                "episode": alias,
                                "interval_id": interval_id,
                                "query_mode": mode,
                                "query_timestamp": round(float(query_timestamp), 6),
                                "expected_source_timestamp": round(expected_source_timestamp, 6),
                                "normalized_source_start": round(normalized_start, 8),
                                "source_position_bucket": _position_bucket(normalized_start),
                                "query_subframe_phase_seconds": round(
                                    float(query_timestamp) % SOURCE_GRID_PERIOD_SECONDS, 6
                                ),
                                "source_subframe_phase_seconds": round(
                                    expected_source_timestamp % SOURCE_GRID_PERIOD_SECONDS, 6
                                ),
                                "source_query_temporal_phase": round(
                                    expected_source_timestamp % SOURCE_GRID_PERIOD_SECONDS, 6
                                ),
                                "source_phase_bucket": _phase_bucket(
                                    expected_source_timestamp % SOURCE_GRID_PERIOD_SECONDS
                                ),
                                "interval_start_phase_seconds": round(
                                    interval_start_phase, 6
                                ),
                                **measurement,
                            })

        aggregate = aggregate_observations(observations)
        by_interval = _interval_aggregates(observations)
        phase_distances = [
            min(
                float(item["source_subframe_phase_seconds"]),
                SOURCE_GRID_PERIOD_SECONDS - float(item["source_subframe_phase_seconds"]),
            )
            for item in observations
        ]
        return {
            "schema_version": "retrieval-characterization-v0.1",
            "created_at": datetime.now(UTC).isoformat(),
            "scope": "phase2a_0_retrieval_characterization",
            "measurement_only": True,
            "contains_media_identity": False,
            "configuration": {
                "source_fps": 2.0,
                "query_fps": 2.0,
                "top_k_values": list(TOP_K_VALUES),
                "tolerance_seconds": tolerance_seconds,
                "samples_per_episode": samples_per_episode,
                "query_duration_seconds": query_duration_seconds,
                "query_modes": list(modes),
                "source_grid_period_seconds": SOURCE_GRID_PERIOD_SECONDS,
                "phase_bucket_count": PHASE_BUCKET_COUNT,
                "source_position_bucket_count": POSITION_BUCKET_COUNT,
                "sampling": "time-stratified random off-grid starts with shuffled balanced phase strata",
                "encoder": self.encoder.cache_identity(),
            },
            "episode_count": len(rows),
            "sampled_interval_count": interval_count,
            "query_variant_count": query_variant_count,
            "aggregate": aggregate,
            "by_episode": _episode_aggregates(observations),
            "by_query_mode": _grouped_aggregates(observations, "query_mode"),
            "by_source_position_bucket": _grouped_aggregates(
                observations, "source_position_bucket"
            ),
            "by_source_phase_bucket": _grouped_aggregates(
                observations, "source_phase_bucket"
            ),
            "by_interval": by_interval,
            "sampling_audit": {
                "all_query_frames_off_source_grid": all(distance >= 0.02 for distance in phase_distances),
                "minimum_distance_from_source_grid_seconds": min(phase_distances),
                "query_subframe_phase_values_seconds": sorted({
                    float(item["query_subframe_phase_seconds"]) for item in observations
                }),
                "source_subframe_phase_range_seconds": {
                    "minimum": min(
                        float(item["source_subframe_phase_seconds"])
                        for item in observations
                    ),
                    "maximum": max(
                        float(item["source_subframe_phase_seconds"])
                        for item in observations
                    ),
                },
                "interval_count_by_episode": {
                    episode: sum(
                        interval["episode"] == episode for interval in by_interval.values()
                    )
                    for episode in sorted({interval["episode"] for interval in by_interval.values()})
                },
                "interval_count_by_source_position_bucket": {
                    bucket: sum(
                        interval["source_position_bucket"] == bucket
                        for interval in by_interval.values()
                    )
                    for bucket in sorted(
                        {interval["source_position_bucket"] for interval in by_interval.values()}
                    )
                },
                "interval_count_by_source_phase_bucket": {
                    bucket: sum(
                        _phase_bucket(float(interval["source_subframe_phase_seconds"])) == bucket
                        for interval in by_interval.values()
                    )
                    for bucket in sorted(
                        {
                            _phase_bucket(float(interval["source_subframe_phase_seconds"]))
                            for interval in by_interval.values()
                        }
                    )
                },
            },
            "observed_threshold_band": _observed_threshold_band(aggregate),
            "observations": observations,
            "decision": None,
            "decision_note": "Architecture Owner applies the Phase 2A-0 decision tree; harness reports measurement only.",
        }


def markdown_summary(report: dict[str, Any]) -> str:
    def percent(value: float | None) -> str:
        return "n/a" if value is None else f"{value:.2%}"

    def number(value: float | int | None, digits: int = 6) -> str:
        if value is None:
            return "n/a"
        if float(value).is_integer():
            return str(int(value))
        return f"{float(value):.{digits}f}"

    recall = report["aggregate"]["retrieval_recall_at_k"]
    score_margin = report["aggregate"]["score_margin"]
    lines = [
        "# Phase 2A-0 Retrieval Characterization",
        "",
        "> Measurement only. Candidate GREEN is not an automatic-release state.",
        "",
        f"- Episodes: {report['episode_count']} (anonymized aliases)",
        f"- Sampled intervals: {report['sampled_interval_count']}",
        f"- Query variants: {report.get('query_variant_count', report['sampled_interval_count'])}",
        f"- Query frames: {report['aggregate']['query_frame_count']}",
        f"- recall@1: {percent(recall['1'])}",
        f"- recall@8: {percent(recall['8'])}",
        f"- recall@32: {percent(recall['32'])}",
        f"- recall@128: {percent(recall['128'])}",
        f"- Median correct-source rank: {number(report['aggregate']['median_rank'])}",
        f"- P90 correct-source rank: {number(report['aggregate']['p90_rank'])}",
        "- Score margin p10 / median / p90: "
        f"{number(score_margin['p10'])} / {number(score_margin['median'])} / "
        f"{number(score_margin.get('p90'))}",
        f"- Observed threshold band: {report.get('observed_threshold_band', 'NOT_REPORTED')}",
        "",
        "## Correct-source rank distribution",
        "",
        "| Rank band | Frames |",
        "|---|---:|",
    ]
    lines.extend(
        f"| {rank_band} | {count} |"
        for rank_band, count in report["aggregate"]["correct_source_rank_distribution"].items()
    )

    def add_group_table(title: str, groups: dict[str, dict[str, Any]]) -> None:
        lines.extend([
            "",
            f"## {title}",
            "",
            "| Group | Frames | R@1 | R@8 | R@32 | R@128 | Median rank | P90 rank | Margin median |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ])
        for group, metrics in groups.items():
            grouped_recall = metrics["retrieval_recall_at_k"]
            lines.append(
                f"| {group} | {metrics['query_frame_count']} | {percent(grouped_recall['1'])} | "
                f"{percent(grouped_recall['8'])} | {percent(grouped_recall['32'])} | "
                f"{percent(grouped_recall['128'])} | {number(metrics['median_rank'])} | "
                f"{number(metrics['p90_rank'])} | "
                f"{number(metrics['score_margin']['median'])} |"
            )

    if report.get("by_episode"):
        add_group_table("By episode", report["by_episode"])
    if report.get("by_query_mode"):
        add_group_table("By query mode", report["by_query_mode"])
    if report.get("by_source_position_bucket"):
        add_group_table("By normalized source position", report["by_source_position_bucket"])
    if report.get("by_source_phase_bucket"):
        add_group_table("By source/query sub-frame phase", report["by_source_phase_bucket"])
    if report.get("by_interval"):
        interval_groups = {
            interval_id: {
                **interval["aggregate"],
            }
            for interval_id, interval in report["by_interval"].items()
        }
        add_group_table("By sampled interval", interval_groups)
        lines.extend([
            "",
            "Interval aliases are anonymous. Normalized positions and exact off-grid phases are in the JSON aggregate.",
        ])

    lines.extend([
        "",
        "No original filename, path, SHA-256, or case identity is included.",
        "Architecture Owner must apply the decision tree; this report makes no route decision.",
    ])
    return "\n".join(lines) + "\n"


def write_report(
    report: dict[str, Any],
    json_path: Path,
    markdown_path: Path,
    aggregate_json_path: Path | None = None,
) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    markdown_path.write_text(markdown_summary(report), encoding="utf-8")
    if aggregate_json_path is not None:
        aggregate_json_path.parent.mkdir(parents=True, exist_ok=True)
        aggregate_json_path.write_text(
            json.dumps(
                anonymized_aggregate_report(report),
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            ),
            encoding="utf-8",
        )
