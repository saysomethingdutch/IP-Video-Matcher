from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from ipvm.database import Database
from ipvm.indexer import OriginalIndexer
from ipvm.matching import VideoMatcher
from ipvm.project import initialize_project


pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg unavailable")


def run_ffmpeg(arguments: list[str]) -> None:
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *arguments],
        check=True,
    )


def test_end_to_end_direct_cut(app_config) -> None:
    initialize_project(app_config)
    app_config.paths.originals.mkdir(parents=True, exist_ok=True)
    source = app_config.paths.originals / "EP04.mp4"
    accused = app_config.paths.workspace / "accused.mp4"
    run_ffmpeg([
        "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=20",
        "-t", "8", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source),
    ])
    run_ffmpeg([
        "-ss", "2", "-i", str(source), "-t", "4", "-an",
        "-c:v", "libx264", "-crf", "18", str(accused),
    ])
    database = Database(app_config.paths.database)
    OriginalIndexer(app_config, database).index_all()
    result = VideoMatcher(app_config, database).analyze_file("CASE_TEST", accused)
    assert result.status == "MATCH_FOUND"
    assert result.segments[0].source_episode == "EP04"
    assert abs(result.segments[0].source_start - 2.0) <= 0.5
