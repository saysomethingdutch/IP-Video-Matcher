"""External tracing wrappers for the frozen Phase 2A TemporalAligner.

Every override delegates computation to ``super()`` and records the returned value.
The module deliberately contains no alternative alignment implementation.
"""

from __future__ import annotations

from dataclasses import asdict
import json
from typing import Any

from ipvm.config import MatchingConfig
from ipvm.matching import (
    TemporalAligner,
    _FittedSegment,
    _FitResult,
    _HypothesisPeak,
    _Proposal,
)
from ipvm.models import CandidateMatch, SegmentMatch

from .metrics import EPSILON, GRID_SECONDS, mapping_evaluation, segment_from_dict


def candidates_from_audit(audit: list[dict[str, Any]]) -> list[CandidateMatch]:
    return [
        CandidateMatch(
            accused_time=float(item["accused_time"]),
            source_episode=str(item["source_id"]),
            source_time=float(item["source_time"]),
            visual_score=float(item["adapted_visual_score"]),
        )
        for item in sorted(
            audit,
            key=lambda item: (float(item["accused_time"]), int(item["rank"])),
        )
    ]


class TracingTemporalAligner(TemporalAligner):
    """A transparent recorder around the frozen implementation."""

    def __init__(self, config: MatchingConfig, candidates: list[CandidateMatch]):
        super().__init__(config)
        self.events: dict[str, list[dict[str, Any]]] = {
            "sparse_peaks": [], "peak_support": [], "continuous_runs": [],
            "robust_fits": [], "proposals": [], "episode_extraction": [], "merges": [],
        }
        self._current_sparse_call_index: int | None = None
        self._current_peak: dict[str, Any] | None = None
        self._candidate_ids = {
            (item.accused_time, item.source_episode, item.source_time): f"C{index:04d}"
            for index, item in enumerate(candidates, 1)
        }

    def _ids(self, matches: list[CandidateMatch]) -> list[str]:
        return [
            self._candidate_ids.get(
                (item.accused_time, item.source_episode, item.source_time), "UNMAPPED"
            )
            for item in matches
        ]

    def _sparse_peaks(
        self, episode: str, matches: list[CandidateMatch]
    ) -> list[_HypothesisPeak]:
        result = super()._sparse_peaks(episode, matches)
        call_index = len(self.events["sparse_peaks"])
        self.events["sparse_peaks"].append({
            "call_index": call_index,
            "episode": episode,
            "input_candidate_ids": self._ids(matches),
            "peaks": [asdict(item) for item in result],
        })
        self._current_sparse_call_index = call_index
        self._current_peak = None
        return result

    def _support_for_peak(
        self, peak: _HypothesisPeak, matches: list[CandidateMatch]
    ) -> list[CandidateMatch]:
        result = super()._support_for_peak(peak, matches)
        self._current_peak = asdict(peak)
        self.events["peak_support"].append({
            "peak": asdict(peak),
            "input_candidate_ids": self._ids(matches),
            "support_candidate_ids": self._ids(result),
        })
        return result

    def _continuous_runs(self, matches: list[CandidateMatch]) -> list[list[CandidateMatch]]:
        result = super()._continuous_runs(matches)
        self.events["continuous_runs"].append({
            "input_candidate_ids": self._ids(matches),
            "runs": [self._ids(run) for run in result],
        })
        return result

    def _robust_fit(
        self, matches: list[CandidateMatch], initial_slope: float | None = None,
        initial_intercept: float | None = None,
    ) -> _FitResult:
        result = super()._robust_fit(matches, initial_slope, initial_intercept)
        self.events["robust_fits"].append({
            "input_candidate_ids": self._ids(matches),
            "initial_slope": initial_slope,
            "initial_intercept": initial_intercept,
            "slope": result.slope,
            "intercept": result.intercept,
            "inlier_candidate_ids": self._ids([
                item for item, keep in zip(matches, result.inliers, strict=True) if keep
            ]),
            "residual_rms_seconds": result.residual_rms,
            "residual_max_seconds": result.residual_max,
            "unconstrained_slope": result.unconstrained_slope,
        })
        return result

    def _proposal_to_segment(
        self, proposal: _Proposal, initial_slope: float | None = None,
        initial_intercept: float | None = None,
    ) -> _FittedSegment | None:
        result = super()._proposal_to_segment(proposal, initial_slope, initial_intercept)
        self.events["proposals"].append({
            "episode": proposal.episode,
            "proposal_vote": proposal.score,
            "input_candidate_ids": self._ids(proposal.matches),
            "initial_slope": initial_slope,
            "initial_intercept": initial_intercept,
            "sparse_call_index": self._current_sparse_call_index,
            "support_peak": self._current_peak,
            "result": result.segment.to_dict() if result is not None else None,
            "inlier_candidate_ids": self._ids(result.inliers) if result is not None else [],
            "fit_quality": list(self._fit_quality(result)) if result is not None else None,
        })
        return result

    def _extract_episode_segments(
        self, episode: str, matches: list[CandidateMatch]
    ) -> tuple[list[_FittedSegment], bool]:
        result, truncated = super()._extract_episode_segments(episode, matches)
        self.events["episode_extraction"].append({
            "episode": episode,
            "input_candidate_ids": self._ids(matches),
            "segments": [item.segment.to_dict() for item in result],
            "inlier_candidate_ids": [self._ids(item.inliers) for item in result],
            "truncated": truncated,
        })
        return result, truncated

    def _refine_adjacent_change_points(
        self, segments: list[_FittedSegment], episode_matches: list[CandidateMatch]
    ) -> list[_FittedSegment]:
        self._current_sparse_call_index = None
        self._current_peak = None
        return super()._refine_adjacent_change_points(segments, episode_matches)

    def _merge_adjacent(self, segments: list[_FittedSegment]) -> list[_FittedSegment]:
        self._current_sparse_call_index = None
        self._current_peak = None
        result = super()._merge_adjacent(segments)
        self.events["merges"].append({
            "input": [item.segment.to_dict() for item in segments],
            "output": [item.segment.to_dict() for item in result],
            "output_inlier_candidate_ids": [self._ids(item.inliers) for item in result],
        })
        return result


def _mapping_key(value: dict[str, Any]) -> tuple[Any, ...]:
    return (
        value["source_episode"],
        round(float(value["accused_start"]), 3),
        round(float(value["accused_end"]), 3),
        round(float(value["source_start"]), 3),
        round(float(value["source_end"]), 3),
    )


def _is_complete(value: dict[str, Any], truth: dict[str, Any]) -> bool:
    return mapping_evaluation(segment_from_dict(value), truth)["complete"]


def _json_equivalent(left: Any, right: Any) -> bool:
    """Compare persisted and replayed values at their JSON contract boundary.

    ``SegmentMatch.to_dict`` retains tuples for a few collection fields while a
    JSON round trip necessarily restores them as lists.  The persistence
    contract is JSON, so tuple/list representation is not a mapping change.
    Numeric values, object keys, array order, and every scalar remain exact.
    """

    options = {"sort_keys": True, "separators": (",", ":"), "allow_nan": False}
    return json.dumps(left, **options) == json.dumps(right, **options)


def trace_case(row: dict[str, Any], config: MatchingConfig | None = None) -> dict[str, Any]:
    """Replay one stored DEV candidate stream through the unchanged aligner."""

    if not row.get("positive") or len(row.get("truth", [])) != 1:
        raise ValueError("trace_case requires one positive single-segment DEV case")
    truth = row["truth"][0]
    candidates = candidates_from_audit(row["candidate_audit"])
    aligner = TracingTemporalAligner(config or MatchingConfig(), candidates)
    outcome = aligner.align_with_diagnostics(candidates, index_complete=True)
    replay_predictions = [item.to_dict() for item in outcome.segments]
    replay_alternatives = [item.to_dict() for item in outcome.alternatives]
    if not _json_equivalent(replay_predictions, row.get("predictions")) or not _json_equivalent(
        replay_alternatives, row.get("alternatives")
    ):
        raise ValueError("stored and replayed frozen alignment mappings differ")
    if row["status"] != ("AMBIGUOUS" if outcome.ambiguous or outcome.contradiction else (
        "MATCH_FOUND" if outcome.segments else "MATCH_NOT_FOUND"
    )):
        raise ValueError("stored and replayed status differ")

    gt_offset = float(truth["source_start"]) - float(truth.get("speed_ratio", 1.0)) * float(truth["accused_start"])
    compatible_peaks: list[dict[str, Any]] = []
    for call in aligner.events["sparse_peaks"]:
        for peak in call["peaks"]:
            if peak["episode"] != truth["source_episode"]:
                continue
            slope = float(peak["slope"])
            offset = float(peak["offset"])
            endpoint_errors = [
                abs(slope * float(truth[key]) + offset - (
                    float(truth["source_start"])
                    + float(truth.get("speed_ratio", 1.0))
                    * (float(truth[key]) - float(truth["accused_start"]))
                ))
                for key in ("accused_start", "accused_end")
            ]
            if (
                abs(slope - float(truth.get("speed_ratio", 1.0))) <= 0.025 + EPSILON
                and max(endpoint_errors) <= GRID_SECONDS + EPSILON
            ):
                compatible_peaks.append({**peak, "endpoint_errors_seconds": endpoint_errors})

    proposal_events = []
    for event in aligner.events["proposals"]:
        enriched = dict(event)
        enriched["evaluator_complete"] = bool(
            event["result"] is not None and _is_complete(event["result"], truth)
        )
        proposal_events.append(enriched)
    complete_proposal_events = [event for event in proposal_events if event["evaluator_complete"]]
    complete_proposal_keys = {
        _mapping_key(event["result"]) for event in complete_proposal_events
    }

    extracted = [
        segment
        for event in aligner.events["episode_extraction"]
        for segment in event["segments"]
    ]
    complete_extracted = [item for item in extracted if _is_complete(item, truth)]
    final_values = replay_predictions + replay_alternatives
    complete_final = [item for item in final_values if _is_complete(item, truth)]

    # Attach exact candidate membership for final alternatives by matching them to the
    # already-generated proposal record. This is raw ignored diagnostic material.
    proposal_by_key: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for event in proposal_events:
        if event["result"] is not None:
            proposal_by_key.setdefault(_mapping_key(event["result"]), []).append(event)
    alternative_membership = []
    for alternative in replay_alternatives:
        matches = proposal_by_key.get(_mapping_key(alternative), [])
        alternative_membership.append({
            "alternative": alternative,
            "proposal_matches": matches,
            "evaluation": mapping_evaluation(segment_from_dict(alternative), truth),
        })

    return {
        "case_id": row["case_id"],
        "backend": row["backend"],
        "candidate_count": len(candidates),
        "admitted_candidate_count": sum(
            item.visual_score >= (config or MatchingConfig()).minimum_visual_similarity
            for item in candidates
        ),
        "candidate_identity": [
            {
                "candidate_id": aligner._candidate_ids[(item.accused_time, item.source_episode, item.source_time)],
                "accused_time": item.accused_time,
                "source_episode": item.source_episode,
                "source_time": item.source_time,
                "visual_score": item.visual_score,
            }
            for item in candidates
        ],
        "truth": truth,
        "events": {**aligner.events, "proposals": proposal_events},
        "gt_offset": gt_offset,
        "gt_compatible_peak_count": len(compatible_peaks),
        "gt_compatible_peaks": compatible_peaks,
        "complete_gt_free_proposal_count": len(complete_proposal_keys),
        "complete_gt_free_proposal_event_count": len(complete_proposal_events),
        "first_sparse_call_complete_proposal_count": len({
            _mapping_key(event["result"])
            for event in complete_proposal_events
            if event.get("sparse_call_index") == 0
        }),
        "complete_extracted_count": len(complete_extracted),
        "complete_final_count": len(complete_final),
        "replay_predictions": replay_predictions,
        "replay_alternatives": replay_alternatives,
        "alternative_membership": alternative_membership,
        "competing_candidates": outcome.competing_candidates,
        "conflict_graph": outcome.conflict_graph,
        "truncated_episodes": list(outcome.truncated_episodes),
        "warnings": list(outcome.warnings),
    }


def classify_first_loss(trace: dict[str, Any], oracle: dict[str, Any]) -> str:
    nearest_complete = bool(
        oracle.get("nearest_per_frame_fit", {}).get("evaluation", {}).get("complete")
    )
    if not nearest_complete:
        return "CANDIDATE_ORACLE"
    if not trace["gt_compatible_peak_count"]:
        return "SPARSE_PEAK"
    if not trace["complete_gt_free_proposal_count"]:
        return "SUPPORT_OR_FIT"
    if not trace["complete_extracted_count"]:
        return "EXTRACTION"
    if not trace["complete_final_count"]:
        return "SUPPRESSION_OR_ALTERNATIVE"
    return "NONE"


def sanitized_trace(trace: dict[str, Any], oracle: dict[str, Any]) -> dict[str, Any]:
    proposals = [event for event in trace["events"]["proposals"] if event["result"]]
    correct = [
        event for event in proposals
        if event["result"]["source_episode"] == trace["truth"]["source_episode"]
    ]
    return {
        "case_id": trace["case_id"],
        "backend": trace["backend"],
        "candidate_count": trace["candidate_count"],
        "admitted_candidate_count": trace["admitted_candidate_count"],
        "sparse_peak_call_count": len(trace["events"]["sparse_peaks"]),
        "peak_count": sum(len(item["peaks"]) for item in trace["events"]["sparse_peaks"]),
        "gt_compatible_peak_count": trace["gt_compatible_peak_count"],
        "support_call_count": len(trace["events"]["peak_support"]),
        "robust_fit_call_count": len(trace["events"]["robust_fits"]),
        "proposal_count": len(proposals),
        "correct_source_proposal_count": len(correct),
        "correct_source_proposal_slopes": sorted({
            round(float(item["result"]["speed_ratio"]), 4)
            for item in correct if item["result"].get("speed_ratio") is not None
        }),
        "complete_gt_free_proposal_count": trace["complete_gt_free_proposal_count"],
        "complete_gt_free_proposal_event_count": trace["complete_gt_free_proposal_event_count"],
        "first_sparse_call_complete_proposal_count": trace["first_sparse_call_complete_proposal_count"],
        "complete_extracted_count": trace["complete_extracted_count"],
        "complete_final_count": trace["complete_final_count"],
        "final_primary_count": len(trace["replay_predictions"]),
        "final_alternative_count": len(trace["replay_alternatives"]),
        "first_loss_stage": classify_first_loss(trace, oracle),
    }
