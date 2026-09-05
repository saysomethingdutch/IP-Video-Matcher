from __future__ import annotations

import argparse
import copy
from dataclasses import asdict
import json
from pathlib import Path

import numpy as np
import pytest

from experiments.phase2b3 import adapter as a
from experiments.phase2b3 import benchmark as b
from experiments.phase2b3 import reporting as r
from ipvm.config import MatchingConfig
from ipvm.matching import TemporalAligner
from ipvm.models import CandidateMatch


def frames(score: float = 0.9, count: int = 20) -> list[dict]:
    return [
        {
            "accused_time": index / 2,
            "top8": [
                {
                    "accused_time": index / 2,
                    "source_time": 100 + index / 2 + rank / 2,
                    "source_id": "SOURCE_A" if rank == 0 else "SOURCE_B",
                    "rank": rank + 1,
                    "score": score - rank / 100,
                }
                for rank in range(8)
            ],
        }
        for index in range(count)
    ]


def test_protocol_constants_and_protocol_only_commit() -> None:
    assert b.FRESH_SEED == 20260905
    assert b.FRESH_POSITIONS_PER_SOURCE == 5
    assert b.FRESH_SPAN_SECONDS == 8.0
    assert b.EXCLUSION_SECONDS == 30.0
    assert a.DINO_BATCH_SIZE == 16
    assert [policy.tau for policy in a.POLICY_GRID] == [0.55, 0.60, 0.64, 0.68, 0.72, 0.76, 0.80, 0.82]
    b.verify_protocol()


@pytest.mark.parametrize("tau", [0.55, 0.60, 0.64, 0.68, 0.72, 0.76, 0.80, 0.82])
def test_anchored_affine_endpoints_monotonicity_and_clipping(tau: float) -> None:
    policy = a.ScorePolicy("probe", tau)
    assert a.anchored_affine_score(tau, policy) == pytest.approx(0.82)
    assert a.anchored_affine_score(1.0, policy) == pytest.approx(1.0)
    samples = np.linspace(-1.0, 1.2, 100)
    mapped = [a.anchored_affine_score(value, policy) for value in samples]
    assert mapped == sorted(mapped)
    assert a.anchored_affine_score(-10.0, policy) == 0.0
    assert a.anchored_affine_score(2.0, policy) == 1.0


def test_special_case_dct_affine_and_raw_identity() -> None:
    for score in np.linspace(-1.0, 1.0, 101):
        assert a.anchored_affine_score(score, a.POLICY_BY_NAME["TAU_0.64"]) == pytest.approx(
            (score + 1.0) / 2.0
        )
    for score in np.linspace(0.0, 1.0, 101):
        assert a.anchored_affine_score(score, a.POLICY_BY_NAME["TAU_0.82"]) == pytest.approx(score)


def test_all_fixed_top8_candidate_identities_preserved_across_policies() -> None:
    identities = []
    for policy in a.POLICY_GRID:
        stream = a.adapt_candidate_frames(frames(), policy)
        assert len(stream.candidates) == 160
        assert all(item["retained"] is True for item in stream.audit)
        identities.append(a.candidate_identity(stream.audit))
    assert all(identity == identities[0] for identity in identities)


def test_adapter_rejects_incomplete_or_duplicate_top8() -> None:
    incomplete = frames(count=1)
    incomplete[0]["top8"].pop()
    with pytest.raises(ValueError, match="complete fixed top-8"):
        a.adapt_candidate_frames(incomplete, a.POLICY_GRID[0])
    duplicate = frames(count=1)
    duplicate[0]["top8"][1]["source_time"] = duplicate[0]["top8"][0]["source_time"]
    duplicate[0]["top8"][1]["source_id"] = duplicate[0]["top8"][0]["source_id"]
    with pytest.raises(ValueError, match="duplicate"):
        a.adapt_candidate_frames(duplicate, a.POLICY_GRID[0])


def test_gt_independence_and_permutation_determinism() -> None:
    original = frames()
    first = a.adapt_candidate_frames(original, a.POLICY_GRID[2])
    changed = copy.deepcopy(list(reversed(original)))
    for frame in changed:
        frame["ground_truth"] = "POISON"
        frame["top8"].reverse()
    second = a.adapt_candidate_frames(changed, a.POLICY_GRID[2])
    assert first == second
    aligner = TemporalAligner(MatchingConfig())
    one = aligner.align_with_diagnostics(list(first.candidates), index_complete=True)
    two = aligner.align_with_diagnostics(list(reversed(first.candidates)), index_complete=True)
    assert asdict(one) == asdict(two)


def test_fixed_batch_size_16_and_natural_tail(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: list[int] = []

    def batches(path: Path, batch_size: int):
        assert batch_size == 16
        yield np.arange(16) / 2, np.zeros((16, 1, 1, 3))
        yield np.arange(16, 20) / 2, np.zeros((4, 1, 1, 3))

    class Encoder:
        def encode_images(self, images: np.ndarray) -> np.ndarray:
            observed.append(len(images))
            return np.ones((len(images), 384))

    monkeypatch.setattr(b.phase2b2.retrieval, "iter_rgb_batches", batches)
    times, embeddings, lengths = b.phase2b2.encode_query(Path("query"), Encoder())
    assert lengths == observed == [16, 4]
    assert len(times) == len(embeddings) == 20
    assert a.policy_metadata(a.POLICY_GRID[0])["dino_batch_size"] == 16


def synthetic_old_corpus() -> dict:
    sources = [
        {"alias": "SOURCE_A", "duration_seconds": 1800.0},
        {"alias": "SOURCE_B", "duration_seconds": 1700.0},
    ]
    positions = []
    for source in sources:
        for ordinal, start in enumerate((100, 250, 400, 550, 700, 850, 1000, 1150, 1300, 1450), 1):
            positions.append(
                {
                    "position_id": f"{source['alias']}-POSITION_{ordinal:03d}",
                    "source_alias": source["alias"],
                    "source_start_seconds": float(start),
                }
            )
    return {"sources": sources, "positions": positions}


def test_deterministic_fresh_selection_exclusion_and_spacing() -> None:
    old = synthetic_old_corpus()
    first = b.select_fresh_positions(old)
    second = b.select_fresh_positions(copy.deepcopy(old))
    assert first == second and len(first) == 10
    for source in ("SOURCE_A", "SOURCE_B"):
        old_starts = [item["source_start_seconds"] for item in old["positions"] if item["source_alias"] == source]
        starts = sorted(item["source_start_seconds"] for item in first if item["source_alias"] == source)
        assert all(abs(fresh - prior) >= 38.0 - 1e-6 for fresh in starts for prior in old_starts)
        assert all(right - left >= 38.0 - 1e-6 for left, right in zip(starts, starts[1:]))


def test_held_out_generation_forbidden_before_freeze(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(b, "_assert_workspace", lambda output: output)
    args = argparse.Namespace(output=tmp_path)
    with pytest.raises(ValueError, match="forbidden before score policy freeze"):
        b.verify_selected(args)


def test_dev_rerun_forbidden_after_freeze(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(b, "_assert_workspace", lambda output: output)
    (tmp_path / "selected-score-contract.json").write_text("{}")
    args = argparse.Namespace(output=tmp_path)
    with pytest.raises(ValueError, match="DEV rerun forbidden"):
        b.ensure_dev_lock(args)


def test_held_out_policy_is_exactly_one_selected_policy() -> None:
    selected = {"selection_source": "DEV_ONLY", "fresh_metrics_used": False, "selected_policy": "TAU_0.68"}
    assert b.held_out_policies(selected) == (a.POLICY_BY_NAME["TAU_0.68"],)
    selected["selected_policy"] = "UNREGISTERED"
    with pytest.raises(ValueError, match="absent from the registered grid"):
        b.held_out_policies(selected)


def metric(safe: int = 50, unsafe: int = 20, no_match: int = 10) -> dict:
    return {
        "taxonomy": {
            "CORRECT_UNAMBIGUOUS_MATCH": safe,
            "SAFE_AMBIGUITY_CONTAINS_GT": 0,
            "UNSAFE_WRONG_MATCH": unsafe,
            "NO_MATCH": no_match,
        },
        "safe_success": {"numerator": safe, "denominator": safe + unsafe + no_match, "rate": None},
        "negatives": {"emitted": 0},
        "source_boundary_mae_seconds": 0.2,
        "accused_boundary_mae_seconds": 0.2,
    }


def selection_fixture() -> dict:
    names = ["DCT", *(policy.name for policy in a.POLICY_GRID)]
    transforms = {
        name: {transform: metric(safe=10, unsafe=3, no_match=7) for transform in b.retrieval.TRANSFORMATIONS}
        for name in names
    }
    cases = []
    for name in names:
        for transform in b.A010_TRANSFORMS:
            cases.append(
                {
                    "backend": name,
                    "case_id": f"SOURCE_A-POSITION_010-{transform}",
                    "outcome": "CORRECT_UNAMBIGUOUS_MATCH",
                    "status": "MATCH_FOUND",
                    "first_primary_wrong_source": False,
                    "correct_complete_alternative": False,
                    "wrong_primary_but_safe_alternative": False,
                    "retrieval_attribution_detail": {"actual_supported_frame_count": 16},
                    "post_threshold_support": {"actual_supported_frame_count": 16},
                    "raw_wrong_source_line": {"actual_frame_count": 0},
                    "effective_wrong_source_line": {"actual_frame_count": 0},
                    "split": "DEV",
                }
            )
    summaries = {name: metric() for name in names}
    # Make all DINO policies satisfy the fixed +4 geometry requirement.
    for name in names[1:]:
        for transform in ("MIRROR", "CENTER_CROP", "VERTICAL_9_16"):
            transforms[name][transform] = metric(safe=14, unsafe=3, no_match=3)
    return {"stage": "DEV", "summaries": summaries, "by_transformation": transforms, "cases": cases}


def test_selection_eligibility_coverage_negative_safety_and_lexicographic_order() -> None:
    dev = selection_fixture()
    # Exact ties choose the higher tau.
    assert b.select_policy(dev)["selected_policy"] == "TAU_0.82"
    # More safe coverage dominates the higher-tau tie break.
    dev["summaries"]["TAU_0.68"]["safe_success"]["numerator"] = 51
    assert b.select_policy(dev)["selected_policy"] == "TAU_0.68"
    # Abstention cannot win: falling below DCT safe coverage makes it ineligible.
    dev["summaries"]["TAU_0.68"]["safe_success"]["numerator"] = 49
    assert not b.policy_eligibility(dev)["TAU_0.68"]["eligible"]
    # A coherent DEV negative is an absolute ineligibility condition.
    dev["summaries"]["TAU_0.72"]["negatives"]["emitted"] = 1
    assert not b.policy_eligibility(dev)["TAU_0.72"]["eligible"]


def test_a010_rival_regression_rule() -> None:
    dev = selection_fixture()
    rows = [row for row in dev["cases"] if row["backend"] == "TAU_0.64"]
    rows[0].update(
        outcome="UNSAFE_WRONG_MATCH",
        first_primary_wrong_source=True,
        correct_complete_alternative=False,
    )
    assert not b.a010_regression(dev["cases"], "TAU_0.64")["passed"]
    rows[0].update(
        outcome="SAFE_AMBIGUITY_CONTAINS_GT",
        status="AMBIGUOUS",
        correct_complete_alternative=True,
        wrong_primary_but_safe_alternative=True,
    )
    assert b.a010_regression(dev["cases"], "TAU_0.64")["passed"]


def test_no_eligible_policy_stops_selection_when_a010_fails_for_every_tau() -> None:
    dev = selection_fixture()
    for policy in a.POLICY_GRID:
        row = next(
            item
            for item in dev["cases"]
            if item["backend"] == policy.name
            and item["case_id"] == "SOURCE_A-POSITION_010-H264_REENCODE"
        )
        row.update(
            outcome="UNSAFE_WRONG_MATCH",
            status="AMBIGUOUS",
            first_primary_wrong_source=True,
            correct_complete_alternative=False,
        )
    selection = b.select_policy(dev)
    assert selection["selected_policy"] is None
    assert selection["eligible_policies"] == []
    assert all(
        not result["criteria"]["a010_rival_preservation"]
        for result in selection["eligibility"].values()
    )


def test_taxonomy_counts_are_mutually_exclusive() -> None:
    values = [
        "CORRECT_UNAMBIGUOUS_MATCH",
        "SAFE_AMBIGUITY_CONTAINS_GT",
        "UNSAFE_WRONG_MATCH",
        "NO_MATCH",
        "NO_MATCH",
    ]
    counts = {name: values.count(name) for name in b.CASE_OUTCOMES}
    assert sum(counts.values()) == len(values)


@pytest.mark.parametrize(
    "payload",
    [
        {"frames": []},
        {"path": "hidden"},
        {"nested": [{"source_start": 1.0}]},
        {"value": "/Users/private"},
        {"value": "a" * 64},
        {"value": "clip.mp4"},
    ],
)
def test_sanitization_rejects_private_raw_material(payload: dict) -> None:
    with pytest.raises(ValueError):
        r.validate_sanitized(payload)


def test_negative_parameters_are_new_and_deterministic() -> None:
    current = [b.fresh_negative_parameters(index) for index in range(10)]
    assert current == [b.fresh_negative_parameters(index) for index in range(10)]
    signatures = {(item["hue_degrees"], item["grid_pixels"], item["saturation"]) for item in current}
    old = {((index * 37) % 360, 64 + (index % 5) * 16, round(0.7 + (index % 4) * 0.15, 2)) for index in range(20)}
    assert len(signatures) == 10 and not signatures & old


def test_correct_band_removal_and_competing_line_safety() -> None:
    aligner = TemporalAligner(MatchingConfig())
    line_a = [CandidateMatch(index / 2, "A", 100 + index / 2, 0.99) for index in range(20)]
    line_b = [CandidateMatch(index / 2, "B", 200 + index / 2, 0.99) for index in range(20)]
    full = aligner.align_with_diagnostics(line_a, index_complete=True)
    reduced = aligner.align_with_diagnostics(line_a[6:], index_complete=True)
    assert sum(item.accused_end - item.accused_start for item in reduced.segments) < sum(
        item.accused_end - item.accused_start for item in full.segments
    )
    contested = aligner.align_with_diagnostics(line_a + line_b, index_complete=True)
    assert contested.ambiguous and contested.alternatives
    wrong = aligner.align_with_diagnostics(line_b, index_complete=True)
    assert wrong.segments and all(item.review_tier == "REVIEW_REQUIRED" for item in wrong.segments)
