"""Unit tests for ValePlugin execution (check/fix) with mocked subprocess."""

from __future__ import annotations

from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.enums.tool_name import ToolName
from lintro.parsers.vale.vale_issue import ValeIssue
from lintro.tools.definitions.vale import ValePlugin
from tests.test_samples_helpers import copy_sample

from .conftest import make_ctx, vale_output


def _write_md(tmp_path: Path) -> str:
    """Create a Markdown file and return its path.

    Args:
        tmp_path: Temporary directory path.

    Returns:
        Path to the created Markdown file as a string.
    """
    md = copy_sample(
        tmp_path,
        "tools",
        "config",
        "vale",
        "vale_violations.md",
        dest_name="doc.md",
    )
    return str(md)


def test_check_with_issues(vale_plugin: ValePlugin, tmp_path: Path) -> None:
    """Check returns issues when Vale reports alerts.

    Args:
        vale_plugin: The ValePlugin instance under test.
        tmp_path: Temporary directory path.
    """
    md = _write_md(tmp_path)
    output = vale_output(
        {
            "doc.md": [
                {
                    "Span": [1, 7],
                    "Check": "Vale.Repetition",
                    "Link": "",
                    "Message": "'the' is repeated!",
                    "Severity": "error",
                    "Match": "The the",
                    "Line": 1,
                },
            ],
        },
    )

    with (
        patch.object(vale_plugin, "_prepare_execution") as mock_prepare,
        patch.object(vale_plugin, "_run_subprocess", return_value=(False, output)),
    ):
        mock_prepare.return_value = make_ctx(str(tmp_path), [md])

        result = vale_plugin.check([md], {})

    assert_that(result.name).is_equal_to(ToolName.VALE)
    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)
    assert_that(result.issues).is_not_none()
    issue = cast(ValeIssue, result.issues[0])  # type: ignore[index]
    assert_that(issue.check).is_equal_to("Vale.Repetition")
    assert_that(issue.style).is_equal_to("Vale")


def test_check_clean(vale_plugin: ValePlugin, tmp_path: Path) -> None:
    """Check succeeds with no issues when Vale reports an empty mapping.

    Args:
        vale_plugin: The ValePlugin instance under test.
        tmp_path: Temporary directory path.
    """
    md = _write_md(tmp_path)

    with (
        patch.object(vale_plugin, "_prepare_execution") as mock_prepare,
        patch.object(vale_plugin, "_run_subprocess", return_value=(True, "{}")),
    ):
        mock_prepare.return_value = make_ctx(str(tmp_path), [md])

        result = vale_plugin.check([md], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.output).is_none()


def test_check_skips_when_no_config(vale_plugin: ValePlugin, tmp_path: Path) -> None:
    """Check skips as a non-error when Vale reports a missing config.

    Args:
        vale_plugin: The ValePlugin instance under test.
        tmp_path: Temporary directory path.
    """
    md = _write_md(tmp_path)
    e100 = (
        '{"Line": 0, "Path": "", "Text": "E100 [.vale.ini not found] '
        'Runtime error\\n\\nno config file found", "Code": "E100", "Span": 0}'
    )

    with (
        patch.object(vale_plugin, "_prepare_execution") as mock_prepare,
        patch.object(vale_plugin, "_run_subprocess", return_value=(False, e100)),
    ):
        mock_prepare.return_value = make_ctx(str(tmp_path), [md])

        result = vale_plugin.check([md], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.output).contains("Skipping vale")


def test_check_returns_early_when_skipping(
    vale_plugin: ValePlugin,
    tmp_path: Path,
) -> None:
    """Check returns the early result when preparation says to skip.

    Args:
        vale_plugin: The ValePlugin instance under test.
        tmp_path: Temporary directory path.
    """
    from unittest.mock import MagicMock

    early = MagicMock()
    ctx = MagicMock()
    ctx.should_skip = True
    ctx.early_result = early

    with patch.object(vale_plugin, "_prepare_execution", return_value=ctx):
        result = vale_plugin.check(["nonexistent"], {})

    assert_that(result).is_same_as(early)


def test_fix_raises_not_implemented(vale_plugin: ValePlugin) -> None:
    """Fix raises NotImplementedError since Vale cannot auto-fix.

    Args:
        vale_plugin: The ValePlugin instance under test.
    """
    with pytest.raises(NotImplementedError):
        vale_plugin.fix(["doc.md"], {})


def test_check_runtime_error_is_not_clean(
    vale_plugin: ValePlugin,
    tmp_path: Path,
) -> None:
    """A non-zero exit with no parseable alerts fails instead of passing.

    Args:
        vale_plugin: The ValePlugin instance under test.
        tmp_path: Temporary directory path.
    """
    md = _write_md(tmp_path)
    runtime_error = "E201 [styles path does not exist] Runtime error"

    with (
        patch.object(vale_plugin, "_prepare_execution") as mock_prepare,
        patch.object(
            vale_plugin,
            "_run_subprocess",
            return_value=(False, runtime_error),
        ),
    ):
        mock_prepare.return_value = make_ctx(str(tmp_path), [md])

        result = vale_plugin.check([md], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.output).contains("E201")
