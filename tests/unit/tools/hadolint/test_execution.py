"""Unit tests for hadolint plugin execution methods."""

from __future__ import annotations

from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, patch

from assertpy import assert_that

from lintro.enums.tool_name import ToolName
from lintro.parsers.hadolint.hadolint_issue import HadolintIssue
from lintro.tools.definitions.hadolint import HadolintPlugin
from tests.test_samples_helpers import copy_sample

# =============================================================================
# Tests for HadolintPlugin.check method with mocked subprocess
# =============================================================================


def test_check_with_issues(hadolint_plugin: HadolintPlugin, tmp_path: Path) -> None:
    """Check returns issues when found.

    Args:
        hadolint_plugin: The HadolintPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    import pathlib

    dockerfile = pathlib.Path(tmp_path) / "Dockerfile"
    dockerfile = copy_sample(
        tmp_path,
        "tools",
        "config",
        "docker",
        "Dockerfile.simple",
        dest_name="Dockerfile",
    )

    mock_output = "Dockerfile:1 DL3006 warning: Always tag the version of an image"
    mock_result = (False, mock_output)

    with (
        patch.object(hadolint_plugin, "_prepare_execution") as mock_prepare,
        patch.object(hadolint_plugin, "_run_subprocess", return_value=mock_result),
    ):
        mock_ctx = MagicMock()
        mock_ctx.should_skip = False
        mock_ctx.early_result = None
        mock_ctx.timeout = 30
        mock_ctx.cwd = str(tmp_path)
        mock_ctx.files = [str(dockerfile)]
        mock_prepare.return_value = mock_ctx

        result = hadolint_plugin.check([str(dockerfile)], {})

        assert_that(result.name).is_equal_to(ToolName.HADOLINT)
        assert_that(result.success).is_false()
        assert_that(result.issues_count).is_equal_to(1)
        assert_that(result.issues).is_not_none()
        issue = cast(HadolintIssue, result.issues[0])  # type: ignore[index]  # validated via is_not_none
        assert_that(issue.code).is_equal_to("DL3006")


def test_check_multiple_files(hadolint_plugin: HadolintPlugin, tmp_path: Path) -> None:
    """Check handles multiple Dockerfiles.

    Args:
        hadolint_plugin: The HadolintPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    import pathlib

    dockerfile1 = pathlib.Path(tmp_path) / "Dockerfile"
    dockerfile1.write_text("FROM python:3.11\n")
    dockerfile2 = pathlib.Path(tmp_path) / "Dockerfile.dev"
    dockerfile2.write_text("FROM python:3.11-slim\n")

    mock_result = (True, "")

    with (
        patch.object(hadolint_plugin, "_prepare_execution") as mock_prepare,
        patch.object(hadolint_plugin, "_run_subprocess", return_value=mock_result),
    ):
        mock_ctx = MagicMock()
        mock_ctx.should_skip = False
        mock_ctx.early_result = None
        mock_ctx.timeout = 30
        mock_ctx.cwd = str(tmp_path)
        mock_ctx.files = [str(dockerfile1), str(dockerfile2)]
        mock_prepare.return_value = mock_ctx

        result = hadolint_plugin.check([str(dockerfile1), str(dockerfile2)], {})

        assert_that(result.success).is_true()


# =============================================================================
# Tests for output parsing
# =============================================================================


def test_parse_single_issue(hadolint_plugin: HadolintPlugin, tmp_path: Path) -> None:
    """Parse single issue from hadolint output.

    Args:
        hadolint_plugin: The HadolintPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    import pathlib

    dockerfile = pathlib.Path(tmp_path) / "Dockerfile"
    dockerfile = copy_sample(
        tmp_path,
        "tools",
        "config",
        "docker",
        "Dockerfile.simple",
        dest_name="Dockerfile",
    )

    mock_output = (
        "Dockerfile:1 DL3006 error: Always tag the version of an image explicitly"
    )
    mock_result = (False, mock_output)

    with (
        patch.object(hadolint_plugin, "_prepare_execution") as mock_prepare,
        patch.object(hadolint_plugin, "_run_subprocess", return_value=mock_result),
    ):
        mock_ctx = MagicMock()
        mock_ctx.should_skip = False
        mock_ctx.early_result = None
        mock_ctx.timeout = 30
        mock_ctx.cwd = str(tmp_path)
        mock_ctx.files = [str(dockerfile)]
        mock_prepare.return_value = mock_ctx

        result = hadolint_plugin.check([str(dockerfile)], {})

        assert_that(result.issues_count).is_equal_to(1)
        assert_that(result.issues).is_not_none()
        issue = cast(HadolintIssue, result.issues[0])  # type: ignore[index]  # validated via is_not_none
        assert_that(issue.file).is_equal_to("Dockerfile")
        assert_that(issue.line).is_equal_to(1)
        assert_that(issue.code).is_equal_to("DL3006")
        assert_that(issue.level).is_equal_to("error")
        assert_that(issue.message).contains("tag the version")


def test_parse_multiple_issues(hadolint_plugin: HadolintPlugin, tmp_path: Path) -> None:
    """Parse multiple issues from hadolint output.

    Args:
        hadolint_plugin: The HadolintPlugin instance to test.
        tmp_path: Temporary directory path for test files.
    """
    import pathlib

    dockerfile = pathlib.Path(tmp_path) / "Dockerfile"
    dockerfile = copy_sample(
        tmp_path,
        "tools",
        "config",
        "docker",
        "Dockerfile.with_apt_update",
        dest_name="Dockerfile",
    )

    mock_output = (
        "Dockerfile:1 DL3006 error: Always tag the version of an image explicitly\n"
        "Dockerfile:2 DL3009 info: Delete the apt-get lists after installing something"
    )
    mock_result = (False, mock_output)

    with (
        patch.object(hadolint_plugin, "_prepare_execution") as mock_prepare,
        patch.object(hadolint_plugin, "_run_subprocess", return_value=mock_result),
    ):
        mock_ctx = MagicMock()
        mock_ctx.should_skip = False
        mock_ctx.early_result = None
        mock_ctx.timeout = 30
        mock_ctx.cwd = str(tmp_path)
        mock_ctx.files = [str(dockerfile)]
        mock_prepare.return_value = mock_ctx

        result = hadolint_plugin.check([str(dockerfile)], {})

        assert_that(result.issues_count).is_equal_to(2)
        assert_that(result.issues).is_not_none()
        first_issue = cast(HadolintIssue, result.issues[0])  # type: ignore[index]  # validated via is_not_none
        second_issue = cast(HadolintIssue, result.issues[1])  # type: ignore[index]  # validated via is_not_none
        assert_that(first_issue.code).is_equal_to("DL3006")
        assert_that(second_issue.code).is_equal_to("DL3009")
