"""Tests for SARIF artifact side-channel output (#723 item 11)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from assertpy import assert_that

from lintro.models.core.tool_result import ToolResult
from lintro.utils.tool_executor import _write_sarif_artifact


def _make_config(*, artifacts: list[str] | None = None) -> MagicMock:
    """Build a minimal LintroConfig-like mock."""
    cfg = MagicMock()
    cfg.execution.artifacts = artifacts or []
    return cfg


def _make_logger() -> MagicMock:
    return MagicMock()


def test_sarif_artifact_not_written_when_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No SARIF file is produced when artifacts is empty and not in GHA."""
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.chdir(tmp_path)

    results = [ToolResult(name="ruff", success=True, issues_count=0)]
    _write_sarif_artifact(results, _make_config(), _make_logger())

    sarif_path = tmp_path / ".lintro" / "sarif" / "results.sarif.json"
    assert_that(sarif_path.exists()).is_false()


def test_sarif_artifact_written_when_configured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SARIF file is produced when 'sarif' is in execution.artifacts."""
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.chdir(tmp_path)

    results = [ToolResult(name="ruff", success=True, issues_count=0)]
    _write_sarif_artifact(
        results,
        _make_config(artifacts=["sarif"]),
        _make_logger(),
    )

    sarif_path = tmp_path / ".lintro" / "sarif" / "results.sarif.json"
    assert_that(sarif_path.exists()).is_true()

    data = json.loads(sarif_path.read_text())
    assert_that(data["version"]).is_equal_to("2.1.0")


def test_sarif_artifact_auto_emits_in_github_actions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SARIF file is auto-emitted when GITHUB_ACTIONS=true."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.chdir(tmp_path)

    results = [ToolResult(name="ruff", success=True, issues_count=0)]
    _write_sarif_artifact(results, _make_config(), _make_logger())

    sarif_path = tmp_path / ".lintro" / "sarif" / "results.sarif.json"
    assert_that(sarif_path.exists()).is_true()


def test_sarif_artifact_logs_warning_on_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A write failure logs a warning instead of crashing."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    # Point to a path that cannot be created (file blocking directory)
    blocker = tmp_path / ".lintro" / "sarif"
    blocker.parent.mkdir(parents=True, exist_ok=True)
    blocker.write_text("not a directory")
    monkeypatch.chdir(tmp_path)

    logger = _make_logger()
    results = [ToolResult(name="ruff", success=True, issues_count=0)]
    _write_sarif_artifact(results, _make_config(), logger)

    logger.console_output.assert_called_once()
    call_arg = logger.console_output.call_args[0][0]
    assert_that(call_arg).contains("SARIF artifact")
