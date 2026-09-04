from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).parents[1] / "experiments" / "phase2b0" / "bakeoff.py"
SPEC = importlib.util.spec_from_file_location("phase2b0_bakeoff", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
BAKEOFF = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BAKEOFF)


def test_speed_transformations_preserve_registered_mapping() -> None:
    slow = BAKEOFF.transformation_spec("SPEED_0_8X")
    fast = BAKEOFF.transformation_spec("SPEED_1_25X")
    assert slow["source_span_seconds"] / slow["speed_ratio"] == pytest.approx(10.0)
    assert fast["source_span_seconds"] / fast["speed_ratio"] == pytest.approx(6.4)
    assert "PTS/0.8" in slow["filter"]
    assert "PTS/1.25" in fast["filter"]


def test_eight_second_ai_partial_mode_changes_only_eligibility_not_hit_threshold() -> None:
    arguments = BAKEOFF.VDF_MODES["AI_PARTIAL_8S_ELIGIBLE"]
    assert arguments[arguments.index("--partial-clip-min-ratio") + 1] == "0.0"
    assert arguments[arguments.index("--ai-partial-hit-percent") + 1] == "89"


def test_vdf_group_interpretation_requires_unique_correct_source_and_partial_offset() -> None:
    groups = [
        {
            "Items": [
                {"Path": "/raw/SOURCE_A.mp4", "Flags": "None", "Similarity": 100},
                {
                    "Path": "/raw/QUERY.mp4",
                    "Flags": "PartialClip, AiMatched",
                    "Similarity": 96.5,
                    "PartialClipOffset": "00:01:40",
                },
            ]
        }
    ]
    result = BAKEOFF.interpret_vdf_output(
        groups,
        expected_source_alias="SOURCE_A",
        expected_offset_seconds=100.5,
        source_aliases={"SOURCE_A", "SOURCE_B"},
    )
    assert result["episode_correct"] is True
    assert result["temporally_usable"] is True
    assert result["offset_correct"] is True
    assert result["false_source_match"] is False

    groups[0]["Items"].append({"Path": "/raw/SOURCE_B.mp4", "Flags": "None"})
    ambiguous = BAKEOFF.interpret_vdf_output(
        groups,
        expected_source_alias="SOURCE_A",
        expected_offset_seconds=100.5,
        source_aliases={"SOURCE_A", "SOURCE_B"},
    )
    assert ambiguous["episode_correct"] is False
    assert ambiguous["false_source_match"] is True


def test_vdf_detection_without_partial_flag_is_not_temporally_usable() -> None:
    groups = [
        {
            "Items": [
                {"Path": "/raw/SOURCE_A.mp4", "Flags": "None"},
                {
                    "Path": "/raw/QUERY.mp4",
                    "Flags": "AiMatched",
                    "Similarity": 95.0,
                    "PartialClipOffset": "00:00:00",
                },
            ]
        }
    ]
    result = BAKEOFF.interpret_vdf_output(
        groups,
        expected_source_alias="SOURCE_A",
        expected_offset_seconds=0.0,
        source_aliases={"SOURCE_A", "SOURCE_B"},
    )
    assert result["detected"] is True
    assert result["episode_correct"] is True
    assert result["temporally_usable"] is False
    assert result["offset_seconds"] is None


def test_sanitized_aggregate_omits_paths_and_exact_ground_truth() -> None:
    manifest = {
        "sources": [{"alias": "SOURCE_A", "path": "/private/source.mp4"}],
        "queries": [],
    }
    dct = {
        "cases": [
            {
                "case_id": "SOURCE_A-POSITION_001-DIRECT",
                "position_id": "SOURCE_A-POSITION_001",
                "transformation": "DIRECT",
                "episode_correct": True,
                "temporally_usable": True,
                "offset_correct": True,
                "false_source_match": False,
                "source_start_error_seconds": 0.1,
                "runtime_seconds": 0.2,
                "query_frame_count": 16,
                "correct_source_top1_frame_count": 15,
                "correct_source_top8_frame_count": 16,
                "source_position_usable_frame_count": 16,
                "correct_source_rank_median": 1.0,
                "status": "MATCH_FOUND",
            }
        ]
    }
    vdf = {"cases": []}
    aggregate = BAKEOFF.sanitized_aggregate(manifest, dct, vdf)
    serialized = __import__("json").dumps(aggregate)
    assert "/private/source.mp4" not in serialized
    assert "source_start_seconds" not in serialized
    assert aggregate["sample_scope"]["independent_position_count"] == 1
    direct = aggregate["by_transformation"]["DIRECT"]["dct"]
    assert direct["retrieval"]["correct_source_top1_frames"]["numerator"] == 15
    assert direct["final_status_distribution"] == {"MATCH_FOUND": 1}


def test_vdf_report_merge_rejects_duplicate_case_mode() -> None:
    report = {
        "vdf_modes": {"AI_PARTIAL_DEFAULT": ["--ai-partial"]},
        "strict_offset_tolerance_seconds": 1.0,
        "coarse_offset_tolerance_seconds": 15.0,
        "cases": [{"case_id": "CASE_001", "mode": "AI_PARTIAL_DEFAULT"}],
    }
    with pytest.raises(ValueError, match="duplicate VDF case/mode"):
        BAKEOFF.merge_vdf_reports([report, report])


def test_vdf_report_merge_preserves_distinct_modes() -> None:
    common = {
        "strict_offset_tolerance_seconds": 1.0,
        "coarse_offset_tolerance_seconds": 15.0,
    }
    merged = BAKEOFF.merge_vdf_reports(
        [
            {
                **common,
                "vdf_modes": {"CLASSIC_DEFAULT": ["--percent", "96"]},
                "cases": [{"case_id": "CASE_001", "mode": "CLASSIC_DEFAULT"}],
            },
            {
                **common,
                "vdf_modes": {"AI_PARTIAL_DEFAULT": ["--ai-partial"]},
                "cases": [{"case_id": "CASE_001", "mode": "AI_PARTIAL_DEFAULT"}],
            },
        ]
    )
    assert set(merged["vdf_modes"]) == {"CLASSIC_DEFAULT", "AI_PARTIAL_DEFAULT"}
    assert len(merged["cases"]) == 2
