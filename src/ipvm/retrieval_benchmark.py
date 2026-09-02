from __future__ import annotations

import json
import random
import statistics
import tempfile
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
            "median": statistics.median(margins) if margins else None,
            "p10": (
                float(np.percentile(np.asarray(margins), 10, method="lower"))
                if margins else None
            ),
        },
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
                for sample_index in range(samples_per_episode):
                    bin_start = 0.25 + usable * sample_index / samples_per_episode
                    bin_end = 0.25 + usable * (sample_index + 1) / samples_per_episode
                    start = randomizer.uniform(bin_start, bin_end)
                    phase = start % 0.5
                    if min(phase, 0.5 - phase) < 0.02:
                        start += 0.071
                    start = round(min(start, duration - query_duration_seconds - 0.25), 6)
                    for mode in modes:
                        if mode == "direct":
                            frames = extract_grayscale_frames(
                                source_path,
                                fps=2.0,
                                width=self.config.matching.frame_width,
                                height=self.config.matching.frame_height,
                                start_seconds=start,
                                duration_seconds=query_duration_seconds,
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
                        interval_count += 1
                        for (query_timestamp, _), feature in zip(frames, features, strict=True):
                            expected_source_timestamp = start + float(query_timestamp)
                            measurement = characterize_query_frame(
                                feature, source_indexes, alias,
                                expected_source_timestamp, tolerance_seconds,
                            )
                            observations.append({
                                "episode": alias,
                                "query_mode": mode,
                                "query_timestamp": round(float(query_timestamp), 6),
                                "expected_source_timestamp": round(expected_source_timestamp, 6),
                                "source_query_temporal_phase": round(expected_source_timestamp % 0.5, 6),
                                **measurement,
                            })

        aggregate = aggregate_observations(observations)
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
                "encoder": self.encoder.cache_identity(),
            },
            "episode_count": len(rows),
            "sampled_interval_count": interval_count,
            "aggregate": aggregate,
            "observations": observations,
            "decision": None,
            "decision_note": "Architecture Owner applies the Phase 2A-0 decision tree; harness reports measurement only.",
        }


def markdown_summary(report: dict[str, Any]) -> str:
    recall = report["aggregate"]["retrieval_recall_at_k"]
    lines = [
        "# Phase 2A-0 Retrieval Characterization",
        "",
        "> Measurement only. Candidate GREEN is not an automatic-release state.",
        "",
        f"- Episodes: {report['episode_count']} (anonymized aliases)",
        f"- Sampled intervals: {report['sampled_interval_count']}",
        f"- Query frames: {report['aggregate']['query_frame_count']}",
        f"- recall@1: {recall['1']}",
        f"- recall@8: {recall['8']}",
        f"- recall@32: {recall['32']}",
        f"- recall@128: {recall['128']}",
        f"- Median correct-source rank: {report['aggregate']['median_rank']}",
        f"- P90 correct-source rank: {report['aggregate']['p90_rank']}",
        "",
        "No original filename, path, SHA-256, or case identity is included.",
        "Architecture Owner must apply the decision tree; this report makes no route decision.",
    ]
    return "\n".join(lines) + "\n"


def write_report(report: dict[str, Any], json_path: Path, markdown_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    markdown_path.write_text(markdown_summary(report), encoding="utf-8")
