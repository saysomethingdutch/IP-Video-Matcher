from __future__ import annotations

import random

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
    # jitter is a robustness diagnostic: the fit must not look authoritative
    assert segment.candidate_tier != "GREEN"
    assert segment.accused_start <= 0.5
    assert segment.accused_end >= 11.5
    # 3f5f4d5's duration-dominant ranking selected an 18-inlier run here; the Phase 2A.1
    # normalized ranking selects a marginally different run in this extreme-jitter regime.
    # Support must stay a clear majority of the span, and every candidate in the chosen
    # run must be an inlier.
    # With the four-frame (-1, 0, +1, 0)-grid pattern, at least three observations per
    # cycle must support one coherent line. This derives 18/24 independently of today's
    # deterministic result (currently 19/24); it is not a snapshot-calibrated 19.
    assert segment.inlier_count >= 18
    assert segment.inlier_ratio >= 0.75
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


def test_merge_joins_one_line_split_by_sparse_evidence_and_refits_union() -> None:
    """Phase 2A.1 item 1: same underlying line split by an evidence hole -> merge."""
    aligner = TemporalAligner(MatchingConfig(minimum_segment_seconds=3.0))
    first_matches = [
        CandidateMatch(index * 0.5, "EP01", 100.0 + 1.25 * index * 0.5, 0.99)
        for index in range(9)
    ]
    # 1.0s hole, then the SAME line continues
    second_matches = [
        CandidateMatch(5.5 + index * 0.5, "EP01", 100.0 + 1.25 * (5.5 + index * 0.5), 0.99)
        for index in range(14)
    ]
    first = aligner._proposal_to_segment(_Proposal("EP01", first_matches, 1.0))
    second = aligner._proposal_to_segment(_Proposal("EP01", second_matches, 1.0))
    assert first is not None and second is not None

    merged = aligner._merge_adjacent([first, second])
    assert len(merged) == 1

    independently_refitted = aligner._proposal_to_segment(
        _Proposal("EP01", first_matches + second_matches, 2.0)
    )
    assert independently_refitted is not None
    # union refit, never an average of the two old slopes
    assert merged[0].segment.speed_ratio == independently_refitted.segment.speed_ratio
    assert merged[0].segment.accused_start == first.segment.accused_start
    assert merged[0].segment.accused_end == second.segment.accused_end


def test_merge_rejects_speed_discontinuity_instead_of_fabricating_a_mean_slope() -> None:
    """Phase 2A.1 item 1 (Critical): 0.8x followed by 1.25x must NOT merge."""
    aligner = TemporalAligner(MatchingConfig(minimum_segment_seconds=3.0))
    slow = [
        CandidateMatch(index * 0.5, "EP01", 100.0 + 0.8 * index * 0.5, 0.99)
        for index in range(24)
    ]
    fast = [
        CandidateMatch(12.0 + index * 0.5, "EP01", 109.6 + 1.25 * index * 0.5, 0.99)
        for index in range(24)
    ]
    first = aligner._proposal_to_segment(_Proposal("EP01", slow, 1.0))
    second = aligner._proposal_to_segment(_Proposal("EP01", fast, 1.0))
    assert first is not None and second is not None
    consistent, divergence = aligner._lines_stay_consistent(first.segment, second.segment)
    assert consistent is False
    assert divergence > aligner.frame_interval

    merged = aligner._merge_adjacent([first, second])
    assert len(merged) == 2
    # no fabricated ~1.0 slope anywhere
    for fitted in merged:
        assert fitted.segment.speed_ratio is not None
        assert not (0.95 <= fitted.segment.speed_ratio <= 1.05)


def test_merge_does_not_bridge_a_genuine_unrelated_gap() -> None:
    """Phase 2A.1 item 3: a real content gap must not become continuous rights-work use."""
    aligner = TemporalAligner(MatchingConfig())
    early = [
        CandidateMatch(index * 0.5, "EP01", 100.0 + index * 0.5, 0.99) for index in range(20)
    ]
    late = [
        CandidateMatch(30.0 + index * 0.5, "EP01", 130.0 + index * 0.5, 0.99)
        for index in range(20)
    ]
    outcome = aligner.align_with_diagnostics(early + late, index_complete=True)
    assert len(outcome.segments) == 2
    assert outcome.segments[0].accused_end < outcome.segments[1].accused_start


def test_retrieval_hole_within_gap_tolerance_stays_one_segment() -> None:
    """Phase 2A.1 item 3: the reviewer's `##..########` hole must not split a segment."""
    aligner = TemporalAligner(MatchingConfig())
    full = [
        CandidateMatch(index * 0.5, "EP01", 1867.5 + index * 0.5, 0.99) for index in range(12)
    ]
    holed = [item for item in full if round(item.accused_time / 0.5) not in (2, 3)]
    outcome = aligner.align_with_diagnostics(holed, index_complete=True)
    assert len(outcome.segments) == 1
    assert outcome.segments[0].accused_start == 0.0
    assert outcome.segments[0].source_start == pytest.approx(1867.5, abs=0.25)


@pytest.mark.parametrize("speed", [0.75, 0.80, 1.00, 1.25, 1.50])
def test_same_line_merges_across_maximum_tolerated_hole_at_all_speeds(
    speed: float,
) -> None:
    """A 1.5s accused retrieval hole scales to `speed * 1.5` on source."""
    matches = [
        CandidateMatch(
            index * 0.5,
            "EP01",
            round((100.0 + speed * index * 0.5) * 2) / 2,
            0.99,
        )
        for index in range(49)
        if index not in (20, 21, 22)
    ]
    outcome = TemporalAligner(MatchingConfig()).align_with_diagnostics(
        matches, index_complete=True
    )
    assert len(outcome.segments) == 1
    assert outcome.segments[0].accused_start == 0.0
    assert outcome.segments[0].accused_end == 24.5


def test_slope_aware_continuity_does_not_bridge_inserted_accused_content() -> None:
    """A source pause across an accused gap is not a retrieval hole on one line."""
    aligner = TemporalAligner(MatchingConfig())
    before = [
        CandidateMatch(index * 0.5, "EP01", 100.0 + 1.5 * index * 0.5, 0.99)
        for index in range(20)
    ]
    after = [
        CandidateMatch(11.5 + index * 0.5, "EP01", 115.0 + 1.5 * index * 0.5, 0.99)
        for index in range(20)
    ]
    left = aligner._proposal_to_segment(_Proposal("EP01", before, 1.0))
    right = aligner._proposal_to_segment(_Proposal("EP01", after, 1.0))
    assert left is not None and right is not None
    assert not aligner._same_temporal_line(left.segment, right.segment)
    assert len(aligner._merge_adjacent([left, right])) == 2


def test_separate_same_line_fragments_fall_back_to_incomplete_non_green(
    monkeypatch,
) -> None:
    """If merge cannot run, same-line neighbours must not mask orphan evidence."""
    matches = [
        CandidateMatch(index * 0.5, "EP01", 100.0 + 1.25 * index * 0.5, 0.99)
        for index in range(49)
        if index not in (20, 21, 22)
    ]
    aligner = TemporalAligner(MatchingConfig())
    monkeypatch.setattr(aligner, "_merge_adjacent", lambda segments: segments)
    outcome = aligner.align_with_diagnostics(matches, index_complete=True)
    assert len(outcome.segments) == 2
    assert all(
        segment.boundary_completeness == "INCOMPLETE"
        for segment in outcome.segments
    )
    assert all(segment.candidate_tier != "GREEN" for segment in outcome.segments)
    assert all(segment.review_tier == "REVIEW_REQUIRED" for segment in outcome.segments)


def test_long_weak_wrong_line_does_not_suppress_short_strong_correct_line() -> None:
    """Phase 2A.1 item 2 (Critical): reviewer's exact adversarial ranking case."""
    aligner = TemporalAligner(MatchingConfig())

    def grid_line(start, end, source_start, score):
        return [
            CandidateMatch(
                start + index * 0.5,
                "EP01",
                round((source_start + index * 0.5) * 2) / 2,
                score,
            )
            for index in range(int((end - start) * 2))
        ]

    decoy = grid_line(0.0, 30.0, 900.0, 0.90)
    correct = grid_line(10.0, 20.0, 500.0, 0.99)
    outcome = aligner.align_with_diagnostics(decoy + correct, index_complete=True)
    emitted = [
        segment for segment in outcome.segments if abs(segment.source_start - 500.0) <= 1.0
    ]
    alternates = [
        segment for segment in outcome.alternatives if abs(segment.source_start - 500.0) <= 1.0
    ]
    # the correct explanation must survive somewhere, never be silently deleted
    assert emitted or alternates
    # and the incompatible pair must be surfaced for review, not auto-accepted
    assert outcome.ambiguous or outcome.contradiction
    for segment in outcome.segments:
        assert segment.review_tier == "REVIEW_REQUIRED"


def test_pure_noise_produces_no_segments() -> None:
    """Phase 2A.1 item 14: pure-noise false-positive rate must stay zero."""
    randomizer = random.Random(4242)
    episodes = ("EP01", "EP02", "EP03")
    candidates = [
        CandidateMatch(
            index * 0.5,
            randomizer.choice(episodes),
            round(randomizer.randrange(0, 4800) / 2, 3),
            round(randomizer.uniform(0.83, 0.99), 4),
        )
        for index in range(240)
        for _ in range(8)
    ]
    outcome = TemporalAligner(MatchingConfig()).align_with_diagnostics(
        candidates, index_complete=True
    )
    assert outcome.segments == []


def test_competing_coherent_lines_stay_ambiguous() -> None:
    """Phase 2A.1 item 9: two equally credible cross-episode lines must not be guessed."""
    aligner = TemporalAligner(MatchingConfig())
    left = [CandidateMatch(index * 0.5, "EP01", 100.0 + index * 0.5, 0.90) for index in range(20)]
    right = [CandidateMatch(index * 0.5, "EP02", 700.0 + index * 0.5, 0.899) for index in range(20)]
    outcome = aligner.align_with_diagnostics(left + right, index_complete=True)
    assert outcome.ambiguous
    assert outcome.alternatives
    for segment in outcome.segments:
        assert segment.candidate_tier != "GREEN"


# 0.70 is excluded: at a 0.5s source grid its snapped sub-runs fit inside the
# configured range, so it is not reliably distinguishable from an in-range speed.
@pytest.mark.parametrize("speed", [0.60, 0.65, 1.65, 1.80])
def test_out_of_range_speed_is_never_reported_as_a_clamped_endpoint(speed: float) -> None:
    """Phase 2A.1 item 4: no silent clamping to 0.75 / 1.50."""
    aligner = TemporalAligner(MatchingConfig())
    candidates = [
        CandidateMatch(
            index * 0.5, "EP01", round((200.0 + speed * index * 0.5) * 2) / 2, 0.99
        )
        for index in range(40)
    ]
    outcome = aligner.align_with_diagnostics(candidates, index_complete=True)
    assert outcome.segments
    # invariant: a slope outside the configured range is never reported as a speed
    for segment in outcome.segments:
        assert segment.review_tier == "REVIEW_REQUIRED"
        unconstrained = segment.unconstrained_speed_ratio
        if unconstrained is not None and not (0.75 <= unconstrained <= 1.50):
            assert segment.speed_out_of_range is True
            assert segment.speed_ratio is None
            assert segment.candidate_tier != "GREEN"
        # and no segment may present a clamped endpoint as a measurement
        assert segment.speed_ratio not in (0.75, 1.5)
        # nor may it claim the true out-of-range speed it cannot represent
        if segment.speed_ratio is not None:
            assert 0.75 <= segment.speed_ratio <= 1.50


@pytest.mark.parametrize(
    ("speed", "detectable"),
    [(0.72, True), (0.74, False), (1.51, False), (1.53, True)],
)
def test_near_endpoint_out_of_range_quantization_band_is_explicit(
    speed: float, detectable: bool
) -> None:
    """Document which near-boundary slopes 2 FPS can distinguish reliably."""
    candidates = [
        CandidateMatch(
            index * 0.5,
            "EP01",
            round((200.0 + speed * index * 0.5) * 2) / 2,
            0.99,
        )
        for index in range(40)
    ]
    outcome = TemporalAligner(MatchingConfig()).align_with_diagnostics(
        candidates, index_complete=True
    )
    assert len(outcome.segments) == 1
    segment = outcome.segments[0]
    assert segment.review_tier == "REVIEW_REQUIRED"
    assert segment.unconstrained_speed_ratio is not None
    if detectable:
        assert segment.speed_ratio is None
        assert segment.speed_out_of_range is True
        assert segment.candidate_tier != "GREEN"
    else:
        # At 2 FPS these lines quantize into the configured endpoint bin. This is a
        # documented representation limit, never a production auto-release signal.
        assert segment.speed_ratio in (0.75, 1.50)
        assert segment.speed_out_of_range is False


def test_short_span_reports_null_speed_ratio() -> None:
    """Phase 2A.1 item 9: <3s of evidence span must not claim a measured speed."""
    aligner = TemporalAligner(MatchingConfig(minimum_segment_seconds=2.0))
    candidates = [
        CandidateMatch(index * 0.5, "EP01", 300.0 + 1.25 * index * 0.5, 0.99)
        for index in range(5)
    ]
    outcome = aligner.align_with_diagnostics(candidates, index_complete=True)
    assert outcome.segments
    assert outcome.segments[0].speed_ratio is None


def test_segment_cap_is_reported_instead_of_silently_truncating() -> None:
    """Phase 2A.1 item 11: reaching the per-episode cap must raise an explicit signal."""
    aligner = TemporalAligner(MatchingConfig(maximum_alignment_segments_per_episode=2))
    candidates: list[CandidateMatch] = []
    for block in range(5):
        base = block * 10.0
        candidates.extend(
            CandidateMatch(base + index * 0.5, "EP01", 100.0 + block * 60.0 + index * 0.5, 0.99)
            for index in range(12)
        )
    outcome = aligner.align_with_diagnostics(candidates, index_complete=True)
    assert outcome.truncated_episodes == ("EP01",)
    assert outcome.reason is not None and "segment cap" in outcome.reason
    assert outcome.warnings == ({
        "code": "ALIGNMENT_SEGMENT_CAP_REACHED",
        "message": "Per-episode alignment segment cap reached; output is truncated",
        "episodes": ["EP01"],
        "limit_per_episode": 2,
    },)
    assert outcome.segments
    assert all(segment.segment_cap_truncated for segment in outcome.segments)
    assert all(segment.candidate_tier != "GREEN" for segment in outcome.segments)
    assert all(segment.review_tier == "REVIEW_REQUIRED" for segment in outcome.segments)


def test_green_diagnostic_is_not_tautological_on_residual() -> None:
    """Phase 2A.1 item 8: residual_max <= 0.55 could never fail; RMS/support can."""
    aligner = TemporalAligner(MatchingConfig())
    clean = [CandidateMatch(index * 0.5, "EP01", 400.0 + index * 0.5, 0.99) for index in range(20)]
    clean_outcome = aligner.align_with_diagnostics(clean, index_complete=True)
    assert clean_outcome.segments[0].candidate_tier == "GREEN"

    noisy = [
        CandidateMatch(
            index * 0.5, "EP01", 400.0 + index * 0.5 + (0.5 if index % 2 else -0.5), 0.99
        )
        for index in range(20)
    ]
    noisy_outcome = aligner.align_with_diagnostics(noisy, index_complete=True)
    assert noisy_outcome.segments
    for segment in noisy_outcome.segments:
        # residual_max is bounded by the inlier tolerance, so it alone proves nothing
        assert segment.residual_max_seconds <= 0.5
        assert segment.candidate_tier != "GREEN"


def _grid_piecewise(specifications):
    matches = []
    truth = []
    accused_start = 0.0
    source_start = 100.0
    for speed, duration in specifications:
        truth.append((accused_start, accused_start + duration, source_start, source_start + speed * duration, speed))
        for index in range(int(duration * 2)):
            accused = accused_start + index * 0.5
            source = round((source_start + speed * index * 0.5) * 2) / 2
            matches.append(CandidateMatch(accused, "EP01", source, 0.99))
        accused_start += duration
        source_start += speed * duration
    return matches, truth


@pytest.mark.parametrize(
    "specifications",
    [
        [(0.8, 12.0), (1.25, 12.0)],
        [(1.25, 12.0), (0.8, 12.0)],
        [(1.0, 12.0), (1.25, 12.0)],
    ],
)
def test_change_point_refinement_recovers_both_source_mappings(specifications) -> None:
    matches, truth = _grid_piecewise(specifications)
    outcome = TemporalAligner(MatchingConfig()).align_with_diagnostics(
        matches, index_complete=True
    )
    assert len(outcome.segments) == 2
    for segment, (acc_start, acc_end, src_start, src_end, speed) in zip(
        outcome.segments, truth, strict=True
    ):
        assert segment.accused_start == pytest.approx(acc_start, abs=0.5)
        assert segment.accused_end == pytest.approx(acc_end, abs=0.5)
        assert segment.source_start == pytest.approx(src_start, abs=0.5)
        assert segment.source_end == pytest.approx(src_end, abs=0.5)
        assert segment.speed_ratio == pytest.approx(speed, abs=0.03)


def test_change_point_refinement_recovers_three_speed_piecewise_mapping() -> None:
    matches, truth = _grid_piecewise([(0.8, 8.0), (1.25, 8.0), (1.0, 8.0)])
    outcome = TemporalAligner(MatchingConfig()).align_with_diagnostics(
        matches, index_complete=True
    )
    assert len(outcome.segments) == 3
    for segment, (acc_start, acc_end, src_start, src_end, speed) in zip(
        outcome.segments, truth, strict=True
    ):
        assert segment.accused_start == pytest.approx(acc_start, abs=0.5)
        assert segment.accused_end == pytest.approx(acc_end, abs=0.5)
        assert segment.source_start == pytest.approx(src_start, abs=0.75)
        assert segment.source_end == pytest.approx(src_end, abs=0.75)
        assert segment.speed_ratio == pytest.approx(speed, abs=0.04)


def test_change_point_does_not_split_short_near_equal_slopes() -> None:
    matches, _ = _grid_piecewise([(1.0, 4.0), (1.05, 4.0)])
    outcome = TemporalAligner(MatchingConfig()).align_with_diagnostics(
        matches, index_complete=True
    )
    assert len(outcome.segments) == 1


def test_change_point_does_not_split_one_line_for_score_fluctuation() -> None:
    matches = [
        CandidateMatch(
            index * 0.5,
            "EP01",
            200.0 + index * 0.5,
            0.90 if index % 2 else 0.99,
        )
        for index in range(48)
    ]
    outcome = TemporalAligner(MatchingConfig()).align_with_diagnostics(
        matches, index_complete=True
    )
    assert len(outcome.segments) == 1


def test_temporal_line_compatibility_uses_mapping_not_raw_boundaries() -> None:
    aligner = TemporalAligner(MatchingConfig())
    same_left = aligner._proposal_to_segment(
        _Proposal("EP01", candidates("EP01", 0.0, 100.0, 16), 1.0)
    )
    same_right = aligner._proposal_to_segment(
        _Proposal("EP01", candidates("EP01", 4.0, 104.0, 16), 1.0)
    )
    assert same_left is not None and same_right is not None
    assert same_left.segment.source_start != same_right.segment.source_start
    assert aligner._same_temporal_line(same_left.segment, same_right.segment)

    diverging = [
        CandidateMatch(index * 0.5, "EP01", 100.0 + 1.25 * index * 0.5, 0.99)
        for index in range(24)
    ]
    diverging_fit = aligner._proposal_to_segment(_Proposal("EP01", diverging, 1.0))
    assert diverging_fit is not None
    assert not aligner._same_temporal_line(same_left.segment, diverging_fit.segment)

    cross_episode = aligner._proposal_to_segment(
        _Proposal("EP02", candidates("EP02", 0.0, 100.0, 16), 1.0)
    )
    assert cross_episode is not None
    assert not aligner._same_temporal_line(same_left.segment, cross_episode.segment)


def test_same_line_with_grid_jitter_does_not_create_artificial_ambiguity() -> None:
    aligner = TemporalAligner(MatchingConfig())
    jitter = (-0.5, 0.0, 0.5, 0.0)
    matches = [
        CandidateMatch(
            index * 0.5,
            "EP01",
            round((300.0 + index * 0.5) * 2) / 2 + jitter[index % 4],
            0.98,
        )
        for index in range(32)
    ]
    outcome = aligner.align_with_diagnostics(matches, index_complete=True)
    assert outcome.segments
    assert outcome.ambiguous is False


def test_same_episode_incompatible_full_support_line_is_a_real_rival() -> None:
    aligner = TemporalAligner(MatchingConfig())
    primary = candidates("EP01", 0.0, 100.0, 20)
    rival = candidates("EP01", 0.0, 500.0, 20)
    for index, item in enumerate(rival):
        rival[index] = CandidateMatch(
            item.accused_time, item.source_episode, item.source_time, 0.989
        )
    outcome = aligner.align_with_diagnostics(primary + rival, index_complete=True)
    assert outcome.ambiguous is True
    assert outcome.alternatives
    assert all(item.source_episode == "EP01" for item in outcome.alternatives)


def test_minimum_support_fragment_is_not_a_major_rival_to_full_explanation() -> None:
    aligner = TemporalAligner(MatchingConfig())
    primary = candidates("EP01", 0.0, 100.0, 20)
    fragment = [
        CandidateMatch(index * 0.5, "EP01", 500.0 + index * 0.5, 0.999)
        for index in range(6)
    ]
    outcome = aligner.align_with_diagnostics(primary + fragment, index_complete=True)
    assert outcome.ambiguous is False
    assert outcome.alternatives == []


def test_orphan_support_marks_confirmed_partial_boundary_incomplete() -> None:
    hits = (0, 1, *range(5, 12))
    matches = [
        CandidateMatch(index * 0.5, "EP01", 1867.5 + index * 0.5, 0.99)
        for index in hits
    ]
    outcome = TemporalAligner(MatchingConfig()).align_with_diagnostics(
        matches, index_complete=True
    )
    assert len(outcome.segments) == 1
    segment = outcome.segments[0]
    assert (segment.accused_start, segment.accused_end) == (2.5, 6.0)
    assert segment.boundary_completeness == "INCOMPLETE"
    assert "2 before start" in (segment.boundary_completeness_reason or "")
    assert segment.candidate_tier != "GREEN"
    assert segment.review_tier == "REVIEW_REQUIRED"


def test_source_reuse_survives_speed_change_refinement() -> None:
    matches, _ = _grid_piecewise([(0.8, 12.0), (1.25, 12.0)])
    matches.extend(candidates("EP01", 30.0, 100.0, 16))
    outcome = TemporalAligner(MatchingConfig()).align_with_diagnostics(
        matches, index_complete=True
    )
    assert len(outcome.segments) == 3
    reused = [segment for segment in outcome.segments if segment.source_reuse]
    assert len(reused) == 2
    assert all(segment.candidate_tier != "GREEN" for segment in reused)
