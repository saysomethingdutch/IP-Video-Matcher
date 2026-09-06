from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path
import sys
import textwrap

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from experiments.phase2b5 import benchmark as b
from experiments.phase2b5.aligner import ExperimentalExtractionAligner
from experiments.phase2b5.policies import (
    INLIER_ONLY,
    LINE_BAND,
    POLICIES,
    ExtractionPolicy,
    choose_fit,
    geometry_key,
    near_tie_ordinals,
)
from experiments.phase2b5.reporting import validate_sanitized
from experiments.phase2b5.synthetic import FIXTURE_NAMES, build_fixture
from ipvm.config import MatchingConfig
from ipvm.matching import TemporalAligner, _FittedSegment
from ipvm.models import CandidateMatch, MatchStatus, SegmentMatch


@pytest.fixture(scope="module")
def synthetic_result() -> dict:
    return b.run_synthetic()


def _segment(**overrides: float) -> SegmentMatch:
    values = {
        "accused_start": 0.0,
        "accused_end": 8.0,
        "source_episode": "E",
        "source_start": 100.0,
        "source_end": 108.0,
        "speed_ratio": 1.0,
        "visual_score": 0.95,
        "audio_score": None,
        "temporal_score": 1.0,
        "match_score": 0.95,
        "residual_rms_seconds": 0.1,
        "inlier_count": 16,
        "inlier_ratio": 1.0,
    }
    values.update(overrides)
    return SegmentMatch(**values)


def _fit(**overrides: float) -> _FittedSegment:
    return _FittedSegment(_segment(**overrides), [])


def _row(result: dict, fixture: str, policy: str) -> dict:
    return next(
        item for item in result["rows"]
        if item["fixture"] == fixture and item["policy_id"] == policy
    )


def test_protocol_constants_and_exact_policy_matrix() -> None:
    assert b.PROTOCOL_COMMIT == "54531ce7e268b20c1292c9b3721b1307eb072835"
    assert [(p.policy_id, p.epsilon, p.occupancy_mode) for p in POLICIES] == [
        ("P0", None, LINE_BAND),
        ("P1", 0.0005, LINE_BAND),
        ("P2", 0.0010, LINE_BAND),
        ("P3", 0.0020, LINE_BAND),
        ("P4", None, INLIER_ONLY),
        ("P5", 0.0010, INLIER_ONLY),
        ("P6", 0.0020, INLIER_ONLY),
    ]


def test_near_tie_is_inclusive_qmax_relative_not_pairwise() -> None:
    assert near_tie_ordinals([1.0, 0.9995, 0.9994999], 0.0005) == (0, 1)
    # The third value is within epsilon of the second, but not of q_max.
    assert near_tie_ordinals([1.0, 0.9996, 0.9992], 0.0005) == (0, 1)
    with pytest.raises(ValueError):
        near_tie_ordinals([], 0.001)


def test_geometry_tuple_order_and_deterministic_tie_break() -> None:
    wider = _fit(accused_end=9.0)
    cleaner = _fit(residual_rms_seconds=0.05)
    assert geometry_key(cleaner, 0.9, 1) > geometry_key(wider, 0.9, 0)
    identical = [_fit(), _fit()]
    policy = ExtractionPolicy("TEST", 0.001, LINE_BAND)
    ordinal, _, trace = choose_fit(
        identical, policy, lambda _: 0.95, lambda _: (0.95,)
    )
    assert ordinal == 0
    assert trace["shortlist_ordinals"] == [0, 1]


def test_baseline_selection_replays_frozen_lexicographic_key() -> None:
    fits = [_fit(inlier_count=12), _fit(inlier_count=16)]
    ordinal, _, trace = choose_fit(
        fits, POLICIES[0], lambda _: 0.1, lambda item: (item.segment.inlier_count,)
    )
    assert ordinal == 1
    assert trace["shortlist_ordinals"] == [1]


def test_inlier_only_removes_exact_object_identity_not_value_equality() -> None:
    first = CandidateMatch(0.0, "E", 100.0, 0.95)
    equal_but_distinct = CandidateMatch(0.0, "E", 100.0, 0.95)
    other = CandidateMatch(0.5, "E", 100.5, 0.95)
    aligner = ExperimentalExtractionAligner(
        MatchingConfig(), POLICIES[4], [first, equal_but_distinct, other]
    )
    selected = _FittedSegment(_segment(), [first])
    kept, removed = aligner._inlier_occupancy(
        [first, equal_but_distinct, other], selected
    )
    assert removed == [0]
    assert kept[0] is equal_but_distinct
    assert kept[1] is other


def test_all_synthetic_candidates_are_fixed_top8_and_grid_aligned() -> None:
    for name in FIXTURE_NAMES:
        fixture = build_fixture(name)
        assert len(fixture.candidates) == 16 * 8
        per_frame: dict[float, int] = {}
        for candidate in fixture.candidates:
            per_frame[candidate.accused_time] = per_frame.get(candidate.accused_time, 0) + 1
            assert candidate.source_time * 2 == pytest.approx(round(candidate.source_time * 2))
        assert set(per_frame.values()) == {8}


def test_baseline_replay_and_extraction_loss_reproducer(synthetic_result: dict) -> None:
    assert synthetic_result["baseline_reproducer"]["valid"] is True
    assert all(
        item["production_replay_equal"]
        for item in synthetic_result["rows"] if item["policy_id"] == "P0"
    )
    loss = _row(synthetic_result, "EXTRACTION_LOSS", "P0")
    assert loss["trace"]["rounds"][0]["complete_proposal_count"] > 0
    assert loss["trace"]["rounds"][0]["selected_evaluator_complete"] is False
    assert loss["trace"]["complete_final_mapping_count"] == 0


@pytest.mark.parametrize("name", ["CLEAN_1_0X", "CLEAN_0_8X", "CLEAN_1_25X"])
def test_registered_clean_speed_baselines_remain_complete(
    synthetic_result: dict, name: str
) -> None:
    assert _row(synthetic_result, name, "P0")["trace"]["complete_final_mapping_count"] > 0


def test_rival_ambiguity_and_wrong_line_review_required(synthetic_result: dict) -> None:
    for policy in POLICIES:
        rival = _row(synthetic_result, "TWO_STRONG_RIVALS", policy.policy_id)
        assert rival["status"] == MatchStatus.AMBIGUOUS.value
        assert len(rival["predictions"]) + len(rival["alternatives"]) >= 2
        wrong = _row(synthetic_result, "COHERENT_WRONG_LINE", policy.policy_id)
        assert wrong["predictions"]
        assert all(item["review_tier"] == "REVIEW_REQUIRED" for item in wrong["predictions"])


def test_synthetic_eligibility_is_recomputed_from_all_registered_criteria(
    synthetic_result: dict,
) -> None:
    assert set(synthetic_result["policy_eligibility"]) == {f"P{i}" for i in range(1, 7)}
    for value in synthetic_result["policy_eligibility"].values():
        assert value["eligible"] == all(value["criteria"].values())


def test_eligibility_and_selection_rules_are_fixed() -> None:
    source = inspect.getsource(b._eligibility)
    for literal in ("139", "56", "25", "27", "10", "1.25"):
        assert literal in source
    summary = {
        "safe": 140,
        "unsafe": 50,
        "extraction_loss_count": 20,
        "extraction_loss_content_position_count": 8,
        "no_match": 20,
        "segment_inflation_ratio": 1.1,
    }
    better = dict(summary, safe=141)
    assert b._selection_key(better, "P4") < b._selection_key(summary, "P1")
    assert b._selection_key(summary, "P1") < b._selection_key(summary, "P4")


def test_gt_mutation_cannot_change_raw_experimental_mapping() -> None:
    fixture = build_fixture("EXTRACTION_LOSS")
    first, aligner_a, _ = b._run_one(list(fixture.candidates), POLICIES[4])
    mutated_truth = dict(
        fixture.truth,
        source_episode="MUTATED",
        source_start=-500.0,
        source_end=-492.0,
        speed_ratio=1.25,
    )
    second, aligner_b, _ = b._run_one(list(fixture.candidates), POLICIES[4])
    assert mutated_truth != fixture.truth
    assert b._json_contract(b._outcome_payload(first)) == b._json_contract(b._outcome_payload(second))
    assert b._json_contract(aligner_a.round_trace) == b._json_contract(aligner_b.round_trace)


def test_runtime_selection_and_occupancy_have_no_evaluator_leakage() -> None:
    forbidden = {
        "truth", "ground_truth", "expected_source", "expected_speed",
        "complete", "is_correct",
    }
    for function in (
        choose_fit,
        ExperimentalExtractionAligner._extract_episode_segments,
        ExperimentalExtractionAligner._line_band_occupancy,
        ExperimentalExtractionAligner._inlier_occupancy,
    ):
        tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        arguments = {
            item.arg
            for node in ast.walk(tree) if isinstance(node, ast.arguments)
            for item in (*node.posonlyargs, *node.args, *node.kwonlyargs)
        }
        assert not (forbidden & (names | arguments))


def test_fresh_artifact_absence_and_production_diff_guards(tmp_path: Path) -> None:
    b.verify_stage(b.ROOT / "workspace/phase2b3/dev.json")
    dev = tmp_path / "dev.json"
    dev.write_text("{}", encoding="utf-8")
    (tmp_path / "held-out.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="fresh/held-out"):
        b.verify_stage(dev)


def test_protocol_commit_precedes_implementation_and_is_protocol_only() -> None:
    assert b._git(
        "diff-tree", "--no-commit-id", "--name-only", "-r", b.PROTOCOL_COMMIT
    ) == "experiments/phase2b5/PROTOCOL.md"
    assert b._git("ls-tree", "-r", "--name-only", b.PROTOCOL_COMMIT, "experiments/phase2b5/aligner.py") == ""
    assert b._git("diff", b.PRODUCTION_COMMIT, "--", "src/", "config/") == ""


def test_phase2b5_workspace_is_ignored() -> None:
    result = b._git("check-ignore", "workspace/phase2b5/synthetic.json")
    assert result == "workspace/phase2b5/synthetic.json"


def test_sanitizer_rejects_timelines_candidates_paths_and_private_hashes() -> None:
    for payload in (
        {"source_time": 1.0},
        {"candidate_ids": [1]},
        {"truth": {}},
        {"note": "/Users/person/private.mp4"},
        {"fingerprints": {}},
        {"value": "a" * 64},
    ):
        with pytest.raises(ValueError):
            validate_sanitized(payload)
    validate_sanitized({"normalized_source_start": 0.5, "selected_slope": 1.0})


def test_committed_aggregate_records_a010_and_failed_selection() -> None:
    path = b.ROOT / "benchmark/visual/results/phase2b5-extraction-remediation-v0.1.json"
    if not path.exists():
        pytest.skip("aggregate is generated during the Phase 2B-5 artifact stage")
    aggregate = json.loads(path.read_text(encoding="utf-8"))
    h264 = aggregate["a010_controls"]["TAU_0.72"]
    assert h264["P0"]["H264_REENCODE"]["safe"] is False
    assert h264["P2"]["H264_REENCODE"]["safe"] is True
    assert h264["P4"]["H264_REENCODE"]["safe"] is False
    assert h264["P5"]["H264_REENCODE"]["safe"] is True
    assert aggregate["qualified_policies"] == []
    assert aggregate["selected_experimental_policy"] is None


def test_committed_aggregate_recomputes_all_dev_gates_and_negative_controls() -> None:
    path = b.ROOT / "benchmark/visual/results/phase2b5-extraction-remediation-v0.1.json"
    if not path.exists():
        pytest.skip("aggregate is generated during the Phase 2B-5 artifact stage")
    aggregate = json.loads(path.read_text(encoding="utf-8"))
    for policy_id, value in aggregate["eligibility"].items():
        assert value["eligible"] == all(value["criteria"].values())
        assert value["criteria"]["dino_segment_inflation"] is True
        assert value["criteria"]["dct_segment_inflation"] is True
        assert value["criteria"]["dino_negative_controls"] is True
        for backend in ("DCT", "TAU_0.72"):
            summary = aggregate["overall"][backend][policy_id]
            assert summary["negative_controls"]["emitted_segment_count"] == 0
            assert summary["unambiguous_wrong_source_green_case_count"] == 0
    assert aggregate["eligibility"]["P4"]["criteria"]["synthetic"] is True
    assert aggregate["eligibility"]["P4"]["criteria"]["a010_h264"] is False
    assert aggregate["eligibility"]["P5"]["criteria"]["dino_safe"] is True
    assert aggregate["eligibility"]["P5"]["criteria"]["dino_unsafe"] is True
    assert aggregate["eligibility"]["P5"]["criteria"]["dct_safe"] is True
    assert aggregate["eligibility"]["P5"]["criteria"]["dct_unsafe"] is True
    assert aggregate["eligibility"]["P5"]["criteria"]["synthetic"] is False
    assert aggregate["eligibility"]["P5"]["criteria"]["dino_extraction_loss_positions"] is False
