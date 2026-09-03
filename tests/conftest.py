from __future__ import annotations

from pathlib import Path

import pytest

from ipvm.config import AppConfig, MatchingConfig, ProjectPaths


@pytest.fixture
def app_config(tmp_path: Path) -> AppConfig:
    paths = ProjectPaths(
        root=tmp_path,
        originals=tmp_path / "input" / "originals",
        originals_metadata=tmp_path / "input" / "originals" / "metadata.json",
        golden=tmp_path / "input" / "golden",
        cases=tmp_path / "input" / "cases",
        workspace=tmp_path / "workspace",
        output=tmp_path / "output",
        database=tmp_path / "workspace" / "db" / "ipvm.db",
    )
    matching = MatchingConfig(
        coarse_fps=2.0,
        frame_width=160,
        frame_height=90,
        top_k=5,
        minimum_segment_seconds=3.0,
        minimum_visual_similarity=0.82,
        maximum_frame_gap_seconds=1.1,
        algorithm_version="test-v1",
    )
    return AppConfig(matching=matching, paths=paths)
