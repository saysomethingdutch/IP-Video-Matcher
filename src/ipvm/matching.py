from __future__ import annotations

import logging
import math
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np

from .config import AppConfig, MatchingConfig
from .database import Database
from .features import DCTVisualEncoder, ImageEncoder
from .media import MediaToolError, extract_grayscale_frames, probe_media, sha256_file
from .models import CandidateMatch, MatchStatus, MediaMetadata, SegmentMatch


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AnalysisResult:
    case_id: str
    accused_path: Path
    status: str
    segments: list[SegmentMatch]
    alternatives: list[SegmentMatch]
    elapsed_seconds: float
    frame_count: int
    accused_sha256: str | None
    media_metadata: MediaMetadata | None
    index_health: dict[str, Any]
    matching_config: dict[str, Any]
    encoder: str
    top_competing_candidates: list[dict[str, Any]]
    conflict_graph: list[dict[str, Any]]
    review_tier: str
    reason: str | None = None


@dataclass(frozen=True)
class AlignmentOutcome:
    segments: list[SegmentMatch]
    alternatives: list[SegmentMatch]
    competing_candidates: list[dict[str, Any]]
    conflict_graph: list[dict[str, Any]]
    ambiguous: bool
    contradiction: bool
    reason: str | None
    truncated_episodes: tuple[str, ...] = ()


class CandidateRetriever:
    def __init__(self, database: Database, project_id: int, config: MatchingConfig):
        self.database = database
        self.project_id = project_id
        self.config = config

    def retrieve(self, query_times: np.ndarray, query_features: np.ndarray) -> list[CandidateMatch]:
        candidates: list[list[CandidateMatch]] = [[] for _ in range(len(query_times))]
        for row in self.database.list_original_indexes(self.project_id):
            index_path = Path(row["index_path"])
            if not index_path.exists():
                raise FileNotFoundError(f"Index missing for {row['episode']}: {index_path}")
            with np.load(index_path) as source:
                source_times = source["timestamps"].astype(np.float32)
                source_features = source["features"].astype(np.float32)
            if len(source_features) == 0:
                continue
            similarities = query_features @ source_features.T
            count = min(self.config.top_k, source_features.shape[0])
            local_indices = np.argpartition(similarities, -count, axis=1)[:, -count:]
            for query_index, source_indices in enumerate(local_indices):
                for source_index in source_indices:
                    score = float(similarities[query_index, source_index])
                    candidates[query_index].append(CandidateMatch(
                        accused_time=float(query_times[query_index]),
                        source_episode=str(row["episode"]),
                        source_time=float(source_times[source_index]),
                        visual_score=max(0.0, min(1.0, (score + 1.0) / 2.0)),
                    ))
        flattened: list[CandidateMatch] = []
        for per_frame in candidates:
            per_frame.sort(key=lambda item: item.visual_score, reverse=True)
            flattened.extend(per_frame[: self.config.top_k])
        return flattened


@dataclass
class _Proposal:
    episode: str
    matches: list[CandidateMatch]
    score: float


@dataclass
class _FittedSegment:
    segment: SegmentMatch
    inliers: list[CandidateMatch]


@dataclass(frozen=True)
class _HypothesisPeak:
    episode: str
    slope: float
    offset: float
    vote: float
    support_frames: int


@dataclass(frozen=True)
class _FitResult:
    slope: float
    intercept: float
    inliers: np.ndarray
    residual_rms: float
    residual_max: float
    unconstrained_slope: float | None = None


def _interval_iou(left: SegmentMatch, right: SegmentMatch) -> float:
    overlap = max(0.0, min(left.accused_end, right.accused_end) - max(left.accused_start, right.accused_start))
    union = max(left.accused_end, right.accused_end) - min(left.accused_start, right.accused_start)
    return overlap / union if union > 0 else 0.0


class TemporalAligner:
    """Sparse slope-offset voting and robust piecewise temporal alignment.

    Voting is episode-local and sparse: only cells touched by a candidate are
    materialized.  The slope axis has a small bounded number of configured bins,
    so construction is O(candidate_count * slope_bin_count), not O(N**2) and not
    a dense episode x slope x offset tensor.
    """

    def __init__(self, config: MatchingConfig):
        self.config = config
        if config.coarse_fps <= 0:
            raise ValueError("coarse_fps must be positive")
        self.frame_interval = 1.0 / config.coarse_fps
        if config.slope_bin_size <= 0:
            raise ValueError("slope_bin_size must be positive")
        if config.minimum_speed_ratio <= 0 or config.maximum_speed_ratio < config.minimum_speed_ratio:
            raise ValueError("invalid temporal speed range")
        if config.candidate_timestamp_uncertainty_grid_steps < 0:
            raise ValueError("candidate timestamp uncertainty cannot be negative")
        self.offset_bin_seconds = self.frame_interval
        self.candidate_timestamp_tolerance_seconds = (
            config.candidate_timestamp_uncertainty_grid_steps * self.frame_interval
        )
        slope_count = int(round(
            (config.maximum_speed_ratio - config.minimum_speed_ratio)
            / config.slope_bin_size
        ))
        self.slope_values = np.linspace(
            config.minimum_speed_ratio,
            config.maximum_speed_ratio,
            slope_count + 1,
            dtype=np.float64,
        )

    def _sparse_peaks(
        self, episode: str, matches: list[CandidateMatch]
    ) -> list[_HypothesisPeak]:
        """Build episode-local slope/offset cells with offset soft voting.

        A query frame contributes at most once to a cell even when several of
        its top-k candidates land there. Offset votes are linearly shared by the
        two neighboring bin centers, preventing a 0.499/0.501 boundary split.
        """
        by_frame: dict[int, list[CandidateMatch]] = defaultdict(list)
        for match in matches:
            frame_id = round(match.accused_time / self.frame_interval)
            by_frame[frame_id].append(match)
        votes: dict[tuple[int, int], float] = defaultdict(float)
        support: dict[tuple[int, int], int] = defaultdict(int)
        for frame_matches in by_frame.values():
            frame_votes: dict[tuple[int, int], float] = {}
            for match in frame_matches:
                for slope_index, slope in enumerate(self.slope_values):
                    offset_coordinate = (
                        match.source_time - float(slope) * match.accused_time
                    ) / self.offset_bin_seconds
                    lower = math.floor(offset_coordinate)
                    fraction = offset_coordinate - lower
                    for offset_bin, soft_weight in (
                        (lower, 1.0 - fraction), (lower + 1, fraction)
                    ):
                        if soft_weight <= 1e-12:
                            continue
                        key = (slope_index, offset_bin)
                        contribution = match.visual_score * soft_weight
                        if contribution > frame_votes.get(key, 0.0):
                            frame_votes[key] = contribution
            for key, contribution in frame_votes.items():
                votes[key] += contribution
                support[key] += 1
        minimum_frames = max(
            2, int(math.ceil(self.config.minimum_segment_seconds * self.config.coarse_fps))
        )
        ranked = sorted(
            (
                (vote, support[key], key)
                for key, vote in votes.items()
                if support[key] >= minimum_frames
            ),
            key=lambda item: (item[0], item[1], -item[2][0], -item[2][1]),
            reverse=True,
        )
        peaks: list[_HypothesisPeak] = []
        accepted_cells: list[tuple[int, int]] = []
        for vote, support_frames, (slope_index, offset_bin) in ranked:
            if any(
                abs(slope_index - other_slope) <= 1
                and abs(offset_bin - other_offset) <= 1
                for other_slope, other_offset in accepted_cells
            ):
                continue
            accepted_cells.append((slope_index, offset_bin))
            peaks.append(_HypothesisPeak(
                episode=episode,
                slope=float(self.slope_values[slope_index]),
                offset=offset_bin * self.offset_bin_seconds,
                vote=float(vote),
                support_frames=support_frames,
            ))
            if len(peaks) >= self.config.maximum_hypothesis_peaks_per_episode:
                break
        return peaks

    def _support_for_peak(
        self, peak: _HypothesisPeak, matches: list[CandidateMatch]
    ) -> list[CandidateMatch]:
        best_by_frame: dict[int, tuple[float, CandidateMatch]] = {}
        tolerance = self.candidate_timestamp_tolerance_seconds + 1e-6
        for match in matches:
            residual = abs(
                match.source_time - (peak.slope * match.accused_time + peak.offset)
            )
            if residual > tolerance:
                continue
            frame_id = round(match.accused_time / self.frame_interval)
            current = best_by_frame.get(frame_id)
            quality = residual - match.visual_score * 1e-6
            if current is None or quality < current[0]:
                best_by_frame[frame_id] = (quality, match)
        return sorted(
            (value[1] for value in best_by_frame.values()),
            key=lambda item: item.accused_time,
        )

    def _continuous_runs(self, matches: list[CandidateMatch]) -> list[list[CandidateMatch]]:
        runs: list[list[CandidateMatch]] = []
        current: list[CandidateMatch] = []
        for match in sorted(matches, key=lambda item: item.accused_time):
            if current and (
                match.accused_time - current[-1].accused_time
                > self.config.maximum_frame_gap_seconds
            ):
                runs.append(current)
                current = []
            current.append(match)
        if current:
            runs.append(current)
        return runs

    def _robust_fit(
        self,
        matches: list[CandidateMatch],
        initial_slope: float | None = None,
        initial_intercept: float | None = None,
    ) -> _FitResult:
        accused = np.asarray([item.accused_time for item in matches], dtype=np.float64)
        source = np.asarray([item.source_time for item in matches], dtype=np.float64)
        if len(matches) < 2 or np.ptp(accused) < 1e-9:
            slope = initial_slope if initial_slope is not None else 1.0
            intercept = float(np.median(source - slope * accused))
            return _FitResult(
                slope, intercept, np.ones(len(matches), dtype=bool), 0.0, 0.0, None
            )
        hypotheses: list[tuple[float, float]] = []
        if initial_slope is not None:
            hypotheses.append((
                initial_slope,
                initial_intercept if initial_intercept is not None
                else float(np.median(source - initial_slope * accused)),
            ))
        # Deterministic bounded pair sampling supplies robust local hypotheses.
        sample_count = min(32, len(matches))
        sample_indices = sorted(set(
            np.linspace(0, len(matches) - 1, sample_count, dtype=int).tolist()
        ))
        minimum_pair_delta = max(1.0, self.frame_interval * 2.0)
        for left_position, left in enumerate(sample_indices):
            for right in sample_indices[left_position + 1:]:
                delta = accused[right] - accused[left]
                if delta + 1e-9 < minimum_pair_delta:
                    continue
                slope = (source[right] - source[left]) / delta
                if not (
                    self.config.minimum_speed_ratio - 1e-9
                    <= slope
                    <= self.config.maximum_speed_ratio + 1e-9
                ):
                    continue
                hypotheses.append((slope, float(np.median(source - slope * accused))))
        if not hypotheses:
            slope = float(np.clip(
                initial_slope if initial_slope is not None else 1.0,
                self.config.minimum_speed_ratio,
                self.config.maximum_speed_ratio,
            ))
            hypotheses.append((slope, float(np.median(source - slope * accused))))
        best_inliers = np.zeros(len(matches), dtype=bool)
        best_key: tuple[int, float, float] = (-1, -1.0, float("-inf"))
        tolerance = self.candidate_timestamp_tolerance_seconds + 1e-6
        for slope, intercept in hypotheses:
            residuals = np.abs(source - (slope * accused + intercept))
            inliers = residuals <= tolerance
            if not inliers.any():
                continue
            span = float(np.ptp(accused[inliers])) if inliers.sum() > 1 else 0.0
            rms = float(np.sqrt(np.mean(np.square(residuals[inliers]))))
            key = (int(inliers.sum()), span, -rms)
            if key > best_key:
                best_inliers = inliers
                best_key = key
        if best_inliers.sum() < 2:
            best_inliers[:] = True
        unconstrained_slope: float | None = None
        for _ in range(3):
            design = np.column_stack([accused[best_inliers], np.ones(best_inliers.sum())])
            slope, intercept = np.linalg.lstsq(
                design, source[best_inliers], rcond=None
            )[0]
            # Keep the unclipped estimate: Phase 2A.1 forbids reporting a clamped
            # endpoint as a measured speed.
            unconstrained_slope = float(slope)
            slope = float(np.clip(
                slope,
                self.config.minimum_speed_ratio,
                self.config.maximum_speed_ratio,
            ))
            intercept = float(np.median(source[best_inliers] - slope * accused[best_inliers]))
            residuals = np.abs(source - (slope * accused + intercept))
            refined = residuals <= tolerance
            if refined.sum() < 2 or np.array_equal(refined, best_inliers):
                break
            best_inliers = refined
        residuals = np.abs(source - (slope * accused + intercept))
        inlier_residuals = residuals[best_inliers]
        return _FitResult(
            float(slope), float(intercept), best_inliers,
            float(np.sqrt(np.mean(np.square(inlier_residuals)))),
            float(np.max(inlier_residuals)),
            unconstrained_slope,
        )

    def _proposal_to_segment(
        self,
        proposal: _Proposal,
        initial_slope: float | None = None,
        initial_intercept: float | None = None,
    ) -> _FittedSegment | None:
        fit = self._robust_fit(proposal.matches, initial_slope, initial_intercept)
        inlier_matches = [
            match for match, keep in zip(proposal.matches, fit.inliers, strict=True) if keep
        ]
        if not inlier_matches:
            return None
        start = min(item.accused_time for item in inlier_matches)
        end = max(item.accused_time for item in inlier_matches) + self.frame_interval
        if end - start + 1e-6 < self.config.minimum_segment_seconds:
            return None
        # Phase 2A.1 item 10: an emitted segment needs the same absolute support a
        # hypothesis peak needs. A sparse low-support tail must not look authoritative.
        minimum_support = max(
            2,
            int(math.ceil(
                self.config.minimum_segment_seconds * self.config.coarse_fps
            )),
        )
        if len(inlier_matches) < minimum_support:
            return None
        visual = float(np.mean([item.visual_score for item in inlier_matches]))
        coverage = min(1.0, len(inlier_matches) / max(1.0, (end - start) * self.config.coarse_fps))
        temporal = min(1.0, len(inlier_matches) / len(proposal.matches))
        accused = np.asarray([item.accused_time for item in inlier_matches])
        source = np.asarray([item.source_time for item in inlier_matches])
        match_score = max(0.0, min(1.0, visual * (0.7 + 0.3 * coverage) * temporal))
        accused_span = float(np.ptp(accused)) if len(accused) > 1 else 0.0
        source_span = float(np.ptp(source)) if len(source) > 1 else 0.0
        span_supports_speed = min(accused_span, source_span) >= 3.0
        unconstrained = fit.unconstrained_slope
        # Phase 2A.1: a fitted slope outside the configured range is never reported as a
        # measured speed. The clipped endpoint is a fitting artefact, not evidence.
        # A source-grid-quantized fit overshoots the configured endpoints by well under
        # one slope bin; only an excursion the slope grid can actually resolve counts as
        # out of range.
        slope_tolerance = self.config.slope_bin_size
        speed_out_of_range = bool(
            unconstrained is not None
            and not (
                self.config.minimum_speed_ratio - slope_tolerance
                <= unconstrained
                <= self.config.maximum_speed_ratio + slope_tolerance
            )
        )
        speed_ratio = (
            round(fit.slope, 4)
            if span_supports_speed and not speed_out_of_range
            else None
        )
        segment = SegmentMatch(
            accused_start=round(start, 3),
            accused_end=round(end, 3),
            source_episode=proposal.episode,
            source_start=round(max(0.0, fit.slope * start + fit.intercept), 3),
            source_end=round(max(0.0, fit.slope * end + fit.intercept), 3),
            speed_ratio=speed_ratio,
            speed_out_of_range=speed_out_of_range,
            unconstrained_speed_ratio=(
                round(unconstrained, 4) if unconstrained is not None else None
            ),
            visual_score=round(visual, 4),
            audio_score=None,
            temporal_score=round(temporal, 4),
            match_score=round(match_score, 4),
            temporal_residual_seconds=round(fit.residual_max, 4),
            fitted_offset_seconds=round(fit.intercept, 4),
            residual_rms_seconds=round(fit.residual_rms, 4),
            residual_max_seconds=round(fit.residual_max, 4),
            inlier_count=len(inlier_matches),
            candidate_count=len(proposal.matches),
            inlier_ratio=round(len(inlier_matches) / len(proposal.matches), 4),
            coverage=round(coverage, 4),
            timestamp_basis="nominal_grid_time",
            algorithm_version=self.config.temporal_algorithm_version,
        )
        return _FittedSegment(segment, inlier_matches)

    def _explanation_quality(self, segment: SegmentMatch) -> float:
        """Normalized quality of a temporal explanation.

        Visual agreement scaled by BOUNDED explained evidence. Evidence is required so
        that a short fragment supported by a handful of high-scoring outliers cannot
        outrank the longer line that actually explains the timeline; the bound is what
        stops a long weak line from overwhelming a materially better short one, since
        support saturates at twice the minimum segment length.

        Neither duration nor coverage appears directly: coverage would penalize a
        segment for the retrieval holes `maximum_frame_gap_seconds` exists to bridge,
        and inlier_ratio is measured against the run, so a short pure fragment would
        maximize it trivially. Both remain GREEN diagnostics and ranking tiebreaks.
        """
        saturation = max(
            1.0,
            2.0 * self.config.minimum_segment_seconds * self.config.coarse_fps,
        )
        support = min(1.0, segment.inlier_count / saturation)
        return float(segment.visual_score * support)

    def _fit_quality(
        self, fitted: _FittedSegment
    ) -> tuple[float, int, float, float, float]:
        segment = fitted.segment
        return (
            self._explanation_quality(segment),
            segment.inlier_count,
            segment.inlier_ratio,
            -segment.residual_rms_seconds,
            segment.accused_end - segment.accused_start,
        )

    def _extract_episode_segments(
        self, episode: str, matches: list[CandidateMatch]
    ) -> tuple[list[_FittedSegment], bool]:
        """Returns (segments, truncated). `truncated` means the per-episode cap was hit."""
        remaining = list(matches)
        extracted: list[_FittedSegment] = []
        truncated = True
        for _ in range(self.config.maximum_alignment_segments_per_episode):
            peaks = self._sparse_peaks(episode, remaining)
            if not peaks:
                truncated = False
                break
            fits: list[_FittedSegment] = []
            for peak in peaks:
                support = self._support_for_peak(peak, remaining)
                for run in self._continuous_runs(support):
                    duration = run[-1].accused_time - run[0].accused_time + self.frame_interval
                    if duration + 1e-6 < self.config.minimum_segment_seconds:
                        continue
                    proposal = _Proposal(episode, run, peak.vote)
                    fitted = self._proposal_to_segment(
                        proposal, peak.slope, peak.offset
                    )
                    if fitted is not None:
                        fits.append(fitted)
            if not fits:
                truncated = False
                break
            best = max(fits, key=self._fit_quality)
            extracted.append(best)
            claimed_frames = {
                round(match.accused_time / self.frame_interval) for match in best.inliers
            }
            slope = best.segment.speed_ratio
            if slope is None:
                fit = self._robust_fit(best.inliers)
                slope = fit.slope
                intercept = fit.intercept
            else:
                intercept = float(best.segment.fitted_offset_seconds or 0.0)
            tolerance = self.candidate_timestamp_tolerance_seconds + 1e-6
            remaining = [
                match for match in remaining
                if not (
                    round(match.accused_time / self.frame_interval) in claimed_frames
                    and abs(match.source_time - (slope * match.accused_time + intercept))
                    <= tolerance
                )
            ]
        if truncated:
            logger.warning(
                "Alignment segment cap reached for %s: results are truncated at %d segments",
                episode, self.config.maximum_alignment_segments_per_episode,
            )
        return extracted, truncated

    def align(self, candidates: list[CandidateMatch]) -> list[SegmentMatch]:
        return self.align_with_diagnostics(candidates).segments

    def align_with_diagnostics(
        self, candidates: list[CandidateMatch], index_complete: bool = False
    ) -> AlignmentOutcome:
        filtered = [
            candidate for candidate in candidates
            if candidate.visual_score >= self.config.minimum_visual_similarity
        ]
        by_episode: dict[str, list[CandidateMatch]] = defaultdict(list)
        for candidate in filtered:
            by_episode[candidate.source_episode].append(candidate)
        scored_segments: list[_FittedSegment] = []
        truncated_episodes: list[str] = []
        for episode, episode_matches in sorted(by_episode.items()):
            episode_segments, truncated = self._extract_episode_segments(
                episode, episode_matches
            )
            scored_segments.extend(episode_segments)
            if truncated:
                truncated_episodes.append(episode)
        scored_segments.sort(key=self._fit_quality, reverse=True)
        claimed_frames: set[int] = set()
        accepted: list[_FittedSegment] = []
        suppressed_rivals: list[SegmentMatch] = []
        for fitted in scored_segments:
            frame_ids = {round(item.accused_time / self.frame_interval) for item in fitted.inliers}
            if frame_ids and len(frame_ids & claimed_frames) / len(frame_ids) > 0.5:
                # Phase 2A.1: a suppressed but still credible rival is retained as an
                # alternative rather than silently deleted.
                rival = fitted.segment
                rival_quality = self._explanation_quality(rival)
                # Only a genuinely INCOMPATIBLE explanation is an ambiguity signal. A
                # rival describing the same episode and source position is a duplicate
                # of the winner, not a competing account of the evidence.
                incompatible = [
                    winner for winner in accepted
                    if (
                        {
                            round(item.accused_time / self.frame_interval)
                            for item in winner.inliers
                        }
                        & frame_ids
                    )
                    and not (
                        winner.segment.source_episode == rival.source_episode
                        and abs(winner.segment.source_start - rival.source_start) <= 1.0
                    )
                ]
                dominant = max(
                    (self._explanation_quality(winner.segment) for winner in incompatible),
                    default=None,
                )
                if (
                    dominant is not None
                    and dominant - rival_quality < self.config.minimum_uniqueness_margin
                ):
                    suppressed_rivals.append(rival)
                continue
            accepted.append(fitted)
            claimed_frames.update(frame_ids)
        accepted.sort(key=lambda item: item.segment.accused_start)
        accepted = self._merge_adjacent(accepted)

        ambiguous = False
        alternatives: list[SegmentMatch] = []
        competing: list[dict[str, Any]] = []
        assessed: list[SegmentMatch] = []
        for winner_index, winner_fitted in enumerate(accepted, start=1):
            winner = replace(winner_fitted.segment, segment_id=f"S{winner_index:03d}")
            rivals = [
                fitted.segment for fitted in scored_segments
                if _interval_iou(winner, fitted.segment) >= 0.8
                and not (
                    fitted.segment.source_episode == winner.source_episode
                    and abs(fitted.segment.source_start - winner.source_start) <= 1.0
                )
            ]
            rivals.sort(key=lambda item: item.match_score, reverse=True)
            runner_up = rivals[0] if rivals else None
            margin = winner.match_score - runner_up.match_score if runner_up else 1.0
            major_rivals = [
                rival for rival in rivals
                if rival.source_episode != winner.source_episode
                and winner.match_score - rival.match_score
                < self.config.minimum_uniqueness_margin
            ]
            is_ambiguous = bool(major_rivals)
            candidate_tier = self._candidate_tier(
                winner, margin, is_ambiguous, index_complete
            )
            winner = replace(
                winner,
                uniqueness_margin=round(margin, 4),
                candidate_tier=candidate_tier,
                review_tier="REVIEW_REQUIRED",
            )
            assessed.append(winner)
            audited_rivals = list(major_rivals)
            for rival in rivals:
                if rival not in audited_rivals and len(audited_rivals) < self.config.audit_top_candidates:
                    audited_rivals.append(rival)
            for rival in audited_rivals:
                competing.append({
                    "winner": winner.to_dict(),
                    "candidate": rival.to_dict(),
                    "score_margin": round(winner.match_score - rival.match_score, 4),
                    "score_ratio": round(rival.match_score / max(winner.match_score, 1e-9), 4),
                })
            if is_ambiguous:
                ambiguous = True
                alternatives.extend([
                    replace(rival, candidate_tier="YELLOW", review_tier="REVIEW_REQUIRED")
                    for rival in major_rivals
                ])

        if suppressed_rivals:
            ambiguous = True
            alternatives.extend(
                replace(rival, candidate_tier="YELLOW", review_tier="REVIEW_REQUIRED")
                for rival in suppressed_rivals
            )
        assessed = self._mark_source_reuse(assessed)
        conflict_graph = self._conflict_graph(assessed)
        contradiction = bool(conflict_graph)
        if contradiction:
            adjacency: dict[str, list[str]] = defaultdict(list)
            reasons: dict[str, list[str]] = defaultdict(list)
            for edge in conflict_graph:
                adjacency[edge["left_segment_id"]].append(edge["right_segment_id"])
                adjacency[edge["right_segment_id"]].append(edge["left_segment_id"])
                reasons[edge["left_segment_id"]].append(edge["reason"])
                reasons[edge["right_segment_id"]].append(edge["reason"])
            assessed = [
                replace(
                    segment,
                    conflict=bool(adjacency.get(segment.segment_id or "")),
                    conflict_with=tuple(sorted(adjacency.get(segment.segment_id or "", []))),
                    conflict_reason=(
                        "; ".join(sorted(set(reasons.get(segment.segment_id or "", []))))
                        or None
                    ),
                    candidate_tier=(
                        "YELLOW" if adjacency.get(segment.segment_id or "")
                        else segment.candidate_tier
                    ),
                    review_tier="REVIEW_REQUIRED",
                )
                for segment in assessed
            ]
        reason = None
        reasons_out: list[str] = []
        if ambiguous:
            reasons_out.append(
                "Competing source explanations remain within the uniqueness margin"
            )
        if truncated_episodes:
            reasons_out.append(
                "Alignment segment cap reached for: " + ", ".join(sorted(truncated_episodes))
            )
        if contradiction:
            conflict_ids = sorted({
                item for edge in conflict_graph
                for item in (edge["left_segment_id"], edge["right_segment_id"])
            })
            reasons_out.append(f"Segment contradiction detected: {', '.join(conflict_ids)}")
        reason = "; ".join(reasons_out) or None
        unique_alternatives: dict[tuple[Any, ...], SegmentMatch] = {}
        for segment in alternatives:
            key = (
                segment.accused_start, segment.accused_end, segment.source_episode,
                segment.source_start, segment.source_end,
            )
            unique_alternatives[key] = segment
        return AlignmentOutcome(
            segments=assessed,
            alternatives=list(unique_alternatives.values()),
            competing_candidates=competing,
            conflict_graph=conflict_graph,
            ambiguous=ambiguous,
            contradiction=contradiction,
            reason=reason,
            truncated_episodes=tuple(sorted(truncated_episodes)),
        )

    def _candidate_tier(
        self, segment: SegmentMatch, margin: float, ambiguous: bool, index_complete: bool
    ) -> str:
        """Diagnostic tier only. Production review_tier is always REVIEW_REQUIRED.

        Phase 2A.1 replaces the tautological `residual_max <= 0.55` condition: inliers
        are defined by residual <= 0.5, so that test could never fail. The conditions
        below use residual RMS, inlier ratio and absolute support, which vary.
        """
        if ambiguous:
            return "YELLOW"
        green = (
            index_complete
            and margin >= self.config.minimum_uniqueness_margin
            and segment.residual_rms_seconds <= self.config.maximum_green_residual_rms_seconds
            and segment.inlier_ratio >= self.config.minimum_green_inlier_ratio
            and segment.inlier_count >= self.config.minimum_green_inlier_count
            and not segment.speed_out_of_range
            and not segment.source_reuse
            and segment.coverage >= self.config.minimum_coverage
            and segment.temporal_score >= self.config.minimum_temporal_consistency
        )
        return "GREEN" if green else "YELLOW"

    @staticmethod
    def _conflict_graph(segments: list[SegmentMatch]) -> list[dict[str, Any]]:
        graph: list[dict[str, Any]] = []
        for index, left in enumerate(segments):
            for right in segments[index + 1:]:
                accused_overlap = max(
                    0.0, min(left.accused_end, right.accused_end)
                    - max(left.accused_start, right.accused_start)
                )
                if accused_overlap > 0.25 and (
                    left.source_episode != right.source_episode
                    or abs(left.source_start - right.source_start) > 1.0
                ):
                    graph.append({
                        "left_segment_id": left.segment_id,
                        "right_segment_id": right.segment_id,
                        "reason": "overlapping accused intervals map to incompatible sources",
                    })
                    continue
        return graph

    @staticmethod
    def _mark_source_reuse(segments: list[SegmentMatch]) -> list[SegmentMatch]:
        reused: set[str] = set()
        for index, left in enumerate(segments):
            for right in segments[index + 1:]:
                accused_overlap = max(
                    0.0, min(left.accused_end, right.accused_end)
                    - max(left.accused_start, right.accused_start)
                )
                source_overlap = max(
                    0.0, min(left.source_end, right.source_end)
                    - max(left.source_start, right.source_start)
                )
                if (
                    left.source_episode == right.source_episode
                    and accused_overlap <= 0.25
                    and source_overlap > 0.5
                ):
                    if left.segment_id:
                        reused.add(left.segment_id)
                    if right.segment_id:
                        reused.add(right.segment_id)
        return [
            replace(
                segment,
                source_reuse=True,
                candidate_tier="YELLOW",
                review_tier="REVIEW_REQUIRED",
            ) if segment.segment_id in reused else segment
            for segment in segments
        ]

    @staticmethod
    def _segment_line(segment: SegmentMatch) -> tuple[float, float] | None:
        """Recover (slope, intercept) of a fitted segment in nominal grid time."""
        span = segment.accused_end - segment.accused_start
        if span <= 1e-9:
            return None
        slope = (segment.source_end - segment.source_start) / span
        intercept = segment.source_start - slope * segment.accused_start
        return slope, intercept

    def _lines_stay_consistent(
        self, left: SegmentMatch, right: SegmentMatch
    ) -> tuple[bool, float]:
        """Do two fitted mappings agree over the proposed union span?

        Two segments only describe one continuous quotation if their mappings stay
        within source-grid temporal uncertainty across the whole union span. A speed
        discontinuity diverges linearly and is rejected here, before any refit.
        """
        left_line = self._segment_line(left)
        right_line = self._segment_line(right)
        if left_line is None or right_line is None:
            return False, float("inf")
        union_start = min(left.accused_start, right.accused_start)
        union_end = max(left.accused_end, right.accused_end)
        divergence = max(
            abs((left_line[0] * t + left_line[1]) - (right_line[0] * t + right_line[1]))
            for t in (union_start, union_end)
        )
        budget = (
            self.config.merge_line_divergence_grid_steps * self.frame_interval + 1e-9
        )
        return divergence <= budget, divergence

    def _merge_adjacent(self, segments: list[_FittedSegment]) -> list[_FittedSegment]:
        if not segments:
            return []
        merged = [segments[0]]
        for segment in segments[1:]:
            previous = merged[-1]
            previous_segment = previous.segment
            current_segment = segment.segment
            adjacent = (
                current_segment.source_episode == previous_segment.source_episode
                and current_segment.accused_start - previous_segment.accused_end
                <= self.config.maximum_frame_gap_seconds
                and abs(current_segment.source_start - previous_segment.source_end) <= 1.5
            )
            consistent, _ = self._lines_stay_consistent(previous_segment, current_segment)
            if not (adjacent and consistent):
                merged.append(segment)
                continue
            combined = previous.inliers + segment.inliers
            proposal = _Proposal(
                previous_segment.source_episode,
                combined,
                previous_segment.match_score + current_segment.match_score,
            )
            refitted = self._proposal_to_segment(proposal)
            # The merged refit must still explain the union; a refit that drops a large
            # share of the combined evidence means the two segments were not one line.
            if refitted is None or (
                refitted.segment.inlier_ratio < self.config.merge_minimum_inlier_ratio
            ):
                merged.append(segment)
            else:
                merged[-1] = refitted
        return merged


class VideoMatcher:
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
        self.retriever = CandidateRetriever(database, self.project_id, config.matching)
        self.aligner = TemporalAligner(config.matching)

    def analyze_file(self, case_id: str, accused_path: Path, persist: bool = True) -> AnalysisResult:
        started = time.perf_counter()
        index_health = self.database.index_health(self.project_id)
        index_health = self._validate_runtime_index(index_health)
        if index_health["status"] != "COMPLETE":
            return self._blocked_result(
                case_id, accused_path, started, MatchStatus.INCOMPLETE_INDEX,
                index_health.get("reason") or "Original index is not complete", index_health, persist,
            )
        try:
            metadata = probe_media(accused_path)
            digest = sha256_file(accused_path)
            extracted = extract_grayscale_frames(
                accused_path,
                fps=self.config.matching.coarse_fps,
                width=self.config.matching.frame_width,
                height=self.config.matching.frame_height,
            )
            query_times = np.asarray([item[0] for item in extracted], dtype=np.float32)
            query_features = self.encoder.encode_images(frame for _, frame in extracted)
            candidates = self.retriever.retrieve(query_times, query_features)
            alignment = self.aligner.align_with_diagnostics(candidates, index_complete=True)
            if alignment.ambiguous or alignment.contradiction:
                status = MatchStatus.AMBIGUOUS
                tier = "REVIEW_REQUIRED"
            elif alignment.segments:
                status = MatchStatus.MATCH_FOUND
                tier = "REVIEW_REQUIRED"
            else:
                status = MatchStatus.MATCH_NOT_FOUND
                tier = "RED"
            result = AnalysisResult(
                case_id, accused_path, status.value, alignment.segments,
                alignment.alternatives, time.perf_counter() - started, len(extracted),
                digest, metadata, index_health, asdict(self.config.matching), self.encoder.name,
                alignment.competing_candidates, alignment.conflict_graph, tier, alignment.reason,
            )
        except (MediaToolError, FileNotFoundError, OSError, ValueError) as exc:
            logger.exception("Analysis failed for %s", case_id)
            result = AnalysisResult(
                case_id, accused_path, MatchStatus.ERROR.value, [], [],
                time.perf_counter() - started, 0, None, None, index_health,
                asdict(self.config.matching), self.encoder.name, [], [], "RED", str(exc),
            )
        if persist:
            self.database.replace_case_result(
                self.project_id, case_id, accused_path, result.accused_sha256,
                result.media_metadata, result.status, result.reason,
                result.segments, result.alternatives,
            )
        logger.info(
            "Analysis %s: %s (%d segments)", case_id, result.status, len(result.segments),
            extra={"case_id": case_id, "duration": result.elapsed_seconds},
        )
        return result

    @staticmethod
    def _validate_runtime_index(index_health: dict[str, Any]) -> dict[str, Any]:
        if index_health.get("status") != "COMPLETE":
            return index_health
        problems: list[str] = []
        for asset in index_health.get("assets", []):
            index_path_value = asset.get("index_path")
            if not index_path_value or not Path(index_path_value).exists():
                problems.append(f"{asset.get('episode')}: index file missing")
                continue
            try:
                with np.load(Path(index_path_value)) as payload:
                    actual = len(payload["timestamps"])
            except (OSError, KeyError, ValueError) as exc:
                problems.append(f"{asset.get('episode')}: unreadable index ({exc})")
                continue
            if actual != asset.get("actual_frame_count"):
                problems.append(
                    f"{asset.get('episode')}: runtime frame count {actual} != recorded "
                    f"{asset.get('actual_frame_count')}"
                )
        if not problems:
            return index_health
        return {**index_health, "status": "INCOMPLETE_INDEX", "reason": "; ".join(problems)}

    def _blocked_result(
        self,
        case_id: str,
        accused_path: Path,
        started: float,
        status: MatchStatus,
        reason: str,
        index_health: dict[str, Any],
        persist: bool,
    ) -> AnalysisResult:
        result = AnalysisResult(
            case_id, accused_path, status.value, [], [], time.perf_counter() - started,
            0, None, None, index_health, asdict(self.config.matching), self.encoder.name,
            [], [], "RED", reason,
        )
        if persist:
            self.database.replace_case_result(
                self.project_id, case_id, accused_path, None, None,
                result.status, reason, [], [],
            )
        logger.error(
            "Analysis blocked: %s", reason,
            extra={"case_id": case_id, "duration": result.elapsed_seconds},
        )
        return result
