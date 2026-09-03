from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from enum import StrEnum


class MatchStatus(StrEnum):
    MATCH_FOUND = "MATCH_FOUND"
    MATCH_NOT_FOUND = "MATCH_NOT_FOUND"
    AMBIGUOUS = "AMBIGUOUS"
    INCOMPLETE_INDEX = "INCOMPLETE_INDEX"
    ERROR = "ERROR"


@dataclass(frozen=True)
class MediaMetadata:
    path: Path
    duration: float
    width: int
    height: int
    fps: float
    video_codec: str | None
    audio_codec: str | None
    sample_rate: int | None
    rotation: int
    creation_time: str | None

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["path"] = str(self.path)
        return result


@dataclass(frozen=True)
class CandidateMatch:
    accused_time: float
    source_episode: str
    source_time: float
    visual_score: float


@dataclass(frozen=True)
class SegmentMatch:
    accused_start: float
    accused_end: float
    source_episode: str
    source_start: float
    source_end: float
    speed_ratio: float | None
    visual_score: float
    audio_score: float | None
    temporal_score: float
    match_score: float
    mirrored: bool | None = None
    cropped: bool | None = None
    temporal_residual_seconds: float = 0.0
    fitted_offset_seconds: float | None = None
    residual_rms_seconds: float = 0.0
    residual_max_seconds: float = 0.0
    inlier_count: int = 0
    candidate_count: int = 0
    inlier_ratio: float = 0.0
    coverage: float = 0.0
    uniqueness_margin: float | None = None
    candidate_tier: str = "YELLOW"
    review_tier: str = "REVIEW_REQUIRED"
    segment_id: str | None = None
    conflict: bool = False
    conflict_with: tuple[str, ...] = ()
    conflict_reason: str | None = None
    source_reuse: bool = False
    speed_out_of_range: bool = False
    unconstrained_speed_ratio: float | None = None
    timestamp_basis: str = "nominal_grid_time"
    algorithm_version: str = "phase2a-temporal-v2"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
