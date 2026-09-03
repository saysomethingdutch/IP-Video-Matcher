from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ipvm.database import Database, SCHEMA_VERSION
from ipvm.models import SegmentMatch
from ipvm.project import initialize_project


def test_phase_zero_schema_and_directories(app_config) -> None:
    created = initialize_project(app_config)
    assert created
    database = Database(app_config.paths.database)
    with database.connect() as connection:
        names = {
            row["name"] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    required = {
        "projects", "original_assets", "accused_videos", "evidence_videos",
        "uploaders", "frame_features", "transcripts", "segment_matches",
        "transformations", "content_segments", "semantic_assessments",
        "rule_results", "review_items", "exports",
    }
    assert required <= names


def test_schema_scopes_episode_uniqueness_by_project(app_config) -> None:
    database = Database(app_config.paths.database)
    first_id = database.initialize(app_config.paths.root)
    second_root = app_config.paths.root / "another-project"
    second_id = database.initialize(second_root)
    with database.connect() as connection:
        for project_id, suffix in ((first_id, "one"), (second_id, "two")):
            connection.execute(
                """
                INSERT INTO original_assets(
                    project_id, episode, file_path, file_size, sha256, duration,
                    media_metadata_json, index_status
                ) VALUES (?, 'EP01', ?, 1, ?, 1.0, '{}', 'COMPLETE')
                """,
                (project_id, suffix, suffix),
            )
        assert connection.execute(
            "SELECT COUNT(*) FROM original_assets WHERE episode='EP01'"
        ).fetchone()[0] == 2


def test_legacy_schema_migrates_match_score_and_project_scope(tmp_path) -> None:
    path = tmp_path / "legacy.db"
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE projects(id INTEGER PRIMARY KEY, root_path TEXT UNIQUE, created_at TEXT);
        CREATE TABLE original_assets(
            id INTEGER PRIMARY KEY, episode TEXT UNIQUE, file_path TEXT, file_size INTEGER,
            sha256 TEXT, duration REAL, media_metadata_json TEXT, index_path TEXT,
            algorithm_version TEXT, indexed_at TEXT
        );
        CREATE TABLE frame_features(
            id INTEGER PRIMARY KEY, original_asset_id INTEGER, timestamp REAL,
            frame_index INTEGER, feature_path TEXT, encoder TEXT
        );
        CREATE TABLE accused_videos(
            id INTEGER PRIMARY KEY, case_id TEXT UNIQUE, file_path TEXT, file_size INTEGER,
            sha256 TEXT, duration REAL, media_metadata_json TEXT, status TEXT, analyzed_at TEXT
        );
        CREATE TABLE segment_matches(
            id INTEGER PRIMARY KEY, case_id TEXT, accused_start REAL, accused_end REAL,
            source_episode TEXT, source_start REAL, source_end REAL, speed_ratio REAL,
            visual_score REAL, audio_score REAL, temporal_score REAL,
            overall_confidence REAL, mirrored INTEGER, cropped INTEGER,
            created_at TEXT, algorithm_version TEXT
        );
        INSERT INTO projects VALUES(1, '/legacy', 'now');
        INSERT INTO original_assets VALUES(
            1, 'EP01', '/legacy/EP01.mp4', 10, 'abc', 5.0, '{}', '/legacy/a.npz', 'v1', 'now'
        );
        INSERT INTO segment_matches VALUES(
            1, 'CASE', 0, 4, 'EP01', 1, 5, 1, .9, NULL, 1, .95, 0, 0, 'now', 'v1'
        );
    """)
    connection.commit()
    connection.close()
    database = Database(path)
    database.initialize(Path("/legacy"))
    with database.connect() as migrated:
        assert migrated.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        row = migrated.execute("SELECT match_score, mirrored, cropped FROM segment_matches").fetchone()
        assert row["match_score"] == 0.95
        assert row["mirrored"] is None and row["cropped"] is None
        assert migrated.execute("PRAGMA foreign_key_check").fetchall() == []


def test_migration_failure_rolls_back_without_half_migrated_schema(tmp_path, monkeypatch) -> None:
    path = tmp_path / "legacy-failure.db"
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE projects(id INTEGER PRIMARY KEY, root_path TEXT UNIQUE, created_at TEXT);
        CREATE TABLE original_assets(
            id INTEGER PRIMARY KEY, episode TEXT UNIQUE, file_path TEXT, file_size INTEGER,
            sha256 TEXT, duration REAL, media_metadata_json TEXT, index_path TEXT,
            algorithm_version TEXT, indexed_at TEXT
        );
        CREATE TABLE frame_features(
            id INTEGER PRIMARY KEY, original_asset_id INTEGER, timestamp REAL,
            frame_index INTEGER, feature_path TEXT, encoder TEXT
        );
        CREATE TABLE accused_videos(
            id INTEGER PRIMARY KEY, case_id TEXT UNIQUE, file_path TEXT, file_size INTEGER,
            sha256 TEXT, duration REAL, media_metadata_json TEXT, status TEXT, analyzed_at TEXT
        );
        CREATE TABLE segment_matches(
            id INTEGER PRIMARY KEY, case_id TEXT, accused_start REAL, accused_end REAL,
            source_episode TEXT, source_start REAL, source_end REAL, speed_ratio REAL,
            visual_score REAL, audio_score REAL, temporal_score REAL,
            overall_confidence REAL, mirrored INTEGER, cropped INTEGER,
            created_at TEXT, algorithm_version TEXT
        );
        INSERT INTO projects VALUES(1, '/legacy', 'now');
        INSERT INTO original_assets VALUES(
            1, 'EP01', '/legacy/EP01.mp4', 10, 'abc', 5.0, '{}', NULL, 'v1', 'now'
        );
    """)
    connection.commit()
    connection.close()

    database = Database(path)

    def fail_at_checkpoint(name: str) -> None:
        if name == "legacy_data_copied":
            raise RuntimeError("simulated migration failure")

    monkeypatch.setattr(database, "_migration_checkpoint", fail_at_checkpoint)
    try:
        database.initialize(Path("/legacy"))
    except RuntimeError as exc:
        assert "simulated" in str(exc)
    else:
        raise AssertionError("migration should fail")

    with sqlite3.connect(path) as rolled_back:
        columns = {
            row[1] for row in rolled_back.execute("PRAGMA table_info(original_assets)")
        }
        tables = {
            row[0] for row in rolled_back.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "project_id" not in columns
        assert not any(name.endswith("_v1") for name in tables)
        assert rolled_back.execute("SELECT COUNT(*) FROM original_assets").fetchone()[0] == 1
        assert rolled_back.execute("PRAGMA user_version").fetchone()[0] == 0
    assert path.with_suffix(path.suffix + ".pre-v4.bak").exists()


def test_temporal_v2_diagnostics_persist_with_timestamp_basis(app_config) -> None:
    database = Database(app_config.paths.database)
    project_id = database.initialize(app_config.paths.root)
    accused = app_config.paths.workspace / "query.mp4"
    accused.parent.mkdir(parents=True, exist_ok=True)
    accused.write_bytes(b"synthetic-test-placeholder")
    segment = SegmentMatch(
        0.0, 6.0, "EP01", 10.0, 17.5, 1.25, 0.99, None, 1.0, 0.99,
        temporal_residual_seconds=0.2,
        fitted_offset_seconds=10.0,
        residual_rms_seconds=0.1,
        residual_max_seconds=0.2,
        inlier_count=12,
        candidate_count=13,
        inlier_ratio=12 / 13,
        source_reuse=True,
        timestamp_basis="nominal_grid_time",
    )
    database.replace_case_result(
        project_id, "CASE", accused, None, None, "MATCH_FOUND", None, [segment]
    )
    with database.connect() as connection:
        row = connection.execute(
            "SELECT fitted_offset_seconds, residual_rms_seconds, residual_max_seconds, "
            "inlier_count, candidate_count, inlier_ratio, source_reuse, timestamp_basis "
            "FROM segment_matches WHERE case_id='CASE'"
        ).fetchone()
    assert row["fitted_offset_seconds"] == 10.0
    assert row["residual_rms_seconds"] == 0.1
    assert row["residual_max_seconds"] == 0.2
    assert row["inlier_count"] == 12 and row["candidate_count"] == 13
    assert row["source_reuse"] == 1
    assert row["timestamp_basis"] == "nominal_grid_time"


def test_v3_to_v4_migration_failure_rolls_back_all_added_columns(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "v3-failure.db"
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE original_assets(
            id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, episode TEXT NOT NULL,
            sha256 TEXT NOT NULL
        );
        CREATE TABLE segment_matches(
            id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, case_id TEXT NOT NULL,
            candidate_tier TEXT NOT NULL DEFAULT 'YELLOW'
        );
        PRAGMA user_version = 3;
    """)
    connection.commit()
    connection.close()

    database = Database(path)

    def fail_at_checkpoint(name: str) -> None:
        if name == "v4_diagnostics_added":
            raise RuntimeError("simulated v4 migration failure")

    monkeypatch.setattr(database, "_migration_checkpoint", fail_at_checkpoint)
    with pytest.raises(RuntimeError, match="simulated v4"):
        database.initialize(tmp_path)

    with sqlite3.connect(path) as rolled_back:
        columns = {
            row[1] for row in rolled_back.execute("PRAGMA table_info(segment_matches)")
        }
        assert "fitted_offset_seconds" not in columns
        assert "timestamp_basis" not in columns
        assert rolled_back.execute("PRAGMA user_version").fetchone()[0] == 3
        tables = {
            row[0] for row in rolled_back.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert tables == {"original_assets", "segment_matches"}
    assert path.with_suffix(path.suffix + ".pre-v4.bak").exists()
