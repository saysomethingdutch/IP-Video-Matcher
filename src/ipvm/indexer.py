from __future__ import annotations

import json
import hashlib
import logging
import math
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .config import AppConfig
from .database import Database
from .features import DCTVisualEncoder, ImageEncoder
from .media import MediaToolError, extract_grayscale_frames, find_video_files, probe_media, sha256_file


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IndexingResult:
    file_path: str
    episode: str | None
    status: str
    frame_count: int
    expected_frame_count: int
    timestamp_coverage: float
    duration: float
    elapsed_seconds: float
    cached: bool
    reason: str | None = None


def episode_from_path(path: Path) -> str | None:
    """Parse only explicit, unambiguous episode conventions."""
    stem = path.stem.strip()
    candidates: set[str] = set()
    season_episode = re.findall(r"(?i)(?<![A-Z0-9])S(\d{1,2})E(\d{1,3})(?!\d)", stem)
    candidates.update(f"S{int(season):02d}E{int(episode):02d}" for season, episode in season_episode)
    standard = re.findall(r"(?i)(?<![A-Z0-9])(?:EP|E)[ _.-]*0*(\d{1,3})(?!\d)", stem)
    candidates.update(f"EP{int(episode):02d}" for episode in standard)
    chinese = re.findall(r"第\s*0*(\d{1,3})\s*集", stem)
    candidates.update(f"EP{int(episode):02d}" for episode in chinese)
    return next(iter(candidates)) if len(candidates) == 1 else None


def load_episode_mapping(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw = payload.get("episodes", payload)
    if not isinstance(raw, dict):
        raise ValueError(f"Episode metadata must be an object mapping filenames to episodes: {path}")
    result: dict[str, str] = {}
    for filename, episode in raw.items():
        if not isinstance(filename, str) or not isinstance(episode, str) or not episode.strip():
            raise ValueError(f"Invalid episode metadata entry in {path}: {filename!r}")
        result[filename] = episode.strip()
    return result


def resolve_episode(path: Path, mapping: dict[str, str]) -> str | None:
    return mapping.get(path.name) or mapping.get(path.stem) or episode_from_path(path)


def validate_index_completeness(
    duration: float, fps: float, timestamps: list[float]
) -> dict[str, Any]:
    expected = max(1, int(round(duration * fps)))
    actual = len(timestamps)
    coverage = actual / fps if fps > 0 else 0.0
    tolerance_frames = max(2, int(math.ceil(expected * 0.01)))
    count_ok = abs(actual - expected) <= tolerance_frames
    coverage_tolerance = max(1.0, 2.0 / fps)
    coverage_ok = coverage >= duration - coverage_tolerance
    monotonic = all(right > left for left, right in zip(timestamps, timestamps[1:]))
    complete = actual > 0 and count_ok and coverage_ok and monotonic
    reasons = []
    if actual == 0:
        reasons.append("no sampled frames")
    if not count_ok:
        reasons.append(f"frame count {actual} outside expected {expected}±{tolerance_frames}")
    if not coverage_ok:
        reasons.append(f"timestamp coverage {coverage:.3f}s below media duration {duration:.3f}s")
    if not monotonic:
        reasons.append("timestamps are not strictly increasing")
    return {
        "status": "COMPLETE" if complete else "INCOMPLETE_INDEX",
        "expected_frame_count": expected,
        "actual_frame_count": actual,
        "timestamp_coverage": round(coverage, 6),
        "reason": "; ".join(reasons) or None,
    }


class OriginalIndexer:
    def __init__(
        self,
        config: AppConfig,
        database: Database,
        encoder: ImageEncoder | None = None,
    ):
        self.config = config
        self.database = database
        self.encoder = encoder or DCTVisualEncoder()
        self.project_id = database.initialize(config.paths.root)

    def index_all(self, force: bool = False) -> list[IndexingResult]:
        files = find_video_files(self.config.paths.originals)
        if not files:
            self.database.update_project_index_status(
                self.project_id, "INCOMPLETE_INDEX", 0, 0, "No original videos found"
            )
            raise FileNotFoundError(f"No original videos found in {self.config.paths.originals}")
        mapping = load_episode_mapping(self.config.paths.originals_metadata)
        results: list[IndexingResult] = []
        episodes_seen: set[str] = set()
        for path in files:
            episode = resolve_episode(path, mapping)
            if episode is None or episode in episodes_seen:
                reason = (
                    "episode mapping is missing or filename is ambiguous"
                    if episode is None else f"duplicate episode mapping: {episode}"
                )
                results.append(IndexingResult(
                    str(path.resolve()), episode, "NEED_METADATA", 0, 0, 0.0,
                    0.0, 0.0, False, reason,
                ))
                continue
            episodes_seen.add(episode)
            try:
                results.append(self.index_one(path, episode, force=force))
            except (MediaToolError, OSError, ValueError) as exc:
                logger.error("Indexing failed for %s: %s", path, exc)
                results.append(IndexingResult(
                    str(path.resolve()), episode, "ERROR", 0, 0, 0.0,
                    0.0, 0.0, False, str(exc),
                ))
        complete_count = sum(result.status == "COMPLETE" for result in results)
        project_complete = complete_count == len(files) == len(results)
        failure_reasons = [
            f"{Path(result.file_path).name}: {result.status} ({result.reason or 'unspecified'})"
            for result in results if result.status != "COMPLETE"
        ]
        self.database.update_project_index_status(
            self.project_id,
            "COMPLETE" if project_complete else "INCOMPLETE_INDEX",
            len(files), complete_count,
            None if project_complete else "; ".join(failure_reasons),
        )
        return results

    def index_one(self, path: Path, episode: str, force: bool = False) -> IndexingResult:
        started = time.perf_counter()
        metadata = probe_media(path)
        digest = sha256_file(path)
        cache_dir = self.config.paths.workspace / "indexes"
        cache_dir.mkdir(parents=True, exist_ok=True)
        encoder_identity = self.encoder.cache_identity()
        cache_identity = {
            "source_sha256": digest,
            "encoder": encoder_identity,
            "algorithm_version": self.config.matching.algorithm_version,
            "coarse_fps": self.config.matching.coarse_fps,
            "frame_width": self.config.matching.frame_width,
            "frame_height": self.config.matching.frame_height,
            "frame_extraction": {
                "colorspace": "gray8",
                "scaler": "ffmpeg-scale",
                "timestamp_origin": "sample_index_over_target_fps",
            },
        }
        identity_json = json.dumps(cache_identity, sort_keys=True, separators=(",", ":"))
        identity_digest = hashlib.sha256(identity_json.encode("utf-8")).hexdigest()[:24]
        encoder_key = re.sub(r"[^A-Za-z0-9_.-]+", "_", self.encoder.name)
        index_path = cache_dir / f"{digest}-{encoder_key}-{identity_digest}.npz"
        encoder_label = f"{self.encoder.name}@{self.encoder.version}"
        cached = index_path.exists() and not force
        if cached:
            with np.load(index_path) as cached_index:
                timestamps = cached_index["timestamps"].astype(float).tolist()
                cached_identity = str(cached_index["cache_identity_json"][0])
            if cached_identity != identity_json:
                cached = False
        if not cached:
            extracted = extract_grayscale_frames(
                path,
                fps=self.config.matching.coarse_fps,
                width=self.config.matching.frame_width,
                height=self.config.matching.frame_height,
            )
            timestamps = [timestamp for timestamp, _ in extracted]
            features = self.encoder.encode_images(frame for _, frame in extracted)
            np.savez_compressed(
                index_path,
                timestamps=np.asarray(timestamps, dtype=np.float32),
                features=features,
                encoder=np.asarray([encoder_label]),
                encoder_name=np.asarray([self.encoder.name]),
                encoder_version=np.asarray([self.encoder.version]),
                algorithm_version=np.asarray([self.config.matching.algorithm_version]),
                cache_identity_json=np.asarray([identity_json]),
                media_duration=np.asarray([metadata.duration], dtype=np.float64),
                target_fps=np.asarray([self.config.matching.coarse_fps], dtype=np.float64),
            )
        completeness = validate_index_completeness(
            metadata.duration, self.config.matching.coarse_fps, timestamps
        )
        asset_id = self.database.upsert_original(
            self.project_id, episode, path, digest, metadata,
            index_path, self.config.matching.algorithm_version, encoder_label, completeness,
        )
        if completeness["status"] == "COMPLETE":
            self.database.add_frame_rows(asset_id, timestamps, index_path, encoder_label)
        elapsed = time.perf_counter() - started
        logger.info(
            "Indexed original %s: %s (%d/%d frames)",
            episode, completeness["status"], len(timestamps), completeness["expected_frame_count"],
            extra={"duration": elapsed},
        )
        return IndexingResult(
            str(path.resolve()), episode, completeness["status"], len(timestamps),
            completeness["expected_frame_count"], completeness["timestamp_coverage"],
            metadata.duration, elapsed, cached, completeness.get("reason"),
        )
