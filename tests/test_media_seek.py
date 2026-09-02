from __future__ import annotations

import io
from pathlib import Path

from ipvm.media import extract_grayscale_frames


class _EmptyProcess:
    def __init__(self) -> None:
        self.stdout = io.BytesIO()

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def poll(self) -> int:
        return 0

    def terminate(self) -> None:
        raise AssertionError("completed process must not be terminated")

    def kill(self) -> None:
        raise AssertionError("completed process must not be killed")


def test_input_seek_resets_pts_before_query_fps_filter(monkeypatch) -> None:
    captured: list[str] = []

    def fake_popen(command, **_kwargs):
        captured.extend(command)
        return _EmptyProcess()

    monkeypatch.setattr("ipvm.media.subprocess.Popen", fake_popen)
    frames = extract_grayscale_frames(
        Path("query-source.mp4"),
        fps=2.0,
        width=160,
        height=90,
        start_seconds=12.137,
        duration_seconds=6.0,
        input_seek=True,
        reset_timestamps=True,
    )
    assert frames == []
    assert captured.index("-ss") < captured.index("-i")
    filters = captured[captured.index("-vf") + 1].split(",")
    assert filters[:2] == ["setpts=PTS-STARTPTS", "fps=2.0"]
