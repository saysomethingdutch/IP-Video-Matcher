from __future__ import annotations

import json

from ipvm.audit import write_audit_result
from ipvm.database import Database
from ipvm.indexer import (
    episode_from_path,
    resolve_episode,
    validate_index_completeness,
)
from ipvm.matching import VideoMatcher
from ipvm.models import MatchStatus
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
    assert audit["status"] == "INCOMPLETE_INDEX"
    assert audit["index_completeness"]["reason"] == "frame coverage incomplete"
    assert "matching_config" in audit["matching"]
    assert "original_assets" in audit
