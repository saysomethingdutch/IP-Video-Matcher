from __future__ import annotations

import sqlite3
from pathlib import Path

from ipvm.database import Database, SCHEMA_VERSION
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
