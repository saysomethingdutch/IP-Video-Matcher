from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class MatchingConfig:
    coarse_fps: float = 2.0
    frame_width: int = 160
    frame_height: int = 90
    top_k: int = 8
    minimum_segment_seconds: float = 3.0
    minimum_visual_similarity: float = 0.82
    offset_bin_seconds: float = 0.5
    maximum_frame_gap_seconds: float = 1.1
    algorithm_version: str = "phase1-dct-v1"
    minimum_uniqueness_margin: float = 0.05
    maximum_temporal_residual_seconds: float = 0.55
    minimum_coverage: float = 0.90
    minimum_temporal_consistency: float = 0.90
    audit_top_candidates: int = 5


@dataclass(frozen=True)
class ProjectPaths:
    root: Path
    originals: Path
    originals_metadata: Path
    golden: Path
    cases: Path
    workspace: Path
    output: Path
    database: Path


@dataclass(frozen=True)
class AppConfig:
    matching: MatchingConfig
    paths: ProjectPaths
    local_only: bool = True


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(root: Path, config_path: Path | None = None) -> AppConfig:
    root = root.resolve()
    default_path = root / "config" / "default.yaml"
    if not default_path.exists():
        raise FileNotFoundError(f"Missing configuration: {default_path}")
    data = yaml.safe_load(default_path.read_text(encoding="utf-8")) or {}
    if config_path:
        override = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        data = _deep_merge(data, override)

    raw_paths = data.get("paths", {})

    def resolve(value: str) -> Path:
        candidate = Path(value)
        return candidate if candidate.is_absolute() else root / candidate

    paths = ProjectPaths(
        root=root,
        originals=resolve(raw_paths.get("originals", "input/originals")),
        originals_metadata=resolve(raw_paths.get("originals_metadata", "input/originals/metadata.json")),
        golden=resolve(raw_paths.get("golden", "input/golden")),
        cases=resolve(raw_paths.get("cases", "input/cases")),
        workspace=resolve(raw_paths.get("workspace", "workspace")),
        output=resolve(raw_paths.get("output", "output")),
        database=resolve(raw_paths.get("database", "workspace/db/ipvm.db")),
    )
    return AppConfig(
        matching=MatchingConfig(**data.get("matching", {})),
        paths=paths,
        local_only=bool(data.get("project", {}).get("local_only", True)),
    )
