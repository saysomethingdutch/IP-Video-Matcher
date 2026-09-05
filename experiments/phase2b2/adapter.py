"""Experimental DINO candidate adapter for frozen Temporal Alignment v2.

This module deliberately stays outside :mod:`ipvm`.  It adapts the score scale
of already-retrieved candidates but never changes their identity or timestamps.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Iterable

from ipvm.models import CandidateMatch


DINO_BATCH_SIZE = 16


@dataclass(frozen=True)
class AdapterPolicy:
    """One pre-registered global score policy."""

    name: str
    floor: float | None = None

    @property
    def is_raw(self) -> bool:
        return self.floor is None


POLICY_GRID: tuple[AdapterPolicy, ...] = (
    AdapterPolicy("RAW"),
    AdapterPolicy("FLOOR_0.45", 0.45),
    AdapterPolicy("FLOOR_0.50", 0.50),
    AdapterPolicy("FLOOR_0.55", 0.55),
    AdapterPolicy("FLOOR_0.60", 0.60),
    AdapterPolicy("FLOOR_0.65", 0.65),
    AdapterPolicy("FLOOR_0.70", 0.70),
)

POLICY_BY_NAME = {policy.name: policy for policy in POLICY_GRID}


@dataclass(frozen=True)
class AdaptedCandidateStream:
    """Aligner input plus a lossless experimental audit sidecar."""

    candidates: tuple[CandidateMatch, ...]
    audit: tuple[dict[str, Any], ...]
    input_candidate_count: int
    retained_candidate_count: int

    @property
    def retained_fraction(self) -> float:
        if not self.input_candidate_count:
            return 0.0
        return self.retained_candidate_count / self.input_candidate_count


def _clip_unit(value: float) -> float:
    if not math.isfinite(value):
        raise ValueError("candidate score must be finite")
    return max(0.0, min(1.0, float(value)))


def adapt_dino_score(raw_cosine: float, policy: AdapterPolicy) -> float | None:
    """Map one DINO cosine according to the frozen global policy family."""

    score = float(raw_cosine)
    if policy.floor is None:
        return _clip_unit(score)
    floor = float(policy.floor)
    if not 0.0 <= floor < 1.0:
        raise ValueError("adapter floor must be in [0, 1)")
    if score < floor:
        return None
    return _clip_unit((score - floor) / (1.0 - floor))


def adapt_dct_score(raw_cosine: float) -> float:
    """Reproduce the frozen production DCT candidate score mapping."""

    return _clip_unit((float(raw_cosine) + 1.0) / 2.0)


def _iter_candidate_records(frames: Iterable[dict[str, Any]]) -> Iterable[dict[str, Any]]:
    for frame in frames:
        accused_time = float(frame["accused_time"])
        top8 = sorted(frame["top8"], key=lambda c: int(c["rank"]))
        ranks = [int(candidate["rank"]) for candidate in top8]
        if ranks != list(range(1, len(top8) + 1)):
            raise ValueError("candidate ranks must be consecutive and start at one")
        if len(top8) > 8:
            raise ValueError("Phase 2B-2 candidate stream exceeds frozen top-k 8")
        if not math.isfinite(accused_time):
            raise ValueError("candidate timestamp must be finite")
        identities = {(c["source_id"], float(c["source_time"])) for c in top8}
        if len(identities) != len(top8):
            raise ValueError("duplicate source/time candidate")
        for candidate in top8:
            if not all(math.isfinite(float(candidate[key])) for key in ("score", "source_time", "accused_time")):
                raise ValueError("candidate fields must be finite")
            if abs(float(candidate["accused_time"]) - accused_time) > 1e-7:
                raise ValueError("candidate accused_time differs from its frame")
            yield candidate


def adapt_candidate_frames(
    frames: Iterable[dict[str, Any]],
    *,
    backend: str,
    policy: AdapterPolicy | None = None,
) -> AdaptedCandidateStream:
    """Adapt Phase 2B-1 frame records into production ``CandidateMatch`` objects.

    ``backend`` is either ``DINO_RAW`` or ``DCT``.  DINO requires an explicit
    pre-registered policy. DCT always uses the frozen production cosine mapping.
    Ground truth is intentionally absent from this interface.
    """

    if backend not in {"DINO_RAW", "DCT"}:
        raise ValueError(f"unsupported adapter backend: {backend}")
    if backend == "DINO_RAW" and policy is None:
        raise ValueError("DINO_RAW requires an adapter policy")
    if backend == "DCT" and policy is not None:
        raise ValueError("DCT control does not accept a DINO adapter policy")

    adapted: list[CandidateMatch] = []
    audit: list[dict[str, Any]] = []
    count = retained = 0
    for record in _iter_candidate_records(sorted(frames, key=lambda f: float(f["accused_time"]))):
        count += 1
        raw_cosine = float(record["score"])
        adapted_score = (
            adapt_dct_score(raw_cosine)
            if backend == "DCT"
            else adapt_dino_score(raw_cosine, policy)  # type: ignore[arg-type]
        )
        is_retained = adapted_score is not None
        if is_retained:
            retained += 1
            adapted.append(
                CandidateMatch(
                    accused_time=float(record["accused_time"]),
                    source_episode=str(record["source_id"]),
                    source_time=float(record["source_time"]),
                    visual_score=float(adapted_score),
                )
            )
        audit.append(
            {
                "accused_time": float(record["accused_time"]),
                "source_time": float(record["source_time"]),
                "source_id": str(record["source_id"]),
                "raw_cosine": raw_cosine,
                "rank": int(record["rank"]),
                "adapted_visual_score": (
                    float(adapted_score) if adapted_score is not None else None
                ),
                "retained": is_retained,
            }
        )
    return AdaptedCandidateStream(
        candidates=tuple(adapted),
        audit=tuple(audit),
        input_candidate_count=count,
        retained_candidate_count=retained,
    )


def policy_metadata(policy: AdapterPolicy) -> dict[str, Any]:
    return {
        "name": policy.name,
        "floor": policy.floor,
        "mapping": (
            "clip(raw_cosine, 0, 1)"
            if policy.floor is None
            else "exclude below floor; clip((raw_cosine-floor)/(1-floor), 0, 1)"
        ),
        "dino_batch_size": DINO_BATCH_SIZE,
    }
