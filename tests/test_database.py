from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from ipvm.database import REQUIRED_COLUMNS, SCHEMA_VERSION, Database, SchemaIntegrityError
from ipvm.models import MediaMetadata, SegmentMatch
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
    assert path.with_suffix(path.suffix + f".pre-v{SCHEMA_VERSION}.bak").exists()


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
    assert path.with_suffix(path.suffix + f".pre-v{SCHEMA_VERSION}.bak").exists()


def _build_genuine_v4_fixture(tmp_path):
    """A v4 database with the real v4 shape, built without the current code path."""
    path = tmp_path / "genuine-v4.db"
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE projects(
            id INTEGER PRIMARY KEY, root_path TEXT NOT NULL UNIQUE,
            index_status TEXT NOT NULL DEFAULT 'NOT_INDEXED',
            source_file_count INTEGER NOT NULL DEFAULT 0,
            indexed_asset_count INTEGER NOT NULL DEFAULT 0,
            index_reason TEXT, last_indexed_at TEXT, created_at TEXT NOT NULL);
        CREATE TABLE original_assets(
            id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, episode TEXT NOT NULL,
            file_path TEXT NOT NULL, file_size INTEGER NOT NULL, sha256 TEXT NOT NULL,
            duration REAL NOT NULL, media_metadata_json TEXT NOT NULL, index_path TEXT,
            index_status TEXT NOT NULL DEFAULT 'NOT_INDEXED', expected_frame_count INTEGER,
            actual_frame_count INTEGER, timestamp_coverage REAL, completeness_reason TEXT,
            encoder TEXT, algorithm_version TEXT, indexed_at TEXT,
            UNIQUE(project_id, episode));
        CREATE TABLE accused_videos(
            id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, case_id TEXT NOT NULL,
            file_path TEXT NOT NULL, file_size INTEGER, sha256 TEXT, duration REAL,
            media_metadata_json TEXT, status TEXT NOT NULL, reason TEXT, analyzed_at TEXT,
            UNIQUE(project_id, case_id));
        CREATE TABLE segment_matches(
            id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, case_id TEXT NOT NULL,
            candidate_rank INTEGER NOT NULL DEFAULT 1, is_primary INTEGER NOT NULL DEFAULT 1,
            accused_start REAL NOT NULL, accused_end REAL NOT NULL,
            source_episode TEXT NOT NULL, source_start REAL NOT NULL, source_end REAL NOT NULL,
            speed_ratio REAL, visual_score REAL NOT NULL, audio_score REAL,
            temporal_score REAL NOT NULL, match_score REAL NOT NULL,
            temporal_residual_seconds REAL, fitted_offset_seconds REAL,
            residual_rms_seconds REAL NOT NULL DEFAULT 0,
            residual_max_seconds REAL NOT NULL DEFAULT 0,
            inlier_count INTEGER NOT NULL DEFAULT 0,
            candidate_count INTEGER NOT NULL DEFAULT 0,
            inlier_ratio REAL NOT NULL DEFAULT 0, coverage REAL, uniqueness_margin REAL,
            candidate_tier TEXT NOT NULL DEFAULT 'YELLOW', review_tier TEXT NOT NULL,
            segment_id TEXT, conflict INTEGER NOT NULL DEFAULT 0,
            conflict_with_json TEXT NOT NULL DEFAULT '[]', conflict_reason TEXT,
            source_reuse INTEGER NOT NULL DEFAULT 0,
            timestamp_basis TEXT NOT NULL DEFAULT 'nominal_grid_time',
            mirrored INTEGER, cropped INTEGER, created_at TEXT NOT NULL,
            algorithm_version TEXT NOT NULL);
        CREATE TABLE frame_features(
            id INTEGER PRIMARY KEY, original_asset_id INTEGER NOT NULL, timestamp REAL NOT NULL,
            frame_index INTEGER NOT NULL, feature_path TEXT NOT NULL, encoder TEXT NOT NULL);
        PRAGMA user_version = 4;
    """)
    connection.execute(
        "INSERT INTO projects(root_path, created_at) VALUES (?, ?)", (str(tmp_path), "2026-01-01")
    )
    connection.execute(
        """INSERT INTO segment_matches(
               project_id, case_id, accused_start, accused_end, source_episode,
               source_start, source_end, speed_ratio, visual_score, temporal_score,
               match_score, review_tier, created_at, algorithm_version)
           VALUES(1,'V4_CASE',0.0,5.0,'EP01',10.0,15.0,1.0,0.97,1.0,0.95,
                  'REVIEW_REQUIRED','2026-01-01','phase2a-temporal-v2')"""
    )
    connection.commit()
    connection.close()
    return path


def _segment_columns(path):
    with sqlite3.connect(path) as connection:
        return {row[1] for row in connection.execute("PRAGMA table_info(segment_matches)")}


def test_genuine_v4_database_reaches_current_schema_with_rows_preserved(tmp_path) -> None:
    """Regression for the Phase 2A.1 false v5 upgrade."""
    path = _build_genuine_v4_fixture(tmp_path)
    Database(path).initialize(tmp_path)
    columns = _segment_columns(path)
    for name, _ in REQUIRED_COLUMNS["segment_matches"]:
        assert name in columns
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert connection.execute(
            "SELECT case_id FROM segment_matches"
        ).fetchone()[0] == "V4_CASE"
        assert "truncated_episodes_json" in {
            row[1] for row in connection.execute("PRAGMA table_info(accused_videos)")
        }


def test_poisoned_v5_database_is_detected_and_repaired(tmp_path) -> None:
    """user_version is an application integer, not a schema validator."""
    path = _build_genuine_v4_fixture(tmp_path)
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version = 5")
    assert "speed_out_of_range" not in _segment_columns(path)

    Database(path).initialize(tmp_path)
    columns = _segment_columns(path)
    assert "speed_out_of_range" in columns
    assert "unconstrained_speed_ratio" in columns
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert connection.execute("SELECT COUNT(*) FROM segment_matches").fetchone()[0] == 1


def test_poisoned_v5_repair_failure_rolls_back_without_false_v6_stamp(
    tmp_path, monkeypatch
) -> None:
    path = _build_genuine_v4_fixture(tmp_path)
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version = 5")
    database = Database(path)

    def fail_after_repair(name: str) -> None:
        if name == "schema_repaired":
            raise RuntimeError("simulated poisoned-v5 repair failure")

    monkeypatch.setattr(database, "_migration_checkpoint", fail_after_repair)
    with pytest.raises(RuntimeError, match="poisoned-v5"):
        database.initialize(tmp_path)

    assert "speed_out_of_range" not in _segment_columns(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 5
        assert connection.execute("SELECT COUNT(*) FROM segment_matches").fetchone()[0] == 1


def test_database_newer_than_code_fails_loudly(tmp_path) -> None:
    path = _build_genuine_v4_fixture(tmp_path)
    with sqlite3.connect(path) as connection:
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    with pytest.raises(SchemaIntegrityError, match="newer than supported"):
        Database(path).initialize(tmp_path)


def test_database_fails_loudly_when_claimed_schema_is_missing_required_table(
    tmp_path,
) -> None:
    path = tmp_path / "missing-table.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE original_assets (id INTEGER PRIMARY KEY, project_id INTEGER)"
        )
        connection.execute(
            "CREATE TABLE projects (id INTEGER PRIMARY KEY, root_path TEXT UNIQUE, created_at TEXT)"
        )
        connection.execute("CREATE TABLE accused_videos (id INTEGER PRIMARY KEY)")
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    with pytest.raises(
        SchemaIntegrityError,
        match="required tables are missing: segment_matches",
    ):
        Database(path).initialize(tmp_path)

    assert path.with_suffix(path.suffix + f".pre-v{SCHEMA_VERSION}.bak").exists()
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_v4_to_v5_migration_failure_rolls_back_and_keeps_old_version(
    tmp_path, monkeypatch
) -> None:
    path = _build_genuine_v4_fixture(tmp_path)
    database = Database(path)

    def fail_at_checkpoint(name: str) -> None:
        if name == "v5_speed_range_added":
            raise RuntimeError("simulated v5 migration failure")

    monkeypatch.setattr(database, "_migration_checkpoint", fail_at_checkpoint)
    with pytest.raises(RuntimeError, match="simulated v5"):
        database.initialize(tmp_path)

    columns = _segment_columns(path)
    assert "speed_out_of_range" not in columns
    assert "unconstrained_speed_ratio" not in columns
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 4
        assert connection.execute("SELECT COUNT(*) FROM segment_matches").fetchone()[0] == 1
    assert path.with_suffix(path.suffix + f".pre-v{SCHEMA_VERSION}.bak").exists()


def test_v5_to_v6_migration_failure_preserves_completed_v5_step(
    tmp_path, monkeypatch
) -> None:
    path = _build_genuine_v4_fixture(tmp_path)
    database = Database(path)

    def fail_at_checkpoint(name: str) -> None:
        if name == "v6_boundary_completeness_added":
            raise RuntimeError("simulated v6 migration failure")

    monkeypatch.setattr(database, "_migration_checkpoint", fail_at_checkpoint)
    with pytest.raises(RuntimeError, match="simulated v6"):
        database.initialize(tmp_path)

    columns = _segment_columns(path)
    assert {"speed_out_of_range", "unconstrained_speed_ratio"} <= columns
    assert "boundary_completeness" not in columns
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 5
        assert connection.execute("SELECT COUNT(*) FROM segment_matches").fetchone()[0] == 1


def test_unrepairable_schema_fails_loudly(tmp_path, monkeypatch) -> None:
    """If a required column still cannot be produced, refuse to run."""
    path = _build_genuine_v4_fixture(tmp_path)
    database = Database(path)

    # Simulate a repair that cannot actually add the column.
    def no_op_repair(connection, action, stamp_version):
        return None

    monkeypatch.setattr(database, "_run_atomic", no_op_repair)
    with pytest.raises(SchemaIntegrityError, match="cannot be repaired"):
        database.initialize(tmp_path)


def test_boundary_completeness_round_trips(app_config) -> None:
    initialize_project(app_config)
    database = Database(app_config.paths.database)
    project_id = database.initialize(app_config.paths.root)
    segment = SegmentMatch(
        0.0, 6.0, "EP02", 100.0, 106.0, 1.0, 0.98, None, 1.0, 0.97,
        segment_id="S001", boundary_completeness="INCOMPLETE",
        boundary_completeness_reason="compatible orphan support outside the emitted span",
        segment_cap_truncated=True,
    )
    metadata = MediaMetadata(Path("x.mp4"), 6.0, 160, 90, 25.0, "h264", None, None, 0, None)
    database.replace_case_result(
        project_id, "BC_CASE", Path("x.mp4"), "d", metadata, "MATCH_FOUND", None,
        [segment], [], truncated_episodes=("EP02",), warnings=({
            "code": "ALIGNMENT_SEGMENT_CAP_REACHED",
            "episodes": ["EP02"],
            "limit_per_episode": 1,
        },),
    )
    with database.connect() as connection:
        row = connection.execute(
            "SELECT boundary_completeness, boundary_completeness_reason, segment_cap_truncated"
            " FROM segment_matches WHERE case_id='BC_CASE'"
        ).fetchone()
        accused = connection.execute(
            "SELECT truncated_episodes_json, analysis_warnings_json "
            "FROM accused_videos WHERE case_id='BC_CASE'"
        ).fetchone()
    assert row["boundary_completeness"] == "INCOMPLETE"
    assert "orphan" in row["boundary_completeness_reason"]
    assert row["segment_cap_truncated"] == 1
    assert json.loads(accused["truncated_episodes_json"]) == ["EP02"]
    assert json.loads(accused["analysis_warnings_json"])[0]["code"] == (
        "ALIGNMENT_SEGMENT_CAP_REACHED"
    )
