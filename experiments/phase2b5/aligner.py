"""Minimal experimental extraction seam over the frozen TemporalAligner."""

from __future__ import annotations

import logging
from typing import Any

from ipvm.matching import TemporalAligner, _FittedSegment, _Proposal
from ipvm.models import CandidateMatch

from .policies import ExtractionPolicy, INLIER_ONLY, choose_fit, geometry_key


logger = logging.getLogger(__name__)


class ExperimentalExtractionAligner(TemporalAligner):
    """Change only fit selection and occupancy inside episode extraction."""

    def __init__(
        self,
        config: Any,
        policy: ExtractionPolicy,
        candidates: list[CandidateMatch],
    ) -> None:
        super().__init__(config)
        self.policy = policy
        self.round_trace: list[dict[str, Any]] = []
        self.proposal_trace: list[dict[str, Any]] = []
        self._trace_round_context: tuple[str, int] | None = None
        self._candidate_ordinal = {
            id(item): ordinal for ordinal, item in enumerate(candidates)
        }

    def _ordinals(self, matches: list[CandidateMatch]) -> list[int]:
        return [self._candidate_ordinal[id(item)] for item in matches]

    def _generate_round_fits(
        self, episode: str, remaining: list[CandidateMatch]
    ) -> tuple[int, list[_FittedSegment]]:
        peaks = self._sparse_peaks(episode, remaining)
        fits: list[_FittedSegment] = []
        for peak in peaks:
            support = self._support_for_peak(peak, remaining)
            for run in self._continuous_runs(support):
                duration = run[-1].accused_time - run[0].accused_time + self.frame_interval
                if duration + 1e-6 < self.config.minimum_segment_seconds:
                    continue
                proposal = _Proposal(episode, run, peak.vote)
                fitted = self._proposal_to_segment(proposal, peak.slope, peak.offset)
                if fitted is not None:
                    fits.append(fitted)
        return len(peaks), fits

    def _proposal_to_segment(
        self,
        proposal: _Proposal,
        initial_slope: float | None = None,
        initial_intercept: float | None = None,
    ) -> _FittedSegment | None:
        result = super()._proposal_to_segment(
            proposal, initial_slope, initial_intercept
        )
        self.proposal_trace.append({
            "episode": proposal.episode,
            "round_context": list(self._trace_round_context) if self._trace_round_context else None,
            "input_candidate_ordinals": self._ordinals(proposal.matches),
            "mapping": result.segment.to_dict() if result is not None else None,
            "inlier_ordinals": self._ordinals(result.inliers) if result is not None else [],
        })
        return result

    def _line_band_occupancy(
        self, remaining: list[CandidateMatch], selected: _FittedSegment
    ) -> tuple[list[CandidateMatch], list[int]]:
        claimed_frames = {
            round(item.accused_time / self.frame_interval) for item in selected.inliers
        }
        slope = selected.segment.speed_ratio
        if slope is None:
            fit = self._robust_fit(selected.inliers)
            slope = fit.slope
            intercept = fit.intercept
        else:
            intercept = float(selected.segment.fitted_offset_seconds or 0.0)
        tolerance = self.candidate_timestamp_tolerance_seconds + 1e-6
        kept: list[CandidateMatch] = []
        removed: list[int] = []
        for item in remaining:
            occupied = (
                round(item.accused_time / self.frame_interval) in claimed_frames
                and abs(item.source_time - (slope * item.accused_time + intercept))
                <= tolerance
            )
            if occupied:
                removed.append(self._candidate_ordinal[id(item)])
            else:
                kept.append(item)
        return kept, removed

    def _inlier_occupancy(
        self, remaining: list[CandidateMatch], selected: _FittedSegment
    ) -> tuple[list[CandidateMatch], list[int]]:
        selected_objects = {id(item) for item in selected.inliers}
        kept: list[CandidateMatch] = []
        removed: list[int] = []
        for item in remaining:
            if id(item) in selected_objects:
                removed.append(self._candidate_ordinal[id(item)])
            else:
                kept.append(item)
        return kept, removed

    def _fit_record(self, ordinal: int, fitted: _FittedSegment) -> dict[str, Any]:
        quality = self._explanation_quality(fitted.segment)
        return {
            "ordinal": ordinal,
            "mapping": fitted.segment.to_dict(),
            "inlier_ordinals": self._ordinals(fitted.inliers),
            "baseline_key": list(self._fit_quality(fitted)),
            "geometry_key": list(geometry_key(fitted, quality, ordinal)),
            "explanation_quality": quality,
        }

    def _extract_episode_segments(
        self, episode: str, matches: list[CandidateMatch]
    ) -> tuple[list[_FittedSegment], bool]:
        """Frozen extraction with only the two registered decision seams changed."""

        remaining = list(matches)
        extracted: list[_FittedSegment] = []
        truncated = True
        for round_index in range(self.config.maximum_alignment_segments_per_episode):
            self._trace_round_context = (episode, round_index)
            peak_count, fits = self._generate_round_fits(episode, remaining)
            if not peak_count:
                truncated = False
                break
            if not fits:
                truncated = False
                break
            selected_ordinal, selected, selection = choose_fit(
                fits,
                self.policy,
                self._explanation_quality,
                self._fit_quality,
            )
            before_ordinals = self._ordinals(remaining)
            if self.policy.occupancy_mode == INLIER_ONLY:
                after, removed = self._inlier_occupancy(remaining, selected)
            else:
                after, removed = self._line_band_occupancy(remaining, selected)
            self.round_trace.append({
                "episode": episode,
                "round_index": round_index,
                "input_candidate_ordinals": before_ordinals,
                "peak_count": peak_count,
                "fits": [self._fit_record(index, item) for index, item in enumerate(fits)],
                "selection": selection,
                "selected_fit": self._fit_record(selected_ordinal, selected),
                "removed_candidate_ordinals": removed,
                "remaining_candidate_ordinals": self._ordinals(after),
            })
            extracted.append(selected)
            remaining = after
        self._trace_round_context = None
        if truncated:
            logger.warning(
                "Experimental alignment segment cap reached for %s at %d segments",
                episode,
                self.config.maximum_alignment_segments_per_episode,
            )
        return self._refine_adjacent_change_points(extracted, matches), truncated
