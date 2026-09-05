"""Global anchored-affine DINO score contract for the Phase 2B-3 experiment.

The module deliberately lives outside :mod:`ipvm`.  It changes only the visual
score attached to each fixed top-8 candidate.  Candidate membership, source/time
identity and original retrieval rank are preserved in the experimental sidecar.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Iterable

from ipvm.models import CandidateMatch


DINO_BATCH_SIZE = 16
ALIGNER_ADMISSION_SCORE = 0.82
SCORE_MAPPING_VERSION = "phase2b3-anchored-affine-v1"


@dataclass(frozen=True)
class ScorePolicy:
    name: str
    tau: float


POLICY_GRID: tuple[ScorePolicy, ...] = (
    ScorePolicy("TAU_0.55", 0.55),
    ScorePolicy("TAU_0.60", 0.60),
    ScorePolicy("TAU_0.64", 0.64),
    ScorePolicy("TAU_0.68", 0.68),
    ScorePolicy("TAU_0.72", 0.72),
    ScorePolicy("TAU_0.76", 0.76),
    ScorePolicy("TAU_0.80", 0.80),
    ScorePolicy("TAU_0.82", 0.82),
)
POLICY_BY_NAME = {policy.name: policy for policy in POLICY_GRID}


@dataclass(frozen=True)
class AdaptedCandidateStream:
    candidates: tuple[CandidateMatch, ...]
    audit: tuple[dict[str, Any], ...]
    input_candidate_count: int


def _clip_unit(value: float) -> float:
    if not math.isfinite(value):
        raise ValueError("candidate score must be finite")
    return max(0.0, min(1.0, float(value)))


def anchored_affine_score(raw_cosine: float, policy: ScorePolicy) -> float:
    """Map DINO cosine to the frozen visual-score contract without pre-filtering."""

    score = float(raw_cosine)
    tau = float(policy.tau)
    if not math.isfinite(score):
        raise ValueError("candidate score must be finite")
    if not 0.0 <= tau < 1.0:
        raise ValueError("tau must be in [0, 1)")
    return _clip_unit(
        ALIGNER_ADMISSION_SCORE
        + (score - tau) * (1.0 - ALIGNER_ADMISSION_SCORE) / (1.0 - tau)
    )


def _candidate_records(frames: Iterable[dict[str, Any]]) -> Iterable[dict[str, Any]]:
    sorted_frames = sorted(frames, key=lambda frame: float(frame["accused_time"]))
    seen_frame_times: set[float] = set()
    for frame in sorted_frames:
        accused_time = float(frame["accused_time"])
        if not math.isfinite(accused_time) or accused_time in seen_frame_times:
            raise ValueError("frame times must be finite and unique")
        seen_frame_times.add(accused_time)
        top8 = sorted(frame["top8"], key=lambda item: int(item["rank"]))
        ranks = [int(item["rank"]) for item in top8]
        if ranks != list(range(1, 9)) or len(top8) != 8:
            raise ValueError("each frame must preserve the complete fixed top-8")
        identities: set[tuple[str, float]] = set()
        for item in top8:
            candidate_time = float(item["accused_time"])
            source_time = float(item["source_time"])
            raw_cosine = float(item["score"])
            if not all(math.isfinite(value) for value in (candidate_time, source_time, raw_cosine)):
                raise ValueError("candidate fields must be finite")
            if abs(candidate_time - accused_time) > 1e-7:
                raise ValueError("candidate accused time differs from its frame")
            identity = (str(item["source_id"]), source_time)
            if identity in identities:
                raise ValueError("duplicate source/time candidate in one frame")
            identities.add(identity)
            yield item


def adapt_candidate_frames(
    frames: Iterable[dict[str, Any]], policy: ScorePolicy
) -> AdaptedCandidateStream:
    """Adapt every fixed top-8 candidate; no candidate is manually deleted."""

    candidates: list[CandidateMatch] = []
    audit: list[dict[str, Any]] = []
    for item in _candidate_records(frames):
        raw_cosine = float(item["score"])
        adapted = anchored_affine_score(raw_cosine, policy)
        candidate = CandidateMatch(
            accused_time=float(item["accused_time"]),
            source_episode=str(item["source_id"]),
            source_time=float(item["source_time"]),
            visual_score=adapted,
        )
        candidates.append(candidate)
        audit.append(
            {
                "accused_time": candidate.accused_time,
                "source_time": candidate.source_time,
                "source_id": candidate.source_episode,
                "rank": int(item["rank"]),
                "raw_cosine": raw_cosine,
                "adapted_visual_score": adapted,
                "retained": True,
            }
        )
    return AdaptedCandidateStream(tuple(candidates), tuple(audit), len(candidates))


def candidate_identity(audit: Iterable[dict[str, Any]]) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        (
            item["accused_time"],
            item["source_time"],
            item["source_id"],
            item["rank"],
            item["raw_cosine"],
        )
        for item in audit
    )


def policy_metadata(policy: ScorePolicy) -> dict[str, Any]:
    return {
        "name": policy.name,
        "tau": policy.tau,
        "mapping_version": SCORE_MAPPING_VERSION,
        "formula": "clip(0.82 + (raw_cosine-tau)*0.18/(1-tau), 0, 1)",
        "effective_raw_admission_threshold": policy.tau,
        "manual_pre_filter": False,
        "dino_batch_size": DINO_BATCH_SIZE,
    }
