from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest
from PIL import Image


MODULE_PATH = Path(__file__).parents[1] / "experiments" / "phase2b1" / "benchmark.py"
SPEC = importlib.util.spec_from_file_location("phase2b1_benchmark", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
BENCHMARK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BENCHMARK)


def test_dino_preprocessing_is_deterministic_and_uses_registered_values() -> None:
    image = np.arange(224 * 224 * 3, dtype=np.uint8).reshape(224, 224, 3)
    first = BENCHMARK.preprocess_dino(image)
    second = BENCHMARK.preprocess_dino(image.copy())
    assert first.shape == (1, 3, 224, 224)
    assert first.dtype == np.float32
    np.testing.assert_array_equal(first, second)
    assert first[0, 0, 0, 0] == pytest.approx((0.0 - 0.485) / 0.229)


def test_dino_encoder_selects_cls_and_l2_normalizes_384_dimensions() -> None:
    class FakeSession:
        def run(self, outputs, inputs):
            batch = next(iter(inputs.values())).shape[0]
            hidden = np.zeros((batch, 257, 384), dtype=np.float32)
            hidden[:, 0, :] = np.arange(1, 385, dtype=np.float32)
            hidden[:, 1:, :] = 999.0
            return [hidden]

    encoder = BENCHMARK.DinoOnnxEncoder.__new__(BENCHMARK.DinoOnnxEncoder)
    encoder.session = FakeSession()
    encoder.input = type("Input", (), {"name": "pixel_values"})()
    encoder.output = type("Output", (), {"name": "last_hidden_state"})()
    result = encoder.encode_images(np.zeros((2, 224, 224, 3), dtype=np.uint8))
    assert result.shape == (2, 384)
    np.testing.assert_allclose(np.linalg.norm(result, axis=1), np.ones(2), atol=1e-6)
    np.testing.assert_array_equal(result[0], result[1])


def test_exact_cosine_topk_is_score_descending_and_index_stable_on_ties() -> None:
    indexes, scores = BENCHMARK.exact_cosine_topk(
        np.asarray([0.2, 0.9, 0.5, 0.9, -0.1], dtype=np.float32), 3
    )
    assert indexes.tolist() == [1, 3, 2]
    assert scores.tolist() == pytest.approx([0.9, 0.9, 0.5])


def _identity_index() -> dict[str, np.ndarray]:
    return {
        "source_alias": np.asarray(["SOURCE_A", "SOURCE_A", "SOURCE_B", "SOURCE_A"]),
        "source_time": np.asarray([10.0, 10.5, 10.0, 11.5], dtype=np.float32),
        "embedding": np.eye(4, dtype=np.float32),
    }


def test_source_time_identity_and_both_tolerances_are_preserved() -> None:
    case = {
        "case_id": "SOURCE_A-POSITION_001-DIRECT",
        "position_id": "SOURCE_A-POSITION_001",
        "source_alias": "SOURCE_A",
        "source_start_seconds": 10.0,
        "normalized_source_start": 0.1,
        "speed_ratio": 1.0,
        "transformation": "DIRECT",
    }
    query = np.asarray([[0.0, 0.0, 1.0, 0.0]], dtype=np.float32)
    row = BENCHMARK.evaluate_positive_case(
        case,
        "DCT",
        np.asarray([0.0], dtype=np.float32),
        query,
        _identity_index(),
    )
    frame = row["frames"][0]
    assert frame["top8"][0]["source_id"] == "SOURCE_B"
    assert frame["top8"][0]["source_time"] == 10.0
    assert frame["top8"][0]["accused_time"] == 0.0
    assert frame["hits_at_1"] == {"0.5": False, "1.0": False}
    assert frame["hits_at_8"] == {"0.5": True, "1.0": True}


def test_speed_ground_truth_mapping_and_registered_durations() -> None:
    assert BENCHMARK.map_source_time(100.0, 0.8, 5.0) == pytest.approx(104.0)
    assert BENCHMARK.map_source_time(100.0, 1.25, 5.0) == pytest.approx(106.25)
    slow = BENCHMARK.transformation_spec("SPEED_0_8X")
    fast = BENCHMARK.transformation_spec("SPEED_1_25X")
    assert slow["source_span_seconds"] / slow["speed_ratio"] == pytest.approx(10.0)
    assert fast["source_span_seconds"] / fast["speed_ratio"] == pytest.approx(6.4)


def test_position_usability_uses_primary_top8_frame_fraction() -> None:
    index = _identity_index()
    case = {
        "case_id": "SOURCE_A-POSITION_001-DIRECT",
        "position_id": "SOURCE_A-POSITION_001",
        "source_alias": "SOURCE_A",
        "source_start_seconds": 10.0,
        "normalized_source_start": 0.1,
        "speed_ratio": 1.0,
        "transformation": "DIRECT",
    }
    # Top-k includes all four source frames, so all three accused frames have a
    # primary-tolerance hit. The position is usable at all registered cutoffs.
    query = np.asarray([[1.0, 0.0, 0.0, 0.0]] * 3, dtype=np.float32)
    row = BENCHMARK.evaluate_positive_case(
        case,
        "DCT",
        np.asarray([0.0, 0.5, 1.0], dtype=np.float32),
        query,
        index,
    )
    assert row["rates"]["source_aware_recall_at_8_tolerance_1.0"] == 1.0
    assert row["position_usable"] == {"0.50": True, "0.75": True, "1.00": True}


def test_correct_rank_distribution_uses_structured_exhaustive_buckets() -> None:
    assert BENCHMARK._rank_buckets([1, 2, 8, 9, 32, 33, 128, 129, 9000, None]) == {
        "rank_1": 1,
        "rank_2_8": 2,
        "rank_9_32": 2,
        "rank_33_128": 2,
        "rank_129_plus": 2,
    }


def test_subtitle_definition_is_actual_raster_text(tmp_path: Path) -> None:
    overlay = tmp_path / "overlay.png"
    BENCHMARK.create_subtitle_overlay(overlay)
    image = np.asarray(Image.open(overlay).convert("RGBA"))
    spec = BENCHMARK.transformation_spec("SUBTITLE_OVERLAY")
    assert spec["overlay_kind"] == "actual_raster_text"
    assert len(np.unique(image.reshape(-1, 4), axis=0)) >= 4
    assert np.count_nonzero(image[:, :, 3]) > 10_000


def test_negative_characterization_has_no_false_positive_threshold() -> None:
    index = _identity_index()
    query = np.asarray([[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]], dtype=np.float32)
    result = BENCHMARK.evaluate_negative_case(
        {"case_id": "NEGATIVE_001"},
        "DCT",
        np.asarray([0.0, 0.5], dtype=np.float32),
        query,
        index,
    )
    assert result["query_frame_count"] == 2
    assert len(result["top1_scores"]) == 2
    assert 0.0 <= result["top1_source_concentration"] <= 1.0
    assert 0.0 <= result["top1_temporal_coherence"] <= 1.0
    assert "is_false_positive" not in result


def test_sanitized_output_validator_rejects_paths_hashes_and_frame_records() -> None:
    BENCHMARK.validate_sanitized_payload({"source_alias": "SOURCE_A", "rate": 0.5})
    private_media = "/" + "Users" + "/person/" + "real" + "_test/" + "EP" + "01.m" + "p4"
    with pytest.raises(ValueError, match="private media"):
        BENCHMARK.validate_sanitized_payload({"note": private_media})
    with pytest.raises(ValueError, match="forbidden keys"):
        BENCHMARK.validate_sanitized_payload({"sha256": "abc"})
    with pytest.raises(ValueError, match="forbidden keys"):
        BENCHMARK.validate_sanitized_payload({"frames": []})


def test_sanitized_aggregate_drops_raw_paths_timestamps_and_candidates() -> None:
    frame = {
        "episode_top1": True,
        "episode_top8": True,
        "hits_at_1": {"0.5": True, "1.0": True},
        "hits_at_8": {"0.5": True, "1.0": True},
        "first_correct_rank": {"0.5": 1, "1.0": 1},
        "true_candidate_score": 0.9,
        "best_wrong_candidate_score": 0.5,
        "score_margin": 0.4,
        "accused_time": 0.0,
        "expected_source_time": 123.456,
        "top8": [{"source_id": "SOURCE_A", "source_time": 123.5, "rank": 1}],
    }
    positives = []
    for backend in BENCHMARK.BACKENDS:
        for transformation in BENCHMARK.TRANSFORMATIONS:
            positives.append(
                {
                    "case_id": f"PRIVATE-{transformation}",
                    "position_id": "SOURCE_A-POSITION_001",
                    "source_alias": "SOURCE_A",
                    "source_start_seconds": 123.456,
                    "transformation": transformation,
                    "backend": backend,
                    "query_frame_count": 1,
                    "rates": {"source_aware_recall_at_8_tolerance_1.0": 1.0},
                    "position_usable": {"0.50": True, "0.75": True, "1.00": True},
                    "retrieval_elapsed_seconds": 0.01,
                    "frames": [frame],
                }
            )
    negatives = [
        {
            "case_id": "NEGATIVE_001",
            "backend": backend,
            "query_frame_count": 1,
            "top1_scores": [0.2],
            "top8_scores": [0.2],
            "top1_source_concentration": 1.0,
            "top1_temporal_coherence": 1.0,
            "retrieval_elapsed_seconds": 0.01,
            "frames": [],
        }
        for backend in BENCHMARK.BACKENDS
    ]
    raw = {
        "model": {"name": "dinov2-small-int8"},
        "index_metadata": {"runtime": {}},
        "positive_cases": positives,
        "negative_cases": negatives,
        "encoding_times": [{"dino_raw_plus_flip_seconds": 0.1, "dct_seconds": 0.01}],
        "runtime": {},
    }
    manifest = {
        "sources": [{"alias": "SOURCE_A", "path": "/private/source-video.bin"}],
        "positions": [
            {"position_id": "SOURCE_A-POSITION_001", "normalized_source_start": 0.1}
        ],
        "queries": [{} for _ in BENCHMARK.TRANSFORMATIONS],
    }
    aggregate = BENCHMARK.sanitized_aggregate(raw, manifest)
    serialized = __import__("json").dumps(aggregate)
    assert "/private/source-video.bin" not in serialized
    assert "123.456" not in serialized
    assert "expected_source_time" not in serialized
    assert aggregate["sample_scope"]["positive_query_count"] == len(BENCHMARK.TRANSFORMATIONS)
