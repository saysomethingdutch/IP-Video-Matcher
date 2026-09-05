from __future__ import annotations

import copy
import importlib
import sys
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
A = importlib.import_module("experiments.phase2b2.adapter")
B = importlib.import_module("experiments.phase2b2.benchmark")
R = importlib.import_module("experiments.phase2b2.reporting")
from ipvm.config import MatchingConfig
from ipvm.matching import TemporalAligner
from ipvm.models import CandidateMatch, SegmentMatch
from ipvm.benchmarking import evaluate_segments
from ipvm.temporal_benchmark import classify_case_outcome


def frames(score=.99, source="SOURCE_A", count=20):
    return [{"accused_time": i / 2, "top8": [{"accused_time": i / 2,
        "source_time": 100 + i / 2, "source_id": source, "rank": 1, "score": score}]}
        for i in range(count)]


def test_identity_raw_and_gt_independence():
    raw = frames()
    one = A.adapt_candidate_frames(raw, backend="DINO_RAW", policy=A.POLICY_GRID[0])
    altered = copy.deepcopy(raw)
    for f in altered:
        f.update(expected_source_time=-999, source_episode="POISON")
    two = A.adapt_candidate_frames(altered, backend="DINO_RAW", policy=A.POLICY_GRID[0])
    assert one == two
    assert one.audit[0] == {"accused_time": 0., "source_time": 100., "source_id": "SOURCE_A",
        "rank": 1, "raw_cosine": .99, "adapted_visual_score": .99, "retained": True}
    assert one.candidates[0] == CandidateMatch(0, "SOURCE_A", 100, .99)


@pytest.mark.parametrize("score,floor,expected", [(0.4,.45,None),(.45,.45,0),(.725,.45,.5),
                                                   (1,.7,1),(1.01,.7,1)])
def test_global_floor(score, floor, expected):
    value = A.adapt_dino_score(score, A.AdapterPolicy("probe", floor))
    assert value is None if expected is None else value == pytest.approx(expected)


def test_raw_clips_and_production_dct_mapping():
    assert A.adapt_dino_score(-.2, A.POLICY_GRID[0]) == 0
    assert A.adapt_dino_score(1.1, A.POLICY_GRID[0]) == 1
    assert A.adapt_dct_score(.64) == pytest.approx(.82)
    with pytest.raises(ValueError):
        A.adapt_dino_score(float("nan"), A.POLICY_GRID[0])


def test_below_floor_removed_without_renumbering():
    raw = frames(.9, count=1)
    raw[0]["top8"].append({"accused_time": 0, "source_time": 200,
        "source_id": "SOURCE_B", "rank": 2, "score": .4})
    stream = A.adapt_candidate_frames(raw, backend="DINO_RAW", policy=A.POLICY_GRID[1])
    assert len(stream.candidates) == 1
    assert stream.audit[1]["rank"] == 2 and stream.audit[1]["retained"] is False


def test_fixed_batch_16_and_natural_tail(monkeypatch):
    observed = []
    def batches(path, batch_size):
        assert batch_size == 16
        yield np.arange(16) / 2, np.zeros((16, 1, 1, 3))
        yield np.arange(16, 20) / 2, np.zeros((4, 1, 1, 3))
    class Encoder:
        def encode_images(self, images):
            observed.append(len(images))
            return np.ones((len(images), 384))
    monkeypatch.setattr(B.retrieval, "iter_rgb_batches", batches)
    times, emb, lengths = B.encode_query(Path("query"), Encoder())
    assert lengths == observed == [16, 4]
    assert len(times) == len(emb) == 20
    assert A.policy_metadata(A.POLICY_GRID[0])["dino_batch_size"] == 16


def test_grouped_split_balance_and_disjointness():
    for source in ("SOURCE_A", "SOURCE_B"):
        cal = {i for i in range(1, 11) if B.split(f"{source}-POSITION_{i:03}") == "CALIBRATION"}
        held = set(range(1, 11)) - cal
        assert cal == {1, 3, 5, 7, 9}
        assert held == {2, 4, 6, 8, 10} and not cal & held
    assert sum(B.split(f"NEGATIVE_{i:03}") == "CALIBRATION" for i in range(1, 21)) == 10


def selection_fixture():
    value = {"taxonomy": {"UNSAFE_WRONG_MATCH": 2}, "negatives": {"emitted": 0},
             "safe_success": {"numerator": 20}, "source_boundary_mae_seconds": .1,
             "accused_boundary_mae_seconds": .1}
    return {"stage": "CALIBRATION", "summaries": {n: copy.deepcopy(value)
            for n in ["DCT"] + [p.name for p in A.POLICY_GRID]}}


def test_selection_safety_coverage_tie_order_and_held_out_rejection():
    cal = selection_fixture()
    assert B.select_policy(cal)["selected_policy"] == "RAW"
    cal["summaries"]["RAW"]["negatives"]["emitted"] = 1
    assert B.select_policy(cal)["selected_policy"] == "FLOOR_0.45"
    cal["summaries"]["FLOOR_0.60"]["safe_success"]["numerator"] = 30
    assert B.select_policy(cal)["selected_policy"] == "FLOOR_0.60"
    cal["summaries"]["FLOOR_0.60"]["taxonomy"]["UNSAFE_WRONG_MATCH"] = 3
    assert B.select_policy(cal)["selected_policy"] == "FLOOR_0.45"
    cal["stage"] = "HELD_OUT"
    with pytest.raises(ValueError):
        B.select_policy(cal)


def test_taxonomy_primary_ambiguous_negative_and_wrong_timeline():
    truth = [{"source_episode": "A", "accused_start": 0, "accused_end": 10,
              "source_start": 100, "source_end": 110, "speed_ratio": 1}]
    p = SegmentMatch(0, 10, "A", 100, 110, 1, .99, None, 1, .99)
    m = evaluate_segments(truth, [p], "AMBIGUOUS")
    assert classify_case_outcome("AMBIGUOUS", m, truth, []) == "SAFE_AMBIGUITY_CONTAINS_GT"
    wrong = replace(p, source_start=200, source_end=210)
    m = evaluate_segments(truth, [wrong])
    assert classify_case_outcome("MATCH_FOUND", m, truth, []) == "UNSAFE_WRONG_MATCH"
    assert classify_case_outcome("AMBIGUOUS", m, truth, [p]) == "SAFE_AMBIGUITY_CONTAINS_GT"
    assert evaluate_segments([], [p])["false_positive_segments"] == 1
    assert classify_case_outcome("MATCH_NOT_FOUND", evaluate_segments(truth, []), truth, []) == "NO_MATCH"


@pytest.mark.parametrize("payload", [{"frames": []}, {"path": "hidden"},
    {"x": [{"source_start": 1}]}, {"x": "/Users/private"}, {"x": "a" * 64}])
def test_sanitization_rejects_private_raw(payload):
    with pytest.raises(ValueError):
        R.validate_sanitized(payload)


def test_protocol_commit_is_protocol_only():
    B.verify_protocol()
    assert len(B.PROTOCOL) == 40


def test_repeat_permutation_removal_and_competing_lines():
    raw = frames()
    stream = A.adapt_candidate_frames(raw, backend="DINO_RAW", policy=A.POLICY_GRID[0])
    permuted = A.adapt_candidate_frames(list(reversed(raw)), backend="DINO_RAW", policy=A.POLICY_GRID[0])
    assert stream == permuted
    aligner = TemporalAligner(MatchingConfig())
    first = aligner.align_with_diagnostics(list(stream.candidates), index_complete=True)
    second = aligner.align_with_diagnostics(list(permuted.candidates), index_complete=True)
    assert asdict(first) == asdict(second)
    assert first.segments[0].accused_start == 0
    removed = aligner.align_with_diagnostics(list(stream.candidates)[6:], index_complete=True)
    assert removed.segments[0].accused_start >= 3
    rival = [replace(c, source_episode="SOURCE_B") for c in stream.candidates]
    contested = aligner.align_with_diagnostics(list(stream.candidates) + rival, index_complete=True)
    assert contested.ambiguous and contested.alternatives
    assert all(p.review_tier == "REVIEW_REQUIRED" for p in contested.segments)


def test_frozen_out_of_range_boundary_and_cap_semantics():
    aligner = TemporalAligner(MatchingConfig())
    outside = [CandidateMatch(i / 2, "A", round((100 + .65 * i / 2) * 2) / 2, .99)
               for i in range(20)]
    outcome = aligner.align_with_diagnostics(outside, index_complete=True)
    assert outcome.segments
    assert any(p.speed_out_of_range and p.speed_ratio is None and p.unconstrained_speed_ratio is not None
               and p.candidate_tier != "GREEN" for p in outcome.segments)
    candidates = [CandidateMatch(i / 2, "A", 100 + i / 2, .99) for i in [0, 1, *range(6, 20)]]
    result = aligner.align_with_diagnostics(candidates, index_complete=True)
    assert result.segments[0].boundary_completeness == "INCOMPLETE"
    assert result.segments[0].candidate_tier != "GREEN"
    capped = TemporalAligner(replace(MatchingConfig(), maximum_alignment_segments_per_episode=1))
    result = capped.align_with_diagnostics(candidates, index_complete=True)
    assert result.truncated_episodes == ("A",) and result.warnings
    assert result.segments[0].segment_cap_truncated
    assert result.segments[0].candidate_tier != "GREEN"
