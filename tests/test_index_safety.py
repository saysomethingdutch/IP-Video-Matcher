from __future__ import annotations

import json

import numpy as np

from ipvm.audit import write_audit_result
from ipvm.database import Database
from ipvm.indexer import (
    episode_from_path,
    resolve_episode,
    validate_index_completeness,
)
from ipvm.matching import VideoMatcher
from ipvm.models import CandidateMatch, MatchStatus, MediaMetadata
from ipvm.project import initialize_project


def test_metadata_mapping_precedes_filename_and_ambiguous_name_needs_metadata(tmp_path) -> None:
    ambiguous = tmp_path / "rights-work-final.mp4"
    assert episode_from_path(ambiguous) is None
    assert resolve_episode(ambiguous, {ambiguous.name: "EP17"}) == "EP17"
    assert episode_from_path(tmp_path / "S01E03.mp4") == "S01E03"
    assert episode_from_path(tmp_path / "第07集.mp4") == "EP07"


def test_index_completeness_detects_truncated_coverage() -> None:
    complete = validate_index_completeness(20.0, 2.0, [index / 2 for index in range(40)])
    incomplete = validate_index_completeness(20.0, 2.0, [index / 2 for index in range(18)])
    assert complete["status"] == "COMPLETE"
    assert incomplete["status"] == "INCOMPLETE_INDEX"
    assert "coverage" in incomplete["reason"]


def test_matcher_refuses_incomplete_index_and_audit_records_reason(app_config) -> None:
    initialize_project(app_config)
    database = Database(app_config.paths.database)
    project_id = database.project_id(app_config.paths.root)
    database.update_project_index_status(
        project_id, "INCOMPLETE_INDEX", 1, 0, "frame coverage incomplete"
    )
    accused = app_config.paths.workspace / "missing-is-not-read.mp4"
    result = VideoMatcher(app_config, database).analyze_file("CASE_BLOCKED", accused)
    assert result.status == MatchStatus.INCOMPLETE_INDEX.value
    assert result.segments == []
    audit_path = write_audit_result(result, app_config.paths.output / "results")
    audit = json.loads(audit_path.read_text())
    assert audit["schema_version"] == "0.5"
    assert audit["status"] == "INCOMPLETE_INDEX"
    assert audit["index_completeness"]["reason"] == "frame coverage incomplete"
    assert "matching_config" in audit["matching"]
    assert "original_assets" in audit
    assert audit["matching"]["timestamp_basis"] == "nominal_grid_time"
    assert audit["matching"]["visual_algorithm_version"] == "test-v1"
    assert audit["matching"]["temporal_algorithm_version"] == "phase2a-temporal-v2"
    assert audit["matching"]["authoritative_media_timeline"] == {
        "basis": "media_pts",
        "status": "NOT_YET_REFINED",
        "legal_delivery_allowed": False,
    }


def test_segment_cap_warning_propagates_through_matcher_database_and_audit(
    app_config, monkeypatch
) -> None:
    initialize_project(app_config)
    database = Database(app_config.paths.database)
    project_id = database.project_id(app_config.paths.root)
    database.update_project_index_status(project_id, "COMPLETE", 0, 0)
    accused = app_config.paths.workspace / "cap-query.mp4"
    accused.write_bytes(b"synthetic-placeholder")

    metadata = MediaMetadata(accused, 50.0, 160, 90, 25.0, "h264", None, None, 0, None)
    monkeypatch.setattr("ipvm.matching.probe_media", lambda _: metadata)
    monkeypatch.setattr("ipvm.matching.sha256_file", lambda _: "digest")
    monkeypatch.setattr(
        "ipvm.matching.extract_grayscale_frames",
        lambda *args, **kwargs: [
            (index * 0.5, np.zeros((90, 160), dtype=np.uint8)) for index in range(100)
        ],
    )

    class FakeEncoder:
        name = "fake"

        def encode_images(self, images):
            materialized = list(images)
            return np.zeros((len(materialized), 1), dtype=np.float32)

    matcher = VideoMatcher(app_config, database, encoder=FakeEncoder())
    candidates = []
    for block in range(5):
        base = block * 10.0
        candidates.extend(
            CandidateMatch(
                base + index * 0.5,
                "EP01",
                100.0 + block * 60.0 + index * 0.5,
                0.99,
            )
            for index in range(12)
        )
    matcher.aligner = matcher.aligner.__class__(
        app_config.matching.__class__(
            **{
                **app_config.matching.__dict__,
                "maximum_alignment_segments_per_episode": 2,
            }
        )
    )
    matcher.retriever.retrieve = lambda *_: candidates

    result = matcher.analyze_file("CAP_CASE", accused, persist=True)
    assert result.truncated_episodes == ("EP01",)
    assert result.warnings[0]["code"] == "ALIGNMENT_SEGMENT_CAP_REACHED"
    assert all(segment.candidate_tier != "GREEN" for segment in result.segments)

    with database.connect() as connection:
        row = connection.execute(
            "SELECT truncated_episodes_json, analysis_warnings_json "
            "FROM accused_videos WHERE case_id='CAP_CASE'"
        ).fetchone()
    assert json.loads(row["truncated_episodes_json"]) == ["EP01"]
    assert json.loads(row["analysis_warnings_json"])[0]["code"] == (
        "ALIGNMENT_SEGMENT_CAP_REACHED"
    )

    audit = json.loads(
        write_audit_result(result, app_config.paths.output / "results").read_text()
    )
    assert audit["segment_cap_reached"] is True
    assert audit["truncated_episodes"] == ["EP01"]
    assert audit["warnings"][0]["code"] == "ALIGNMENT_SEGMENT_CAP_REACHED"
