from __future__ import annotations

import logging
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
    review_tier: str
    reason: str | None = None


@dataclass(frozen=True)
class AlignmentOutcome:
    segments: list[SegmentMatch]
    alternatives: list[SegmentMatch]
    competing_candidates: list[dict[str, Any]]
    ambiguous: bool
    contradiction: bool
    reason: str | None


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


def _interval_iou(left: SegmentMatch, right: SegmentMatch) -> float:
    overlap = max(0.0, min(left.accused_end, right.accused_end) - max(left.accused_start, right.accused_start))
    union = max(left.accused_end, right.accused_end) - min(left.accused_start, right.accused_start)
    return overlap / union if union > 0 else 0.0


class TemporalAligner:
    """Phase-1 offset clustering and RANSAC, plus Phase-1.5 safety diagnostics."""

    def __init__(self, config: MatchingConfig):
        self.config = config
        self.frame_interval = 1.0 / config.coarse_fps

    def _proposals(self, candidates: list[CandidateMatch]) -> list[_Proposal]:
        buckets: dict[tuple[str, int], list[CandidateMatch]] = defaultdict(list)
        for candidate in candidates:
            if candidate.visual_score < self.config.minimum_visual_similarity:
                continue
            offset = candidate.source_time - candidate.accused_time
            bucket = round(offset / self.config.offset_bin_seconds)
            buckets[(candidate.source_episode, bucket)].append(candidate)

        proposals: list[_Proposal] = []
        for (episode, _), bucket_matches in buckets.items():
            best_by_query: dict[float, CandidateMatch] = {}
            for match in bucket_matches:
                current = best_by_query.get(match.accused_time)
                if current is None or match.visual_score > current.visual_score:
                    best_by_query[match.accused_time] = match
            ordered = sorted(best_by_query.values(), key=lambda item: item.accused_time)
            runs: list[list[CandidateMatch]] = []
            current_run: list[CandidateMatch] = []
            for match in ordered:
                if current_run and (
                    match.accused_time - current_run[-1].accused_time
                    > self.config.maximum_frame_gap_seconds
                ):
                    runs.append(current_run)
                    current_run = []
                current_run.append(match)
            if current_run:
                runs.append(current_run)
            for run in runs:
                duration = run[-1].accused_time - run[0].accused_time + self.frame_interval
                if duration + 1e-6 < self.config.minimum_segment_seconds:
                    continue
                mean_visual = float(np.mean([item.visual_score for item in run]))
                proposals.append(_Proposal(episode, run, duration * mean_visual))
        return proposals

    @staticmethod
    def _robust_fit(matches: list[CandidateMatch]) -> tuple[float, float, np.ndarray]:
        accused = np.asarray([item.accused_time for item in matches], dtype=np.float64)
        source = np.asarray([item.source_time for item in matches], dtype=np.float64)
        if len(matches) < 2 or np.ptp(accused) < 1e-9:
            return 1.0, float(np.median(source - accused)), np.ones(len(matches), dtype=bool)
        best_inliers = np.zeros(len(matches), dtype=bool)
        best_error = float("inf")
        rng = np.random.default_rng(17)
        iterations = min(100, max(20, len(matches) * 4))
        for _ in range(iterations):
            left, right = rng.choice(len(matches), size=2, replace=False)
            delta = accused[right] - accused[left]
            if abs(delta) < 1e-9:
                continue
            slope = (source[right] - source[left]) / delta
            if not 0.5 <= slope <= 2.0:
                continue
            intercept = source[left] - slope * accused[left]
            residuals = np.abs(source - (slope * accused + intercept))
            inliers = residuals <= 0.55
            error = float(residuals[inliers].mean()) if inliers.any() else float("inf")
            if inliers.sum() > best_inliers.sum() or (
                inliers.sum() == best_inliers.sum() and error < best_error
            ):
                best_inliers = inliers
                best_error = error
        if best_inliers.sum() < 2:
            best_inliers[:] = True
        design = np.column_stack([accused[best_inliers], np.ones(best_inliers.sum())])
        slope, intercept = np.linalg.lstsq(design, source[best_inliers], rcond=None)[0]
        return float(slope), float(intercept), best_inliers

    def _proposal_to_segment(self, proposal: _Proposal) -> SegmentMatch | None:
        slope, intercept, inliers = self._robust_fit(proposal.matches)
        inlier_matches = [
            match for match, keep in zip(proposal.matches, inliers, strict=True) if keep
        ]
        if not inlier_matches:
            return None
        start = min(item.accused_time for item in inlier_matches)
        end = max(item.accused_time for item in inlier_matches) + self.frame_interval
        if end - start + 1e-6 < self.config.minimum_segment_seconds:
            return None
        visual = float(np.mean([item.visual_score for item in inlier_matches]))
        coverage = min(1.0, len(inlier_matches) / max(1.0, (end - start) * self.config.coarse_fps))
        temporal = min(1.0, len(inlier_matches) / len(proposal.matches))
        accused = np.asarray([item.accused_time for item in inlier_matches])
        source = np.asarray([item.source_time for item in inlier_matches])
        residual = float(np.max(np.abs(source - (slope * accused + intercept))))
        match_score = max(0.0, min(1.0, visual * (0.7 + 0.3 * coverage) * temporal))
        return SegmentMatch(
            accused_start=round(start, 3),
            accused_end=round(end, 3),
            source_episode=proposal.episode,
            source_start=round(max(0.0, slope * start + intercept), 3),
            source_end=round(max(0.0, slope * end + intercept), 3),
            speed_ratio=round(slope, 4),
            visual_score=round(visual, 4),
            audio_score=None,
            temporal_score=round(temporal, 4),
            match_score=round(match_score, 4),
            temporal_residual_seconds=round(residual, 4),
            coverage=round(coverage, 4),
            algorithm_version=self.config.algorithm_version,
        )

    def align(self, candidates: list[CandidateMatch]) -> list[SegmentMatch]:
        return self.align_with_diagnostics(candidates).segments

    def align_with_diagnostics(
        self, candidates: list[CandidateMatch], index_complete: bool = False
    ) -> AlignmentOutcome:
        proposals = sorted(self._proposals(candidates), key=lambda item: item.score, reverse=True)
        scored_segments = [
            (proposal, segment)
            for proposal in proposals
            if (segment := self._proposal_to_segment(proposal)) is not None
        ]
        claimed_frames: set[int] = set()
        accepted: list[SegmentMatch] = []
        for proposal, segment in scored_segments:
            frame_ids = {round(item.accused_time / self.frame_interval) for item in proposal.matches}
            if frame_ids and len(frame_ids & claimed_frames) / len(frame_ids) > 0.5:
                continue
            accepted.append(segment)
            claimed_frames.update(frame_ids)
        accepted.sort(key=lambda item: item.accused_start)
        accepted = self._merge_adjacent(accepted)

        ambiguous = False
        alternatives: list[SegmentMatch] = []
        competing: list[dict[str, Any]] = []
        assessed: list[SegmentMatch] = []
        for winner in accepted:
            rivals = [
                candidate for _, candidate in scored_segments
                if _interval_iou(winner, candidate) >= 0.8
                and not (
                    candidate.source_episode == winner.source_episode
                    and abs(candidate.source_start - winner.source_start) <= 1.0
                )
            ]
            rivals.sort(key=lambda item: item.match_score, reverse=True)
            runner_up = rivals[0] if rivals else None
            margin = winner.match_score - runner_up.match_score if runner_up else 1.0
            is_ambiguous = bool(
                runner_up and runner_up.match_score / max(winner.match_score, 1e-9)
                >= self.config.ambiguity_score_ratio
            )
            tier = self._review_tier(winner, margin, is_ambiguous, index_complete)
            winner = replace(winner, uniqueness_margin=round(margin, 4), review_tier=tier)
            assessed.append(winner)
            for rival in rivals[: self.config.audit_top_candidates]:
                competing.append({
                    "winner": winner.to_dict(),
                    "candidate": rival.to_dict(),
                    "score_margin": round(winner.match_score - rival.match_score, 4),
                    "score_ratio": round(rival.match_score / max(winner.match_score, 1e-9), 4),
                })
            if is_ambiguous:
                ambiguous = True
                alternatives.extend([winner, *rivals[: self.config.audit_top_candidates]])

        contradiction = self._has_contradiction(assessed)
        if contradiction:
            assessed = [replace(segment, review_tier="YELLOW") for segment in assessed]
            alternatives.extend(assessed)
        reason = None
        if ambiguous:
            reason = "Equivalent competing source candidates exceed the ambiguity score ratio"
        elif contradiction:
            reason = "Segment contradiction detected"
        unique_alternatives: dict[tuple[Any, ...], SegmentMatch] = {}
        for segment in alternatives:
            key = (
                segment.accused_start, segment.accused_end, segment.source_episode,
                segment.source_start, segment.source_end,
            )
            unique_alternatives[key] = segment
        return AlignmentOutcome(
            segments=[] if ambiguous or contradiction else assessed,
            alternatives=list(unique_alternatives.values()),
            competing_candidates=competing[: self.config.audit_top_candidates],
            ambiguous=ambiguous,
            contradiction=contradiction,
            reason=reason,
        )

    def _review_tier(
        self, segment: SegmentMatch, margin: float, ambiguous: bool, index_complete: bool
    ) -> str:
        if ambiguous:
            return "RED"
        green = (
            index_complete
            and margin >= self.config.minimum_uniqueness_margin
            and segment.temporal_residual_seconds <= self.config.maximum_temporal_residual_seconds
            and segment.coverage >= self.config.minimum_coverage
            and segment.temporal_score >= self.config.minimum_temporal_consistency
        )
        return "GREEN" if green else "YELLOW"

    @staticmethod
    def _has_contradiction(segments: list[SegmentMatch]) -> bool:
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
                    return True
                source_overlap = max(
                    0.0, min(left.source_end, right.source_end)
                    - max(left.source_start, right.source_start)
                )
                if (
                    left.source_episode == right.source_episode
                    and accused_overlap <= 0.25 and source_overlap > 0.5
                ):
                    return True
        return False

    def _merge_adjacent(self, segments: list[SegmentMatch]) -> list[SegmentMatch]:
        if not segments:
            return []
        merged = [segments[0]]
        for segment in segments[1:]:
            previous = merged[-1]
            compatible = (
                segment.source_episode == previous.source_episode
                and segment.accused_start - previous.accused_end <= self.config.maximum_frame_gap_seconds
                and abs(segment.source_start - previous.source_end) <= 1.5
            )
            if not compatible:
                merged.append(segment)
                continue
            first_duration = previous.accused_end - previous.accused_start
            second_duration = segment.accused_end - segment.accused_start
            total_duration = first_duration + second_duration
            weighted_visual = (
                previous.visual_score * first_duration + segment.visual_score * second_duration
            ) / total_duration
            merged[-1] = SegmentMatch(
                accused_start=previous.accused_start,
                accused_end=segment.accused_end,
                source_episode=previous.source_episode,
                source_start=previous.source_start,
                source_end=segment.source_end,
                speed_ratio=round((previous.speed_ratio + segment.speed_ratio) / 2, 4),
                visual_score=round(weighted_visual, 4),
                audio_score=None,
                temporal_score=min(previous.temporal_score, segment.temporal_score),
                match_score=min(previous.match_score, segment.match_score),
                temporal_residual_seconds=max(
                    previous.temporal_residual_seconds, segment.temporal_residual_seconds
                ),
                coverage=min(previous.coverage, segment.coverage),
                algorithm_version=self.config.algorithm_version,
            )
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
                tier = "RED"
            elif alignment.segments:
                status = MatchStatus.MATCH_FOUND
                tier = "GREEN" if all(
                    segment.review_tier == "GREEN" for segment in alignment.segments
                ) else "YELLOW"
            else:
                status = MatchStatus.MATCH_NOT_FOUND
                tier = "RED"
            result = AnalysisResult(
                case_id, accused_path, status.value, alignment.segments,
                alignment.alternatives, time.perf_counter() - started, len(extracted),
                digest, metadata, index_health, asdict(self.config.matching), self.encoder.name,
                alignment.competing_candidates, tier, alignment.reason,
            )
        except (MediaToolError, FileNotFoundError, OSError, ValueError) as exc:
            logger.exception("Analysis failed for %s", case_id)
            result = AnalysisResult(
                case_id, accused_path, MatchStatus.ERROR.value, [], [],
                time.perf_counter() - started, 0, None, None, index_health,
                asdict(self.config.matching), self.encoder.name, [], "RED", str(exc),
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
            [], "RED", reason,
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
