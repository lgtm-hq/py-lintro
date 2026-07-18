"""Unit tests for the j2lint plugin.

Covers the tool definition attributes, option validation and command
building, and the check path with a mocked subprocess so no real j2lint
binary is required.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.enums.tool_type import ToolType
from lintro.parsers.j2lint.j2lint_issue import J2lintIssue
from lintro.plugins.subprocess_executor import SubprocessResult
from lintro.tools.definitions.j2lint import J2lintPlugin


@pytest.fixture
def j2lint_plugin() -> J2lintPlugin:
    """Provide a J2lintPlugin instance for testing.

    Returns:
        A J2lintPlugin instance.
    """
    return J2lintPlugin()


def _report(errors: list[dict[str, object]], warnings: list[dict[str, object]]) -> str:
    """Build a j2lint JSON report string.

    Args:
        errors: Entries to place under the ERRORS bucket.
        warnings: Entries to place under the WARNINGS bucket.

    Returns:
        A JSON string mirroring j2lint's ``--json`` output.
    """
    return json.dumps({"ERRORS": errors, "WARNINGS": warnings})


def test_definition_attributes(j2lint_plugin: J2lintPlugin) -> None:
    """The definition exposes the expected metadata."""
    definition = j2lint_plugin.definition
    assert_that(definition.name).is_equal_to("j2lint")
    assert_that(definition.can_fix).is_false()
    assert_that(definition.tool_type).is_equal_to(ToolType.LINTER)
    assert_that(definition.priority).is_equal_to(60)
    assert_that(definition.version_command).is_equal_to(["j2lint", "--version"])


def test_definition_file_patterns(j2lint_plugin: J2lintPlugin) -> None:
    """The definition targets non-HTML Jinja2 extensions."""
    assert_that(j2lint_plugin.definition.file_patterns).is_equal_to(
        ["*.j2", "*.jinja", "*.jinja2"],
    )


def test_native_configs(j2lint_plugin: J2lintPlugin) -> None:
    """The definition declares the native config file."""
    assert_that(j2lint_plugin.definition.native_configs).contains(".j2lint.yaml")


def test_set_options_valid(j2lint_plugin: J2lintPlugin) -> None:
    """Valid ignore/warn lists are stored on the plugin options."""
    j2lint_plugin.set_options(ignore=["S1"], warn=["S3"])
    assert_that(j2lint_plugin.options.get("ignore")).is_equal_to(["S1"])
    assert_that(j2lint_plugin.options.get("warn")).is_equal_to(["S3"])


def test_set_options_invalid_ignore_raises(j2lint_plugin: J2lintPlugin) -> None:
    """A non-list ignore value raises a validation error."""
    assert_that(j2lint_plugin.set_options).raises(Exception).when_called_with(
        ignore="S1",
    )


def test_build_command_default(j2lint_plugin: J2lintPlugin) -> None:
    """The base command requests JSON and terminates options with ``--``."""
    cmd = j2lint_plugin._build_command(files=["a.j2"])
    assert_that(cmd).contains("--json")
    assert_that(cmd).contains("--")
    assert_that(cmd[-1]).is_equal_to("a.j2")
    assert_that(cmd).does_not_contain("-i")
    assert_that(cmd).does_not_contain("-w")


def test_build_command_with_ignore_and_warn(j2lint_plugin: J2lintPlugin) -> None:
    """Ignore and warn options add ``-i``/``-w`` before the file separator."""
    j2lint_plugin.set_options(ignore=["S1", "S2"], warn=["S3"])
    cmd = j2lint_plugin._build_command(files=["a.j2"])
    assert_that(cmd).contains("-i", "S1", "S2")
    assert_that(cmd).contains("-w", "S3")
    # Files must come after the ``--`` separator.
    assert_that(cmd.index("--")).is_greater_than(cmd.index("-w"))
    assert_that(cmd.index("a.j2")).is_greater_than(cmd.index("--"))


def test_doc_url(j2lint_plugin: J2lintPlugin) -> None:
    """doc_url returns a URL for a code and None for an empty code."""
    assert_that(j2lint_plugin.doc_url("S3")).is_not_none()
    assert_that(j2lint_plugin.doc_url("")).is_none()


def _run_check(
    plugin: J2lintPlugin,
    tmp_path: Path,
    mock_output: str,
    *,
    success: bool = False,
) -> object:
    """Run check() against a mocked subprocess and prepared context.

    Args:
        plugin: The plugin under test.
        tmp_path: Temporary directory for the template file.
        mock_output: The stdout the mocked subprocess returns.
        success: The subprocess success flag to simulate.

    Returns:
        The ToolResult produced by check().
    """
    template = tmp_path / "template.j2"
    template.write_text("{{ x }}\n")

    subprocess_result = SubprocessResult(
        returncode=0 if success else 1,
        stdout=mock_output,
        stderr="",
        output=mock_output,
    )

    with (
        patch.object(plugin, "_prepare_execution") as mock_prepare,
        patch.object(
            plugin,
            "_run_subprocess_result",
            return_value=subprocess_result,
        ),
    ):
        mock_ctx = MagicMock()
        mock_ctx.should_skip = False
        mock_ctx.early_result = None
        mock_ctx.timeout = 30
        mock_ctx.cwd = str(tmp_path)
        mock_ctx.files = [str(template)]
        mock_prepare.return_value = mock_ctx

        return plugin.check([str(template)], {})


def test_check_with_issues(j2lint_plugin: J2lintPlugin, tmp_path: Path) -> None:
    """check() reports issues and fails when errors are present."""
    output = _report(
        errors=[
            {
                "id": "S3",
                "message": "Bad Indentation",
                "filename": "template.j2",
                "line_number": 3,
                "line": "{%- x %}",
                "severity": "HIGH",
            },
        ],
        warnings=[],
    )
    result = _run_check(j2lint_plugin, tmp_path, output)
    assert_that(result.success).is_false()  # type: ignore[attr-defined]
    assert_that(result.issues_count).is_equal_to(1)  # type: ignore[attr-defined]
    issue = cast(J2lintIssue, result.issues[0])  # type: ignore[attr-defined]
    assert_that(issue.code).is_equal_to("S3")
    assert_that(issue.level).is_equal_to("error")


def test_check_clean_output(j2lint_plugin: J2lintPlugin, tmp_path: Path) -> None:
    """check() succeeds and reports no issues for a clean report."""
    output = _report(errors=[], warnings=[])
    result = _run_check(j2lint_plugin, tmp_path, output, success=True)
    assert_that(result.success).is_true()  # type: ignore[attr-defined]
    assert_that(result.issues_count).is_equal_to(0)  # type: ignore[attr-defined]


def test_check_nonzero_exit_without_issues_fails(
    j2lint_plugin: J2lintPlugin,
    tmp_path: Path,
) -> None:
    """A non-zero exit with no parseable output fails instead of passing clean.

    Guards against a crash, bad arguments, or malformed output being reported
    as a clean pass because the parsed issue list happened to be empty.
    """
    result = _run_check(
        j2lint_plugin,
        tmp_path,
        "Traceback (most recent call last): boom",
        success=False,
    )
    assert_that(result.success).is_false()  # type: ignore[attr-defined]
    assert_that(result.issues_count).is_equal_to(0)  # type: ignore[attr-defined]


def test_check_stderr_does_not_corrupt_json_parsing(
    j2lint_plugin: J2lintPlugin,
    tmp_path: Path,
) -> None:
    """Stderr braces must not shift the stdout JSON bounds during parsing.

    Only stdout is parsed, so a stderr warning containing ``{`` cannot cause
    the parser to select the wrong JSON object and drop the real findings.
    """
    template = tmp_path / "template.j2"
    template.write_text("{{ x }}\n")

    stdout = _report(
        errors=[
            {
                "id": "S3",
                "message": "Bad Indentation",
                "filename": "template.j2",
                "line_number": 3,
                "line": "{%- x %}",
                "severity": "HIGH",
            },
        ],
        warnings=[],
    )
    subprocess_result = SubprocessResult(
        returncode=1,
        stdout=stdout,
        stderr="WARNING: stray brace { in stderr }",
        output=stdout + "\nWARNING: stray brace { in stderr }",
    )

    with (
        patch.object(j2lint_plugin, "_prepare_execution") as mock_prepare,
        patch.object(
            j2lint_plugin,
            "_run_subprocess_result",
            return_value=subprocess_result,
        ),
    ):
        mock_ctx = MagicMock()
        mock_ctx.should_skip = False
        mock_ctx.early_result = None
        mock_ctx.timeout = 30
        mock_ctx.cwd = str(tmp_path)
        mock_ctx.files = [str(template)]
        mock_prepare.return_value = mock_ctx

        result = j2lint_plugin.check([str(template)], {})

    assert_that(result.success).is_false()  # type: ignore[attr-defined]
    assert_that(result.issues_count).is_equal_to(1)  # type: ignore[attr-defined]
    issue = cast(J2lintIssue, result.issues[0])  # type: ignore[attr-defined]
    assert_that(issue.code).is_equal_to("S3")


def test_check_warnings_only_succeeds(
    j2lint_plugin: J2lintPlugin,
    tmp_path: Path,
) -> None:
    """Warning-only reports are surfaced but do not fail the check."""
    output = _report(
        errors=[],
        warnings=[
            {
                "id": "S6",
                "message": "delimiter",
                "filename": "template.j2",
                "line_number": 3,
                "line": "{%- x %}",
                "severity": "LOW",
            },
        ],
    )
    result = _run_check(j2lint_plugin, tmp_path, output)
    assert_that(result.success).is_true()  # type: ignore[attr-defined]
    assert_that(result.issues_count).is_equal_to(1)  # type: ignore[attr-defined]


def test_check_no_files_returns_early(
    j2lint_plugin: J2lintPlugin,
    tmp_path: Path,
) -> None:
    """check() short-circuits with success when no files are discovered."""
    with patch.object(j2lint_plugin, "_prepare_execution") as mock_prepare:
        mock_ctx = MagicMock()
        mock_ctx.should_skip = False
        mock_ctx.early_result = None
        mock_ctx.files = []
        mock_prepare.return_value = mock_ctx

        result = j2lint_plugin.check([str(tmp_path)], {})
        assert_that(result.success).is_true()
        assert_that(result.issues_count).is_equal_to(0)


def test_fix_raises_not_implemented(j2lint_plugin: J2lintPlugin) -> None:
    """fix() is unsupported and raises NotImplementedError."""
    assert_that(j2lint_plugin.fix).raises(NotImplementedError).when_called_with(
        ["a.j2"],
        {},
    )
