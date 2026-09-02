from __future__ import annotations

from ipvm.config import MatchingConfig
from ipvm.matching import TemporalAligner
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
    assert segments[0].review_tier == "YELLOW"
    assert segments[0].mirrored is None
    assert segments[0].cropped is None
    safe_outcome = aligner.align_with_diagnostics(matches, index_complete=True)
    assert safe_outcome.segments[0].review_tier == "GREEN"


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
    assert outcome.segments == []
    assert {item.source_episode for item in outcome.alternatives} == {"EP01", "EP02"}
