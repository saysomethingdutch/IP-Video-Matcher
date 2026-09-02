from __future__ import annotations

from click.testing import CliRunner

from ipvm.cli import main


def test_cli_lists_required_commands() -> None:
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    for command in (
        "init-project", "index-originals", "analyze", "analyze-all",
        "export", "benchmark", "doctor",
    ):
        assert command in result.output
