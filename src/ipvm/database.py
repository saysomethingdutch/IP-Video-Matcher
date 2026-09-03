from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from .models import MediaMetadata, SegmentMatch


SCHEMA_VERSION = 5

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY,
    root_path TEXT NOT NULL UNIQUE,
    index_status TEXT NOT NULL DEFAULT 'NOT_INDEXED',
    source_file_count INTEGER NOT NULL DEFAULT 0,
    indexed_asset_count INTEGER NOT NULL DEFAULT 0,
    index_reason TEXT,
    last_indexed_at TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS original_assets (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
    episode TEXT NOT NULL,
    file_path TEXT NOT NULL,
    file_size INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    duration REAL NOT NULL,
    media_metadata_json TEXT NOT NULL,
    index_path TEXT,
    index_status TEXT NOT NULL DEFAULT 'NOT_INDEXED',
    expected_frame_count INTEGER,
    actual_frame_count INTEGER,
    timestamp_coverage REAL,
    completeness_reason TEXT,
    encoder TEXT,
    algorithm_version TEXT,
    indexed_at TEXT,
    UNIQUE(project_id, episode),
    FOREIGN KEY(project_id) REFERENCES projects(id)
);
CREATE TABLE IF NOT EXISTS accused_videos (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
    case_id TEXT NOT NULL,
    file_path TEXT NOT NULL,
    file_size INTEGER,
    sha256 TEXT,
    duration REAL,
    media_metadata_json TEXT,
    status TEXT NOT NULL,
    reason TEXT,
    analyzed_at TEXT,
    UNIQUE(project_id, case_id),
    FOREIGN KEY(project_id) REFERENCES projects(id)
);
CREATE TABLE IF NOT EXISTS evidence_videos (
    id INTEGER PRIMARY KEY, case_id TEXT NOT NULL, file_path TEXT NOT NULL,
    file_size INTEGER, sha256 TEXT, duration REAL, metadata_json TEXT
);
CREATE TABLE IF NOT EXISTS uploaders (
    id INTEGER PRIMARY KEY, uploader_id TEXT UNIQUE, metadata_json TEXT
);
CREATE TABLE IF NOT EXISTS frame_features (
    id INTEGER PRIMARY KEY, original_asset_id INTEGER NOT NULL,
    timestamp REAL NOT NULL, frame_index INTEGER NOT NULL, feature_path TEXT NOT NULL,
    encoder TEXT NOT NULL, FOREIGN KEY(original_asset_id) REFERENCES original_assets(id)
);
CREATE TABLE IF NOT EXISTS transcripts (
    id INTEGER PRIMARY KEY, asset_type TEXT, asset_id INTEGER, segments_json TEXT,
    algorithm_version TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS segment_matches (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
    case_id TEXT NOT NULL,
    candidate_rank INTEGER NOT NULL DEFAULT 1,
    is_primary INTEGER NOT NULL DEFAULT 1,
    accused_start REAL NOT NULL,
    accused_end REAL NOT NULL,
    source_episode TEXT NOT NULL,
    source_start REAL NOT NULL,
    source_end REAL NOT NULL,
    speed_ratio REAL,
    visual_score REAL NOT NULL,
    audio_score REAL,
    temporal_score REAL NOT NULL,
    match_score REAL NOT NULL,
    temporal_residual_seconds REAL,
    fitted_offset_seconds REAL,
    residual_rms_seconds REAL NOT NULL DEFAULT 0,
    residual_max_seconds REAL NOT NULL DEFAULT 0,
    inlier_count INTEGER NOT NULL DEFAULT 0,
    candidate_count INTEGER NOT NULL DEFAULT 0,
    inlier_ratio REAL NOT NULL DEFAULT 0,
    coverage REAL,
    uniqueness_margin REAL,
    candidate_tier TEXT NOT NULL DEFAULT 'YELLOW',
    review_tier TEXT NOT NULL,
    segment_id TEXT,
    conflict INTEGER NOT NULL DEFAULT 0,
    conflict_with_json TEXT NOT NULL DEFAULT '[]',
    conflict_reason TEXT,
    source_reuse INTEGER NOT NULL DEFAULT 0,
    speed_out_of_range INTEGER NOT NULL DEFAULT 0,
    unconstrained_speed_ratio REAL,
    timestamp_basis TEXT NOT NULL DEFAULT 'nominal_grid_time',
    mirrored INTEGER,
    cropped INTEGER,
    created_at TEXT NOT NULL,
    algorithm_version TEXT NOT NULL,
    FOREIGN KEY(project_id) REFERENCES projects(id)
);
CREATE TABLE IF NOT EXISTS transformations (
    id INTEGER PRIMARY KEY, segment_match_id INTEGER, facts_json TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS content_segments (
    id INTEGER PRIMARY KEY, case_id TEXT, start REAL, end REAL, category TEXT,
    confidence REAL, evidence_json TEXT
);
CREATE TABLE IF NOT EXISTS semantic_assessments (
    id INTEGER PRIMARY KEY, case_id TEXT, assessment_json TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS rule_results (
    id INTEGER PRIMARY KEY, case_id TEXT, result_json TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS review_items (
    id INTEGER PRIMARY KEY, case_id TEXT, reason TEXT, status TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS exports (
    id INTEGER PRIMARY KEY, export_path TEXT, metadata_json TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS cache_entries (
    id INTEGER PRIMARY KEY, cache_key TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
    path TEXT NOT NULL, metadata_json TEXT, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_frame_features_asset ON frame_features(original_asset_id);
CREATE INDEX IF NOT EXISTS idx_segment_matches_case ON segment_matches(project_id, case_id);
CREATE INDEX IF NOT EXISTS idx_original_sha ON original_assets(project_id, sha256);
"""


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}


class Database:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self, project_root: Path) -> int:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        should_backup = self.path.exists() and self.path.stat().st_size > 0
        connection = sqlite3.connect(self.path, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA foreign_keys = OFF")
        try:
            existing = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='original_assets'"
            ).fetchone()
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            legacy = bool(
                existing and "project_id" not in _columns(connection, "original_assets")
            )
            migration_needed = legacy or bool(existing and version < SCHEMA_VERSION)
            if should_backup and migration_needed:
                backup = self.path.with_suffix(self.path.suffix + ".pre-v4.bak")
                if not backup.exists():
                    with sqlite3.connect(backup) as backup_connection:
                        connection.backup(backup_connection)
            connection.execute("BEGIN IMMEDIATE")
            if legacy:
                self._migrate_legacy(connection, project_root)
            else:
                self._execute_schema(connection)
                if existing and version < 3:
                    self._migrate_v2_to_v3(connection)
                if existing and "fitted_offset_seconds" not in _columns(
                    connection, "segment_matches"
                ):
                    self._migrate_v3_to_v4(connection)
            self._execute_schema(connection)
            connection.execute(
                "INSERT OR IGNORE INTO projects(root_path, created_at) VALUES (?, ?)",
                (str(project_root.resolve()), utc_now()),
            )
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            self._migration_checkpoint("before_commit")
            connection.execute("COMMIT")
            row = connection.execute(
                "SELECT id FROM projects WHERE root_path = ?", (str(project_root.resolve()),)
            ).fetchone()
            assert row is not None
            return int(row["id"])
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.close()

    @staticmethod
    def _execute_schema(connection: sqlite3.Connection) -> None:
        for statement in SCHEMA.split(";"):
            if statement.strip():
                connection.execute(statement)

    def _migration_checkpoint(self, name: str) -> None:
        """No-op seam used to simulate mid-migration failure in tests."""

    def _migrate_legacy(self, connection: sqlite3.Connection, project_root: Path) -> None:
        """Preserve Phase-1 data inside the caller's explicit transaction."""
        connection.execute(
            "INSERT OR IGNORE INTO projects(root_path, created_at) VALUES (?, ?)",
            (str(project_root.resolve()), utc_now()),
        )
        project_row = connection.execute(
            "SELECT id FROM projects WHERE root_path = ?", (str(project_root.resolve()),)
        ).fetchone()
        assert project_row is not None
        project_id = int(project_row[0])

        connection.execute("ALTER TABLE original_assets RENAME TO original_assets_v1")
        connection.execute("ALTER TABLE frame_features RENAME TO frame_features_v1")
        connection.execute("ALTER TABLE accused_videos RENAME TO accused_videos_v1")
        connection.execute("ALTER TABLE segment_matches RENAME TO segment_matches_v1")
        self._migration_checkpoint("legacy_tables_renamed")
        for column, definition in (
            ("index_status", "TEXT NOT NULL DEFAULT 'NOT_INDEXED'"),
            ("source_file_count", "INTEGER NOT NULL DEFAULT 0"),
            ("indexed_asset_count", "INTEGER NOT NULL DEFAULT 0"),
            ("index_reason", "TEXT"),
            ("last_indexed_at", "TEXT"),
        ):
            if column not in _columns(connection, "projects"):
                connection.execute(f"ALTER TABLE projects ADD COLUMN {column} {definition}")
        self._execute_schema(connection)
        connection.execute(
            """
            INSERT INTO original_assets(
                id, project_id, episode, file_path, file_size, sha256, duration,
                media_metadata_json, index_path, index_status, algorithm_version, indexed_at
            )
            SELECT id, ?, episode, file_path, file_size, sha256, duration,
                   media_metadata_json, index_path,
                   CASE WHEN index_path IS NULL THEN 'NOT_INDEXED' ELSE 'UNKNOWN_LEGACY' END,
                   algorithm_version, indexed_at
            FROM original_assets_v1
            """,
            (project_id,),
        )
        connection.execute(
            """
            INSERT INTO frame_features(id, original_asset_id, timestamp, frame_index, feature_path, encoder)
            SELECT id, original_asset_id, timestamp, frame_index, feature_path, encoder
            FROM frame_features_v1
            """
        )
        connection.execute(
            """
            INSERT INTO accused_videos(
                id, project_id, case_id, file_path, file_size, sha256, duration,
                media_metadata_json, status, analyzed_at
            )
            SELECT id, ?, case_id, file_path, file_size, sha256, duration,
                   media_metadata_json, status, analyzed_at
            FROM accused_videos_v1
            """,
            (project_id,),
        )
        connection.execute(
            """
            INSERT INTO segment_matches(
                id, project_id, case_id, accused_start, accused_end, source_episode,
                source_start, source_end, speed_ratio, visual_score, audio_score,
                temporal_score, match_score, candidate_tier, review_tier, mirrored, cropped,
                created_at, algorithm_version
            )
            SELECT id, ?, case_id, accused_start, accused_end, source_episode,
                   source_start, source_end, speed_ratio, visual_score, audio_score,
                   temporal_score, overall_confidence, 'YELLOW', 'REVIEW_REQUIRED', NULL, NULL,
                   created_at, algorithm_version
            FROM segment_matches_v1
            """,
            (project_id,),
        )
        self._migration_checkpoint("legacy_data_copied")
        for table in (
            "frame_features_v1", "segment_matches_v1", "accused_videos_v1", "original_assets_v1"
        ):
            connection.execute(f"DROP TABLE {table}")
        self._execute_schema(connection)
        connection.execute(
            """
            UPDATE projects SET index_status='INCOMPLETE_INDEX',
                source_file_count=(SELECT COUNT(*) FROM original_assets WHERE project_id=?),
                indexed_asset_count=0,
                index_reason='Legacy indexes require completeness validation before reuse'
            WHERE id=?
            """,
            (project_id, project_id),
        )

    def _migrate_v2_to_v3(self, connection: sqlite3.Connection) -> None:
        if "candidate_tier" in _columns(connection, "segment_matches"):
            return
        connection.execute("DROP INDEX IF EXISTS idx_segment_matches_case")
        connection.execute("ALTER TABLE segment_matches RENAME TO segment_matches_v2")
        self._migration_checkpoint("v2_table_renamed")
        self._execute_schema(connection)
        connection.execute(
            """
            INSERT INTO segment_matches(
                id, project_id, case_id, candidate_rank, is_primary,
                accused_start, accused_end, source_episode, source_start, source_end,
                speed_ratio, visual_score, audio_score, temporal_score, match_score,
                temporal_residual_seconds, coverage, uniqueness_margin,
                candidate_tier, review_tier, mirrored, cropped, created_at, algorithm_version
            )
            SELECT id, project_id, case_id, candidate_rank, is_primary,
                   accused_start, accused_end, source_episode, source_start, source_end,
                   speed_ratio, visual_score, audio_score, temporal_score, match_score,
                   temporal_residual_seconds, coverage, uniqueness_margin,
                   CASE WHEN review_tier='GREEN' THEN 'GREEN' ELSE 'YELLOW' END,
                   CASE WHEN review_tier IN ('GREEN', 'YELLOW')
                        THEN 'REVIEW_REQUIRED' ELSE review_tier END,
                   mirrored, cropped, created_at, algorithm_version
            FROM segment_matches_v2
            """
        )
        self._migration_checkpoint("v2_data_copied")
        connection.execute("DROP TABLE segment_matches_v2")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_segment_matches_case "
            "ON segment_matches(project_id, case_id)"
        )

    def _migrate_v3_to_v4(self, connection: sqlite3.Connection) -> None:
        """Add Temporal Alignment v2 diagnostics inside the caller transaction."""
        additions = (
            ("fitted_offset_seconds", "REAL"),
            ("residual_rms_seconds", "REAL NOT NULL DEFAULT 0"),
            ("residual_max_seconds", "REAL NOT NULL DEFAULT 0"),
            ("inlier_count", "INTEGER NOT NULL DEFAULT 0"),
            ("candidate_count", "INTEGER NOT NULL DEFAULT 0"),
            ("inlier_ratio", "REAL NOT NULL DEFAULT 0"),
            ("source_reuse", "INTEGER NOT NULL DEFAULT 0"),
            ("speed_out_of_range", "INTEGER NOT NULL DEFAULT 0"),
            ("unconstrained_speed_ratio", "REAL"),
            ("timestamp_basis", "TEXT NOT NULL DEFAULT 'nominal_grid_time'"),
        )
        for column, definition in additions:
            if column not in _columns(connection, "segment_matches"):
                connection.execute(
                    f"ALTER TABLE segment_matches ADD COLUMN {column} {definition}"
                )
        self._migration_checkpoint("v4_diagnostics_added")

    def project_id(self, project_root: Path) -> int:
        return self.initialize(project_root)

    def update_project_index_status(
        self,
        project_id: int,
        status: str,
        source_file_count: int,
        indexed_asset_count: int,
        reason: str | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE projects SET index_status=?, source_file_count=?, indexed_asset_count=?,
                    index_reason=?, last_indexed_at=? WHERE id=?
                """,
                (status, source_file_count, indexed_asset_count, reason, utc_now(), project_id),
            )

    def index_health(self, project_id: int) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT index_status, source_file_count, indexed_asset_count,
                       index_reason, last_indexed_at
                FROM projects WHERE id=?
                """,
                (project_id,),
            ).fetchone()
            if row is None:
                return {"status": "INCOMPLETE_INDEX", "reason": "Project not initialized"}
            assets = list(connection.execute(
                """
                SELECT episode, file_path, sha256, duration, index_path, index_status, expected_frame_count,
                       actual_frame_count, timestamp_coverage, completeness_reason,
                       encoder, algorithm_version
                FROM original_assets WHERE project_id=? ORDER BY episode
                """,
                (project_id,),
            ))
        return {
            "status": row["index_status"],
            "source_file_count": row["source_file_count"],
            "indexed_asset_count": row["indexed_asset_count"],
            "reason": row["index_reason"],
            "last_indexed_at": row["last_indexed_at"],
            "assets": [dict(asset) for asset in assets],
        }

    def upsert_original(
        self,
        project_id: int,
        episode: str,
        path: Path,
        sha256: str,
        metadata: MediaMetadata,
        index_path: Path | None,
        algorithm_version: str,
        encoder: str,
        completeness: dict[str, Any],
    ) -> int:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO original_assets(
                    project_id, episode, file_path, file_size, sha256, duration,
                    media_metadata_json, index_path, index_status,
                    expected_frame_count, actual_frame_count, timestamp_coverage,
                    completeness_reason, encoder, algorithm_version, indexed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, episode) DO UPDATE SET
                    file_path=excluded.file_path, file_size=excluded.file_size,
                    sha256=excluded.sha256, duration=excluded.duration,
                    media_metadata_json=excluded.media_metadata_json,
                    index_path=excluded.index_path, index_status=excluded.index_status,
                    expected_frame_count=excluded.expected_frame_count,
                    actual_frame_count=excluded.actual_frame_count,
                    timestamp_coverage=excluded.timestamp_coverage,
                    completeness_reason=excluded.completeness_reason,
                    encoder=excluded.encoder,
                    algorithm_version=excluded.algorithm_version,
                    indexed_at=excluded.indexed_at
                """,
                (
                    project_id, episode, str(path.resolve()), path.stat().st_size, sha256,
                    metadata.duration, json.dumps(metadata.to_dict(), ensure_ascii=False),
                    str(index_path.resolve()) if index_path else None,
                    completeness["status"], completeness["expected_frame_count"],
                    completeness["actual_frame_count"], completeness["timestamp_coverage"],
                    completeness.get("reason"), encoder, algorithm_version, utc_now(),
                ),
            )
            row = connection.execute(
                "SELECT id FROM original_assets WHERE project_id=? AND episode=?",
                (project_id, episode),
            ).fetchone()
            assert row is not None
            asset_id = int(row["id"])
            connection.execute("DELETE FROM frame_features WHERE original_asset_id=?", (asset_id,))
            return asset_id

    def add_frame_rows(
        self, asset_id: int, timestamps: list[float], index_path: Path, encoder: str
    ) -> None:
        with self.connect() as connection:
            connection.executemany(
                """
                INSERT INTO frame_features(
                    original_asset_id, timestamp, frame_index, feature_path, encoder
                ) VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (asset_id, timestamp, index, str(index_path.resolve()), encoder)
                    for index, timestamp in enumerate(timestamps)
                ],
            )

    def list_original_indexes(self, project_id: int) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(connection.execute(
                """
                SELECT episode, file_path, sha256, duration, index_path, index_status,
                       expected_frame_count, actual_frame_count, timestamp_coverage,
                       encoder, algorithm_version
                FROM original_assets
                WHERE project_id=? AND index_status='COMPLETE' AND index_path IS NOT NULL
                ORDER BY episode
                """,
                (project_id,),
            ))

    def replace_case_result(
        self,
        project_id: int,
        case_id: str,
        path: Path,
        sha256: str | None,
        metadata: MediaMetadata | None,
        status: str,
        reason: str | None,
        segments: list[SegmentMatch],
        alternatives: list[SegmentMatch] | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO accused_videos(
                    project_id, case_id, file_path, file_size, sha256, duration,
                    media_metadata_json, status, reason, analyzed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, case_id) DO UPDATE SET
                    file_path=excluded.file_path, file_size=excluded.file_size,
                    sha256=excluded.sha256, duration=excluded.duration,
                    media_metadata_json=excluded.media_metadata_json,
                    status=excluded.status, reason=excluded.reason,
                    analyzed_at=excluded.analyzed_at
                """,
                (
                    project_id, case_id, str(path.resolve()),
                    path.stat().st_size if path.exists() else None, sha256,
                    metadata.duration if metadata else None,
                    json.dumps(metadata.to_dict(), ensure_ascii=False) if metadata else None,
                    status, reason, utc_now(),
                ),
            )
            connection.execute(
                "DELETE FROM segment_matches WHERE project_id=? AND case_id=?",
                (project_id, case_id),
            )
            rows: list[tuple[Any, ...]] = []
            for rank, segment in enumerate(segments, start=1):
                rows.append(self._segment_row(project_id, case_id, rank, True, segment))
            for rank, segment in enumerate(alternatives or [], start=1):
                rows.append(self._segment_row(project_id, case_id, rank, False, segment))
            connection.executemany(
                """
                INSERT INTO segment_matches(
                    project_id, case_id, candidate_rank, is_primary,
                    accused_start, accused_end, source_episode, source_start, source_end,
                    speed_ratio, visual_score, audio_score, temporal_score, match_score,
                    temporal_residual_seconds, fitted_offset_seconds,
                    residual_rms_seconds, residual_max_seconds,
                    inlier_count, candidate_count, inlier_ratio,
                    coverage, uniqueness_margin,
                    candidate_tier, review_tier, segment_id, conflict,
                    conflict_with_json, conflict_reason, source_reuse,
                    speed_out_of_range, unconstrained_speed_ratio, timestamp_basis,
                    mirrored, cropped, created_at, algorithm_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    @staticmethod
    def _segment_row(
        project_id: int, case_id: str, rank: int, primary: bool, segment: SegmentMatch
    ) -> tuple[Any, ...]:
        return (
            project_id, case_id, rank, int(primary), segment.accused_start,
            segment.accused_end, segment.source_episode, segment.source_start,
            segment.source_end, segment.speed_ratio, segment.visual_score,
            segment.audio_score, segment.temporal_score, segment.match_score,
            segment.temporal_residual_seconds, segment.fitted_offset_seconds,
            segment.residual_rms_seconds, segment.residual_max_seconds,
            segment.inlier_count, segment.candidate_count, segment.inlier_ratio,
            segment.coverage,
            segment.uniqueness_margin, segment.candidate_tier, segment.review_tier,
            segment.segment_id, int(segment.conflict),
            json.dumps(segment.conflict_with, ensure_ascii=False), segment.conflict_reason,
            int(segment.source_reuse), int(segment.speed_out_of_range),
            segment.unconstrained_speed_ratio, segment.timestamp_basis,
            None if segment.mirrored is None else int(segment.mirrored),
            None if segment.cropped is None else int(segment.cropped),
            utc_now(), segment.algorithm_version,
        )
