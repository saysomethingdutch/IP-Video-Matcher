from __future__ import annotations

import pytest

from ipvm.config import MatchingConfig
from ipvm.matching import TemporalAligner, _Proposal
from ipvm.models import CandidateMatch


def candidates(episode: str, accused_start: float, source_start: float, count: int):
    return [
        CandidateMatch(accused_start + index * 0.5, episode, source_start + index * 0.5, 0.99)
        for index in range(count)
    ]


def test_temporal_alignment_maps_episode_and_timeline() -> None:
    aligner = TemporalAligner(MatchingConfig(minimum_segment_seconds=3.0))
    matches = candidates("EP07", 2.0, 42.0, 8)
    matches += [CandidateMatch(index * 0.5, "EP03", 99.0, 0.2) for index in range(8)]
    segments = aligner.align(matches)
    assert len(segments) == 1
    assert segments[0].source_episode == "EP07"
    assert segments[0].accused_start == 2.0
    assert segments[0].accused_end == 6.0
    assert segments[0].source_start == 42.0
    assert segments[0].source_end == 46.0
    assert segments[0].review_tier == "REVIEW_REQUIRED"
    assert segments[0].mirrored is None
    assert segments[0].cropped is None
    safe_outcome = aligner.align_with_diagnostics(matches, index_complete=True)
    assert safe_outcome.segments[0].candidate_tier == "GREEN"
    assert safe_outcome.segments[0].review_tier == "REVIEW_REQUIRED"


def test_temporal_alignment_is_piecewise() -> None:
    aligner = TemporalAligner(MatchingConfig(minimum_segment_seconds=3.0))
    matches = candidates("EP01", 0.0, 10.0, 8)
    matches += candidates("EP02", 5.0, 30.0, 8)
    segments = aligner.align(matches)
    assert [(item.source_episode, item.accused_start) for item in segments] == [
        ("EP01", 0.0), ("EP02", 5.0)
    ]


def test_short_match_is_rejected() -> None:
    aligner = TemporalAligner(MatchingConfig(minimum_segment_seconds=3.0))
    assert aligner.align(candidates("EP01", 0.0, 10.0, 5)) == []


def test_exact_cross_episode_duplicate_is_ambiguous() -> None:
    aligner = TemporalAligner(MatchingConfig(minimum_segment_seconds=3.0))
    matches = candidates("EP01", 0.0, 10.0, 12)
    matches += candidates("EP02", 0.0, 20.0, 12)
    outcome = aligner.align_with_diagnostics(matches)
    assert outcome.ambiguous is True
    assert len(outcome.segments) == 1
    assert outcome.segments[0].review_tier == "REVIEW_REQUIRED"
    assert {item.source_episode for item in outcome.alternatives} == {"EP02"}


@pytest.mark.parametrize("speed", [0.75, 0.80, 0.90, 1.0, 1.10, 1.25, 1.40, 1.50])
def test_temporal_alignment_v2_ideal_speed_sweep(speed: float) -> None:
    aligner = TemporalAligner(MatchingConfig(minimum_segment_seconds=3.0))
    accused_start = 2.0
    source_start = 40.0
    matches = [
        CandidateMatch(
            accused_start + index * 0.5,
            "EP07",
            source_start + speed * index * 0.5,
            0.97,
        )
        for index in range(20)
    ]
    outcome = aligner.align_with_diagnostics(matches, index_complete=True)
    assert outcome.ambiguous is False
    assert len(outcome.segments) == 1
    segment = outcome.segments[0]
    assert segment.source_episode == "EP07"
    assert segment.accused_start == accused_start
    assert segment.accused_end == accused_start + 10.0
    assert segment.source_start == pytest.approx(source_start, abs=0.05)
    assert segment.source_end == pytest.approx(source_start + speed * 10.0, abs=0.05)
    assert segment.speed_ratio == pytest.approx(speed, abs=0.01)
    assert segment.fitted_offset_seconds == pytest.approx(
        source_start - speed * accused_start, abs=0.05
    )
    assert segment.residual_rms_seconds <= 0.01
    assert segment.residual_max_seconds <= 0.01
    assert segment.inlier_count == 20
    assert segment.candidate_count == 20
    assert segment.inlier_ratio == 1.0
    assert segment.timestamp_basis == "nominal_grid_time"
    assert segment.review_tier == "REVIEW_REQUIRED"


def test_temporal_alignment_v2_tolerates_grid_jitter_and_high_score_outliers() -> None:
    aligner = TemporalAligner(MatchingConfig(minimum_segment_seconds=3.0))
    speed = 1.25
    jitter = (-0.5, 0.0, 0.5, 0.0)
    matches = []
    for index in range(24):
        accused = index * 0.5
        matches.append(CandidateMatch(
            accused, "EP01", 25.0 + speed * accused + jitter[index % 4], 0.96
        ))
        matches.append(CandidateMatch(
            accused, "EP01", 80.0 + ((index * 7) % 19) * 0.5, 1.0
        ))
        matches.append(CandidateMatch(
            accused, "EP99", 140.0 + ((index * 11) % 23) * 0.5, 0.999
        ))
    outcome = aligner.align_with_diagnostics(matches, index_complete=True)
    assert outcome.segments
    segment = outcome.segments[0]
    assert segment.source_episode == "EP01"
    assert segment.speed_ratio == pytest.approx(speed, abs=0.05)
    assert segment.accused_start <= 0.5
    assert segment.accused_end >= 11.5
    assert segment.inlier_count >= 18
    assert segment.residual_max_seconds <= 0.5 + 1e-6


def test_candidate_uncertainty_is_derived_from_configured_grid_period() -> None:
    aligner = TemporalAligner(MatchingConfig(
        coarse_fps=4.0,
        minimum_segment_seconds=3.0,
        candidate_timestamp_uncertainty_grid_steps=1,
    ))
    assert aligner.frame_interval == 0.25
    assert aligner.offset_bin_seconds == 0.25
    assert aligner.candidate_timestamp_tolerance_seconds == 0.25
    jitter = (-0.25, 0.0, 0.25, 0.0)
    matches = [
        CandidateMatch(
            index * 0.25,
            "EP01",
            10.0 + index * 0.25 + jitter[index % len(jitter)],
            0.98,
        )
        for index in range(40)
    ]
    segment = aligner.align(matches)[0]
    assert segment.speed_ratio == pytest.approx(1.0, abs=0.05)
    assert segment.residual_max_seconds <= 0.25 + 1e-6


def test_temporal_alignment_v2_extracts_five_piecewise_segments_with_reuse() -> None:
    aligner = TemporalAligner(MatchingConfig(minimum_segment_seconds=3.0))
    specifications = [
        ("EP01", 0.0, 10.0, 1.0),
        ("EP02", 8.0, 30.0, 1.25),
        ("EP01", 14.0, 50.0, 0.8),
        ("EP03", 22.0, 70.0, 1.1),
        ("EP01", 30.0, 10.0, 1.0),
    ]
    matches: list[CandidateMatch] = []
    for episode, accused_start, source_start, speed in specifications:
        for index in range(12):
            accused = accused_start + index * 0.5
            matches.append(CandidateMatch(
                accused,
                episode,
                source_start + speed * index * 0.5,
                0.98,
            ))
            matches.append(CandidateMatch(
                accused,
                episode,
                150.0 + ((index * 7 + int(accused_start)) % 29) * 0.5,
                0.995,
            ))
    outcome = aligner.align_with_diagnostics(matches, index_complete=True)
    assert outcome.ambiguous is False
    assert outcome.contradiction is False
    assert len(outcome.segments) == 5
    assert [item.source_episode for item in outcome.segments] == [
        "EP01", "EP02", "EP01", "EP03", "EP01"
    ]
    assert [item.speed_ratio for item in outcome.segments] == pytest.approx(
        [1.0, 1.25, 0.8, 1.1, 1.0], abs=0.02
    )
    reused = [item for item in outcome.segments if item.source_reuse]
    assert len(reused) == 2
    assert {(item.accused_start, item.source_start) for item in reused} == {
        (0.0, 10.0), (30.0, 10.0)
    }


def test_adjacent_segment_merge_refits_all_inliers_instead_of_averaging_slopes() -> None:
    aligner = TemporalAligner(MatchingConfig(minimum_segment_seconds=3.0))
    first_matches = [
        CandidateMatch(index * 0.5, "EP01", 10.0 + 0.8 * index * 0.5, 0.99)
        for index in range(9)
    ]
    second_matches = [
        CandidateMatch(4.5 + index * 0.5, "EP01", 13.6 + index * 0.5, 0.99)
        for index in range(20)
    ]
    first = aligner._proposal_to_segment(_Proposal("EP01", first_matches, 1.0))
    second = aligner._proposal_to_segment(_Proposal("EP01", second_matches, 1.0))
    assert first is not None and second is not None

    merged = aligner._merge_adjacent([first, second])
    independently_refitted = aligner._proposal_to_segment(
        _Proposal("EP01", first_matches + second_matches, 2.0)
    )

    assert len(merged) == 1
    assert independently_refitted is not None
    assert merged[0].segment.speed_ratio == independently_refitted.segment.speed_ratio
    arithmetic_mean = (first.segment.speed_ratio + second.segment.speed_ratio) / 2
    assert merged[0].segment.speed_ratio != pytest.approx(arithmetic_mean, abs=0.02)
