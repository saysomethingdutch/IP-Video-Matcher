from __future__ import annotations

from click.testing import CliRunner
from types import SimpleNamespace

from ipvm.cli import _analyze, main


def test_cli_lists_required_commands() -> None:
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    for command in (
        "init-project", "index-originals", "analyze", "analyze-all",
        "export", "benchmark", "doctor",
    ):
        assert command in result.output


def test_analyze_payload_exposes_structured_warnings(tmp_path, monkeypatch) -> None:
    accused = tmp_path / "cases" / "CASE" / "accused.mp4"
    accused.parent.mkdir(parents=True)
    accused.write_bytes(b"x")
    warning = {
        "code": "ALIGNMENT_SEGMENT_CAP_REACHED",
        "episodes": ["EP01"],
        "limit_per_episode": 2,
    }
    result = SimpleNamespace(
        case_id="CASE",
        status="MATCH_FOUND",
        review_tier="REVIEW_REQUIRED",
        reason="cap",
        truncated_episodes=("EP01",),
        warnings=(warning,),
        segments=[],
        alternatives=[],
        conflict_graph=[],
        elapsed_seconds=0.1,
    )
    context = SimpleNamespace(
        config=SimpleNamespace(
            paths=SimpleNamespace(cases=tmp_path / "cases", output=tmp_path / "output")
        ),
        database=object(),
    )
    monkeypatch.setattr("ipvm.cli.VideoMatcher", lambda *args: SimpleNamespace(
        analyze_file=lambda *args: result
    ))
    monkeypatch.setattr("ipvm.cli.write_audit_result", lambda *args: tmp_path / "audit.json")
    payload = _analyze(context, "CASE")
    assert payload["truncated_episodes"] == ["EP01"]
    assert payload["warnings"] == [warning]
