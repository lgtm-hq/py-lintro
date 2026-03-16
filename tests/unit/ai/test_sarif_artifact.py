"""Tests for artifact side-channel output (#723 item 11)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from assertpy import assert_that

from lintro.enums.action import Action
from lintro.models.core.tool_result import ToolResult
from lintro.utils.tool_executor import _write_artifacts


def _make_config(*, artifacts: list[str] | None = None) -> MagicMock:
    """Build a minimal LintroConfig-like mock."""
    cfg = MagicMock()
    cfg.execution.artifacts = artifacts or []
    return cfg


def _make_logger() -> MagicMock:
    return MagicMock()


def _call_write(
    results: list[ToolResult],
    config: MagicMock,
    logger: MagicMock,
) -> None:
    _write_artifacts(
        results,
        config,
        logger,
        action=Action.CHECK,
        total_issues=0,
        total_fixed=0,
    )


def test_no_artifacts_when_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No files are produced when artifacts is empty and not in GHA."""
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.chdir(tmp_path)

    results = [ToolResult(name="ruff", success=True, issues_count=0)]
    _call_write(results, _make_config(), _make_logger())

    artifacts_dir = tmp_path / ".lintro" / "artifacts"
    assert_that(artifacts_dir.exists()).is_false()


def test_sarif_artifact_written_when_configured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SARIF file is produced when 'sarif' is in execution.artifacts."""
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.chdir(tmp_path)

    results = [ToolResult(name="ruff", success=True, issues_count=0)]
    _call_write(results, _make_config(artifacts=["sarif"]), _make_logger())

    sarif_path = tmp_path / ".lintro" / "artifacts" / "sarif" / "results.sarif.json"
    assert_that(sarif_path.exists()).is_true()

    data = json.loads(sarif_path.read_text())
    assert_that(data["version"]).is_equal_to("2.1.0")


def test_json_artifact_written_when_configured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """JSON artifact file is produced when 'json' is in execution.artifacts."""
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.chdir(tmp_path)

    results = [ToolResult(name="ruff", success=True, issues_count=0)]
    _call_write(results, _make_config(artifacts=["json"]), _make_logger())

    json_path = tmp_path / ".lintro" / "artifacts" / "json" / "results.json"
    assert_that(json_path.exists()).is_true()

    data = json.loads(json_path.read_text())
    assert_that(data).contains_key("summary")


def test_csv_artifact_written_when_configured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CSV artifact file is produced when 'csv' is in execution.artifacts."""
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.chdir(tmp_path)

    results = [ToolResult(name="ruff", success=True, issues_count=0)]
    _call_write(results, _make_config(artifacts=["csv"]), _make_logger())

    csv_path = tmp_path / ".lintro" / "artifacts" / "csv" / "results.csv"
    assert_that(csv_path.exists()).is_true()
    assert_that(csv_path.read_text()).contains("tool")


def test_multiple_artifacts_written(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Multiple artifact formats can be emitted simultaneously."""
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.chdir(tmp_path)

    results = [ToolResult(name="ruff", success=True, issues_count=0)]
    _call_write(
        results,
        _make_config(artifacts=["json", "csv", "markdown"]),
        _make_logger(),
    )

    assert_that(
        (tmp_path / ".lintro" / "artifacts" / "json" / "results.json").exists(),
    ).is_true()
    assert_that(
        (tmp_path / ".lintro" / "artifacts" / "csv" / "results.csv").exists(),
    ).is_true()
    assert_that(
        (tmp_path / ".lintro" / "artifacts" / "markdown" / "results.md").exists(),
    ).is_true()


def test_sarif_auto_emits_in_github_actions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SARIF file is auto-emitted when GITHUB_ACTIONS=true."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.chdir(tmp_path)

    results = [ToolResult(name="ruff", success=True, issues_count=0)]
    _call_write(results, _make_config(), _make_logger())

    sarif_path = tmp_path / ".lintro" / "artifacts" / "sarif" / "results.sarif.json"
    assert_that(sarif_path.exists()).is_true()


def test_unknown_artifact_format_warns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown artifact format logs a warning and is skipped."""
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.chdir(tmp_path)

    logger = _make_logger()
    results = [ToolResult(name="ruff", success=True, issues_count=0)]
    _call_write(results, _make_config(artifacts=["xlsx"]), logger)

    logger.console_output.assert_called_once()
    call_arg = logger.console_output.call_args[0][0]
    assert_that(call_arg).contains("Unknown artifact format")


def test_artifact_logs_warning_on_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A write failure logs a warning instead of crashing."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    # Block directory creation by placing a file where the dir should be
    blocker = tmp_path / ".lintro" / "artifacts"
    blocker.parent.mkdir(parents=True, exist_ok=True)
    blocker.write_text("not a directory")
    monkeypatch.chdir(tmp_path)

    logger = _make_logger()
    results = [ToolResult(name="ruff", success=True, issues_count=0)]
    _call_write(results, _make_config(), logger)

    logger.console_output.assert_called_once()
    call_arg = logger.console_output.call_args[0][0]
    assert_that(call_arg).contains("sarif artifact")
