"""Exercise listing and execution with real API-v2 plugin registrations."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from lintro.cli import cli
from lintro.cli_utils.commands.list_tools import list_tools_command
from lintro.models.core.tool_result import ToolResult
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import ToolRegistry
from lintro.tools.core import snapshots

_AMBIGUOUS = ("ambiguous-alpha", "ambiguous-beta")
_HEALTHY = "healthy-control"


def _register_plugin(name: str, *, in_process: bool | None) -> None:
    """Register matching plugins that differ only in execution-mode evidence."""

    class ProbePlugin(BaseToolPlugin):
        LINTRO_PLUGIN_API_VERSION = 2

        @property
        def definition(self) -> ToolDefinition:
            return ToolDefinition(
                name,
                "Listing regression fixture",
                in_process=in_process,
                file_patterns=["*.fake"],
            )

        def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
            prepared = self.prepare(paths, options)
            if isinstance(prepared, ToolResult):
                return prepared
            return ToolResult(name=name, success=True, output="Inspected fixture")

    ToolRegistry.register(ProbePlugin, origin="listing-fixtures")


@pytest.fixture
def registered_plugins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Use an isolated registry with two ambiguous tools and a healthy peer."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("COLUMNS", "240")
    monkeypatch.setattr(
        snapshots,
        "_cache_file_path",
        lambda *, cache_root=None: (cache_root or tmp_path)
        / snapshots.SNAPSHOT_CACHE_RELPATH,
    )
    ToolRegistry.clear()
    for name in _AMBIGUOUS:
        _register_plugin(name, in_process=None)
    _register_plugin(_HEALTHY, in_process=True)
    monkeypatch.setattr(
        snapshots,
        "get_executable_command",
        lambda name, **kwargs: [str(tmp_path / name)],
    )
    (tmp_path / "target.fake").write_text("content\n")
    return tmp_path


@pytest.mark.parametrize("output_format", ["json", "table"])
def test_list_tools_degrades_each_ambiguous_plugin_and_preserves_healthy_peer(
    registered_plugins: Path,
    output_format: str,
) -> None:
    """The actual command reports every plugin and its migration diagnostic."""
    output_path = registered_plugins / "listing.txt"
    args = ["--json"] if output_format == "json" else ["--output", str(output_path)]
    result = CliRunner().invoke(list_tools_command, args, terminal_width=240)

    assert result.exit_code == 0, result.output
    if output_format == "json":
        rows = json.loads(result.output)
        assert set(rows) == {*_AMBIGUOUS, _HEALTHY}
        for name in _AMBIGUOUS:
            assert rows[name]["status"] == "unavailable"
            assert rows[name]["available"] is False
            assert "execution mode is unspecified" in rows[name]["probe_error"]
            assert "in_process=True" in rows[name]["remediation_hint"]
            assert "in_process=False" in rows[name]["remediation_hint"]
        assert rows[_HEALTHY]["available"] is True
        assert rows[_HEALTHY]["status"] == "unknown"
        assert rows[_HEALTHY]["probe_error"] is None
    else:
        for rendered in (result.output, output_path.read_text()):
            rendered = " ".join(rendered.split())
            assert _HEALTHY in rendered
            for name in _AMBIGUOUS:
                assert name in rendered
            assert rendered.count("execution mode is unspecified") == 2
            assert rendered.count("in_process=True") == 2
            assert rendered.count("in_process=False") == 2
            assert "unavailable" in rendered


@pytest.mark.parametrize("hydrate", [False, True], ids=["memory", "disk"])
def test_listing_does_not_relax_execution_mode_failure(
    registered_plugins: Path,
    hydrate: bool,
) -> None:
    """Listing cannot cache successful unavailability for ambiguous execution."""
    runner = CliRunner()
    listing = runner.invoke(list_tools_command, ["--json"])
    assert listing.exit_code == 0, listing.output
    cache = json.loads(
        (registered_plugins / snapshots.SNAPSHOT_CACHE_RELPATH).read_text(),
    )
    assert set(cache["snapshots"]) == {_HEALTHY}
    if hydrate:
        snapshots.clear_snapshot_cache(include_disk=False)
    for name in (*_AMBIGUOUS, _HEALTHY):
        report = registered_plugins / f"{name}.json"
        result = runner.invoke(
            cli,
            [
                "chk",
                "target.fake",
                "--tools",
                name,
                "--output-format",
                "json",
                "--output",
                str(report),
            ],
        )
        row = json.loads(report.read_text())["results"][0]
        if name in _AMBIGUOUS:
            assert result.exit_code == 1
            assert row["status"] == "failed"
            assert row["success"] is False
            assert "in_process=True" in row["output"]
        else:
            assert result.exit_code == 0
            assert row["status"] == "ok"
            assert row["output"] == "Inspected fixture"


@pytest.mark.parametrize("error_type", [ValueError, RuntimeError, AttributeError])
def test_list_tools_does_not_hide_unexpected_probe_errors(
    registered_plugins: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[Exception],
) -> None:
    """Only the known ambiguous-mode condition may become listing diagnostics."""

    def crash(*args: object, **kwargs: object) -> list[str]:
        raise error_type("unexpected internal probe failure")

    monkeypatch.setattr(snapshots, "get_executable_command", crash)
    result = CliRunner().invoke(list_tools_command, ["--json"])
    assert result.exit_code != 0
    assert type(result.exception) is error_type
    assert str(result.exception) == "unexpected internal probe failure"
