from __future__ import annotations

import json
from dataclasses import replace

import numpy as np

from ipvm.benchmarking import _ffmpeg, _make_clip
from ipvm.config import MatchingConfig
from ipvm.database import Database
from ipvm.indexer import OriginalIndexer
from ipvm.matching import TemporalAligner, VideoMatcher
from ipvm.models import CandidateMatch, MatchStatus
from ipvm.retrieval_benchmark import RetrievalBenchmark


def _line(episode: str, accused: float, source: float, count: int, score: float = 0.99):
    return [
        CandidateMatch(accused + index * 0.5, episode, source + index * 0.5, score)
        for index in range(count)
    ]


def test_contradiction_marks_only_conflicting_segments_and_keeps_independent_fact() -> None:
    aligner = TemporalAligner(replace(
        MatchingConfig(),
        minimum_segment_seconds=3.0,
    ))
    candidates = _line("EP01", 0.0, 10.0, 8)
    candidates += _line("EP02", 2.0, 30.0, 8)
    candidates += _line("EP03", 8.0, 50.0, 8)
    outcome = aligner.align_with_diagnostics(candidates, index_complete=True)
    assert outcome.contradiction is True
    assert len(outcome.segments) == 3
    conflicting = [item for item in outcome.segments if item.conflict]
    independent = [item for item in outcome.segments if not item.conflict]
    assert {item.segment_id for item in conflicting} == {"S001", "S002"}
    assert len(independent) == 1
    assert independent[0].source_episode == "EP03"
    assert outcome.conflict_graph[0]["reason"]


def test_disjoint_accused_segments_may_reuse_the_same_source_interval() -> None:
    aligner = TemporalAligner(MatchingConfig(minimum_segment_seconds=3.0))
    candidates = _line("EP01", 0.0, 10.0, 8)
    candidates += _line("EP01", 8.0, 10.0, 8)
    outcome = aligner.align_with_diagnostics(candidates, index_complete=True)
    assert outcome.contradiction is False
    assert len(outcome.segments) == 2
    assert all(item.source_reuse for item in outcome.segments)
    assert all(item.candidate_tier == "YELLOW" for item in outcome.segments)
    assert all(item.review_tier == "REVIEW_REQUIRED" for item in outcome.segments)


def test_speed_ratio_is_null_when_effective_fit_span_is_under_three_seconds() -> None:
    aligner = TemporalAligner(MatchingConfig(minimum_segment_seconds=1.5))
    segment = aligner.align(_line("EP01", 0.0, 10.0, 4))[0]
    assert segment.speed_ratio is None


def test_independently_encoded_crf20_crf26_duplicate_is_ambiguous_and_persisted(
    app_config,
) -> None:
    app_config.paths.originals.mkdir(parents=True, exist_ok=True)
    first = app_config.paths.originals / "EP20.mp4"
    second = app_config.paths.originals / "EP26.mp4"
    for target, crf in ((first, 20), (second, 26)):
        _ffmpeg([
            "-f", "lavfi", "-i", "mandelbrot=size=320x180:rate=20",
            "-t", "9", "-an", "-c:v", "libx264", "-crf", str(crf),
            "-pix_fmt", "yuv420p", str(target),
        ])
    app_config.paths.workspace.mkdir(parents=True, exist_ok=True)
    query = app_config.paths.workspace / "crf-query.mp4"
    _make_clip(first, query, 1.337, 5.113)
    database = Database(app_config.paths.database)
    OriginalIndexer(app_config, database).index_all(force=True)
    for row in database.list_original_indexes(database.project_id(app_config.paths.root)):
        with np.load(row["index_path"]) as payload:
            identity = json.loads(str(payload["cache_identity_json"][0]))
        assert identity["source_sha256"]
        assert identity["encoder"]["name"] == "dct-low-frequency"
        assert identity["encoder"]["version"] == "1"
        assert identity["coarse_fps"] == 2.0
        assert identity["frame_width"] == 160
        assert identity["frame_height"] == 90
        assert identity["frame_extraction"]["colorspace"] == "gray8"
    result = VideoMatcher(app_config, database).analyze_file("CRF_DUPLICATE", query)
    assert result.status == MatchStatus.AMBIGUOUS.value
    assert result.review_tier == "REVIEW_REQUIRED"
    assert result.segments
    assert result.alternatives
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT is_primary, source_episode, review_tier FROM segment_matches "
            "WHERE case_id='CRF_DUPLICATE' ORDER BY is_primary DESC"
        ).fetchall()
    assert any(row["is_primary"] == 1 for row in rows)
    assert any(row["is_primary"] == 0 for row in rows)
    assert all(row["review_tier"] == "REVIEW_REQUIRED" for row in rows)

    retrieval = RetrievalBenchmark(app_config, database).run(
        samples_per_episode=1,
        query_duration_seconds=3.0,
        modes=("direct",),
        seed=7,
    )
    serialized = json.dumps(retrieval, allow_nan=False)
    assert retrieval["aggregate"]["retrieval_recall_at_k"]["128"] is not None
    assert retrieval["decision"] is None
    assert str(first) not in serialized and str(second) not in serialized
    assert "EP20.mp4" not in serialized and "EP26.mp4" not in serialized
