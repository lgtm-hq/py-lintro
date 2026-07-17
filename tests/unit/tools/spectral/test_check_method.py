"""Tests for SpectralPlugin.check."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, patch

from assertpy import assert_that

from lintro.parsers.spectral.spectral_issue import SpectralIssue
from lintro.tools.definitions.spectral import SpectralPlugin

MOCK_OUTPUT = (
    '[{"code": "operation-operationId", "path": ["paths", "/users", "get"], '
    '"message": "Operation must have \\"operationId\\".", "severity": 1, '
    '"range": {"start": {"line": 6, "character": 8}}, "source": "openapi.yaml"}]'
)


def _mock_ctx(tmp_path: Path) -> MagicMock:
    """Build a mock execution context for check().

    Args:
        tmp_path: Temporary directory for the fake target file.

    Returns:
        MagicMock: A context object mimicking _prepare_execution output.
    """
    ctx = MagicMock()
    ctx.should_skip = False
    ctx.early_result = None
    ctx.timeout = 30
    ctx.cwd = str(tmp_path)
    ctx.rel_files = ["openapi.yaml"]
    ctx.files = [str(tmp_path / "openapi.yaml")]
    return ctx


def test_check_with_issues(spectral_plugin: SpectralPlugin, tmp_path: Path) -> None:
    """Check returns parsed issues and marks failure.

    Args:
        spectral_plugin: The SpectralPlugin instance under test.
        tmp_path: Temporary directory path for test files.
    """
    (tmp_path / "openapi.yaml").write_text("openapi: 3.0.0\n")

    with (
        patch.object(spectral_plugin, "_prepare_execution") as mock_prepare,
        patch.object(
            spectral_plugin,
            "_find_ruleset",
            return_value=str(tmp_path / ".spectral.yaml"),
        ),
        patch.object(
            spectral_plugin,
            "_run_subprocess",
            return_value=(False, MOCK_OUTPUT),
        ),
        patch.object(
            spectral_plugin,
            "_get_spectral_command",
            return_value=["spectral"],
        ),
    ):
        mock_prepare.return_value = _mock_ctx(tmp_path)
        result = spectral_plugin.check([str(tmp_path / "openapi.yaml")], {})

    assert_that(result.name).is_equal_to("spectral")
    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)
    issue = cast(SpectralIssue, result.issues[0])  # type: ignore[index]
    assert_that(issue.code).is_equal_to("operation-operationId")
    assert_that(issue.doc_url).contains("stoplight.io")


def test_check_without_issues(spectral_plugin: SpectralPlugin, tmp_path: Path) -> None:
    """Check returns success and suppresses output when clean.

    Args:
        spectral_plugin: The SpectralPlugin instance under test.
        tmp_path: Temporary directory path for test files.
    """
    (tmp_path / "openapi.yaml").write_text("openapi: 3.0.0\n")

    with (
        patch.object(spectral_plugin, "_prepare_execution") as mock_prepare,
        patch.object(
            spectral_plugin,
            "_find_ruleset",
            return_value=str(tmp_path / ".spectral.yaml"),
        ),
        patch.object(spectral_plugin, "_run_subprocess", return_value=(True, "[]")),
        patch.object(
            spectral_plugin,
            "_get_spectral_command",
            return_value=["spectral"],
        ),
    ):
        mock_prepare.return_value = _mock_ctx(tmp_path)
        result = spectral_plugin.check([str(tmp_path / "openapi.yaml")], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.output).is_none()


def test_check_skips_without_ruleset(
    spectral_plugin: SpectralPlugin,
    tmp_path: Path,
) -> None:
    """Check skips gracefully (success, no run) when no ruleset is found.

    Args:
        spectral_plugin: The SpectralPlugin instance under test.
        tmp_path: Temporary directory path for test files.
    """
    (tmp_path / "openapi.yaml").write_text("openapi: 3.0.0\n")

    with (
        patch.object(spectral_plugin, "_prepare_execution") as mock_prepare,
        patch.object(spectral_plugin, "_find_ruleset", return_value=None),
        patch.object(spectral_plugin, "_run_subprocess") as mock_run,
    ):
        mock_prepare.return_value = _mock_ctx(tmp_path)
        result = spectral_plugin.check([str(tmp_path / "openapi.yaml")], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.output).contains("no ruleset")
    mock_run.assert_not_called()


def test_check_returns_early_when_skipped(
    spectral_plugin: SpectralPlugin,
    tmp_path: Path,
) -> None:
    """Check returns the early result when preparation signals a skip.

    Args:
        spectral_plugin: The SpectralPlugin instance under test.
        tmp_path: Temporary directory path for test files.
    """
    early = MagicMock()
    ctx = MagicMock()
    ctx.should_skip = True
    ctx.early_result = early

    with patch.object(spectral_plugin, "_prepare_execution", return_value=ctx):
        result = spectral_plugin.check([str(tmp_path)], {})

    assert_that(result).is_same_as(early)


def test_check_handles_timeout(
    spectral_plugin: SpectralPlugin,
    tmp_path: Path,
) -> None:
    """Check surfaces a timeout as a failed result.

    Args:
        spectral_plugin: The SpectralPlugin instance under test.
        tmp_path: Temporary directory path for test files.
    """
    (tmp_path / "openapi.yaml").write_text("openapi: 3.0.0\n")

    with (
        patch.object(spectral_plugin, "_prepare_execution") as mock_prepare,
        patch.object(
            spectral_plugin,
            "_find_ruleset",
            return_value=str(tmp_path / ".spectral.yaml"),
        ),
        patch.object(
            spectral_plugin,
            "_run_subprocess",
            side_effect=subprocess.TimeoutExpired(cmd=["spectral"], timeout=30),
        ),
        patch.object(
            spectral_plugin,
            "_get_spectral_command",
            return_value=["spectral"],
        ),
    ):
        mock_prepare.return_value = _mock_ctx(tmp_path)
        result = spectral_plugin.check([str(tmp_path / "openapi.yaml")], {})

    assert_that(result.name).is_equal_to("spectral")
    assert_that(result.success).is_false()


def test_check_runtime_error_is_not_clean(
    spectral_plugin: SpectralPlugin,
    tmp_path: Path,
) -> None:
    """A non-zero exit with no parseable findings fails instead of passing.

    Args:
        spectral_plugin: The SpectralPlugin instance under test.
        tmp_path: Temporary directory path for test files.
    """
    (tmp_path / "openapi.yaml").write_text("openapi: 3.0.0\n")
    runtime_error = "Error: Cannot find module 'tslib'\n"

    with (
        patch.object(spectral_plugin, "_prepare_execution") as mock_prepare,
        patch.object(
            spectral_plugin,
            "_find_ruleset",
            return_value=str(tmp_path / ".spectral.yaml"),
        ),
        patch.object(
            spectral_plugin,
            "_run_subprocess",
            return_value=(False, runtime_error),
        ),
        patch.object(
            spectral_plugin,
            "_get_spectral_command",
            return_value=["spectral"],
        ),
    ):
        mock_prepare.return_value = _mock_ctx(tmp_path)
        result = spectral_plugin.check([str(tmp_path / "openapi.yaml")], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.output).contains("tslib")
