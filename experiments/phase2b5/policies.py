"""Pre-registered Phase 2B-5 proposal-selection policies.

This module has no ground-truth or evaluator dependency.  It operates only on
already-fitted proposal geometry and inherited explanation quality.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Sequence


LINE_BAND = "LINE_BAND"
INLIER_ONLY = "INLIER_ONLY"


@dataclass(frozen=True)
class ExtractionPolicy:
    policy_id: str
    epsilon: float | None
    occupancy_mode: str

    @property
    def uses_near_tie(self) -> bool:
        return self.epsilon is not None


POLICIES: tuple[ExtractionPolicy, ...] = (
    ExtractionPolicy("P0", None, LINE_BAND),
    ExtractionPolicy("P1", 0.0005, LINE_BAND),
    ExtractionPolicy("P2", 0.0010, LINE_BAND),
    ExtractionPolicy("P3", 0.0020, LINE_BAND),
    ExtractionPolicy("P4", None, INLIER_ONLY),
    ExtractionPolicy("P5", 0.0010, INLIER_ONLY),
    ExtractionPolicy("P6", 0.0020, INLIER_ONLY),
)
POLICY_BY_ID = {item.policy_id: item for item in POLICIES}
SIMPLICITY_ORDER = {name: ordinal for ordinal, name in enumerate(("P1", "P2", "P3", "P4", "P5", "P6"))}


def near_tie_ordinals(qualities: Sequence[float], epsilon: float) -> tuple[int, ...]:
    """Return the inclusive q_max-relative shortlist in generation order."""

    if epsilon <= 0:
        raise ValueError("epsilon must be positive")
    if not qualities:
        raise ValueError("at least one quality is required")
    q_max = max(qualities)
    return tuple(
        ordinal for ordinal, value in enumerate(qualities)
        if value >= q_max - epsilon
    )


def geometry_key(item: Any, quality: float, ordinal: int) -> tuple[float, ...]:
    segment = item.segment
    return (
        float(segment.inlier_ratio),
        -float(segment.residual_rms_seconds),
        float(segment.accused_end - segment.accused_start),
        float(segment.inlier_count),
        float(quality),
        float(-ordinal),
    )


def choose_fit(
    fits: Sequence[Any],
    policy: ExtractionPolicy,
    explanation_quality: Callable[[Any], float],
    baseline_quality: Callable[[Any], tuple[float, ...]],
) -> tuple[int, Any, dict[str, Any]]:
    """Select one fit and return GT-free selection diagnostics."""

    if not fits:
        raise ValueError("at least one fit is required")
    qualities = [float(explanation_quality(item.segment)) for item in fits]
    q_max = max(qualities)
    if not policy.uses_near_tie:
        ordinal, selected = max(
            enumerate(fits), key=lambda pair: (baseline_quality(pair[1]), -pair[0])
        )
        shortlist = (ordinal,)
        selected_key: tuple[float, ...] = tuple(float(value) for value in baseline_quality(selected))
    else:
        assert policy.epsilon is not None
        shortlist = near_tie_ordinals(qualities, policy.epsilon)
        ordinal = max(
            shortlist,
            key=lambda index: geometry_key(fits[index], qualities[index], index),
        )
        selected = fits[ordinal]
        selected_key = geometry_key(selected, qualities[ordinal], ordinal)
    return ordinal, selected, {
        "q_max": q_max,
        "shortlist_ordinals": list(shortlist),
        "selected_ordinal": ordinal,
        "selected_quality": qualities[ordinal],
        "selected_key": list(selected_key),
    }
