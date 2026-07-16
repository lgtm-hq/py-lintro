"""Tests for StylelintPlugin.check method."""

from __future__ import annotations

import subprocess  # nosec B404 - subprocess is used for TimeoutExpired in mocked tool execution tests
from pathlib import Path
from typing import cast
from unittest.mock import patch

from assertpy import assert_that

from lintro.parsers.stylelint.stylelint_issue import StylelintIssue
from lintro.tools.definitions.stylelint import StylelintPlugin
from tests.unit.tools.stylelint.conftest import make_ctx

WARNINGS_JSON = (
    '[{"source":"/tmp/a.css","warnings":[{"line":2,"column":10,'
    '"rule":"color-hex-length","severity":"error",'
    '"text":"Expected \\"#FFFFFF\\" to be \\"#FFF\\" (color-hex-length)"}]}]'
)
CLEAN_JSON = '[{"source":"/tmp/a.css","errored":false,"warnings":[]}]'
NO_CONFIG = "ConfigurationError: No configuration provided for /tmp/a.css"


def test_check_with_issues(
    stylelint_plugin: StylelintPlugin,
    tmp_path: Path,
) -> None:
    """Check reports issues and fails when stylelint finds violations."""
    (tmp_path / "a.css").write_text("a { color: #FFFFFF; }\n")

    with (
        patch.object(stylelint_plugin, "_prepare_execution") as prep,
        patch.object(
            stylelint_plugin,
            "_run_subprocess",
            return_value=(False, WARNINGS_JSON),
        ),
        patch.object(
            stylelint_plugin,
            "_get_executable_command",
            return_value=["stylelint"],
        ),
    ):
        prep.return_value = make_ctx(tmp_path, ["a.css"])
        result = stylelint_plugin.check([str(tmp_path / "a.css")], {})

    assert_that(result.name).is_equal_to("stylelint")
    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)
    assert_that(result.issues).is_not_none()
    issue = cast(StylelintIssue, result.issues[0])  # type: ignore[index]
    assert_that(issue.code).is_equal_to("color-hex-length")
    assert_that(result.output).is_not_none()


def test_check_without_issues(
    stylelint_plugin: StylelintPlugin,
    tmp_path: Path,
) -> None:
    """Check succeeds and suppresses output when no issues are found."""
    (tmp_path / "a.css").write_text("a { color: #fff; }\n")

    with (
        patch.object(stylelint_plugin, "_prepare_execution") as prep,
        patch.object(
            stylelint_plugin,
            "_run_subprocess",
            return_value=(True, CLEAN_JSON),
        ),
        patch.object(
            stylelint_plugin,
            "_get_executable_command",
            return_value=["stylelint"],
        ),
    ):
        prep.return_value = make_ctx(tmp_path, ["a.css"])
        result = stylelint_plugin.check([str(tmp_path / "a.css")], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.output).is_none()


def test_check_skips_when_no_config(
    stylelint_plugin: StylelintPlugin,
    tmp_path: Path,
) -> None:
    """Check returns a non-error skip when no stylelint config exists."""
    (tmp_path / "a.css").write_text("a { color: red; }\n")

    with (
        patch.object(stylelint_plugin, "_prepare_execution") as prep,
        patch.object(
            stylelint_plugin,
            "_run_subprocess",
            return_value=(False, NO_CONFIG),
        ),
        patch.object(
            stylelint_plugin,
            "_get_executable_command",
            return_value=["stylelint"],
        ),
    ):
        prep.return_value = make_ctx(tmp_path, ["a.css"])
        result = stylelint_plugin.check([str(tmp_path / "a.css")], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.output).contains("no stylelint configuration")


def test_check_timeout(
    stylelint_plugin: StylelintPlugin,
    tmp_path: Path,
) -> None:
    """Check handles subprocess timeouts gracefully."""
    (tmp_path / "a.css").write_text("a { color: red; }\n")

    with (
        patch.object(stylelint_plugin, "_prepare_execution") as prep,
        patch.object(
            stylelint_plugin,
            "_run_subprocess",
            side_effect=subprocess.TimeoutExpired(cmd=["stylelint"], timeout=30),
        ),
        patch.object(
            stylelint_plugin,
            "_get_executable_command",
            return_value=["stylelint"],
        ),
    ):
        prep.return_value = make_ctx(tmp_path, ["a.css"])
        result = stylelint_plugin.check([str(tmp_path / "a.css")], {})

    assert_that(result.success).is_false()
    assert_that(result.output).contains("timed out")


def test_check_passes_config_option(
    stylelint_plugin: StylelintPlugin,
    tmp_path: Path,
) -> None:
    """An explicit config option is threaded into the command."""
    (tmp_path / "a.css").write_text("a { color: #fff; }\n")
    stylelint_plugin.set_options(config="my.stylelintrc.json")

    with (
        patch.object(stylelint_plugin, "_prepare_execution") as prep,
        patch.object(
            stylelint_plugin,
            "_run_subprocess",
            return_value=(True, CLEAN_JSON),
        ) as run,
        patch.object(
            stylelint_plugin,
            "_get_executable_command",
            return_value=["stylelint"],
        ),
    ):
        prep.return_value = make_ctx(tmp_path, ["a.css"])
        stylelint_plugin.check([str(tmp_path / "a.css")], {})

    cmd = run.call_args.kwargs["cmd"]
    assert_that(cmd).contains("--config")
    assert_that(cmd).contains("my.stylelintrc.json")
    assert_that(cmd).contains("--formatter")
    assert_that(cmd).contains("json")


def test_check_skips_when_ctx_should_skip(
    stylelint_plugin: StylelintPlugin,
    tmp_path: Path,
) -> None:
    """Check returns the early result when preparation signals a skip."""
    from lintro.models.core.tool_result import ToolResult

    early = ToolResult(name="stylelint", success=True, output=None, issues_count=0)
    with patch.object(stylelint_plugin, "_prepare_execution") as prep:
        ctx = make_ctx(tmp_path, [])
        ctx.should_skip = True
        ctx.early_result = early
        prep.return_value = ctx
        result = stylelint_plugin.check([str(tmp_path)], {})

    assert_that(result).is_same_as(early)


def test_check_runtime_error_is_not_clean(
    stylelint_plugin: StylelintPlugin,
    tmp_path: Path,
) -> None:
    """A non-zero exit with nothing parsed fails instead of passing."""
    (tmp_path / "a.css").write_text("a { color: #fff; }\n")

    with (
        patch.object(stylelint_plugin, "_prepare_execution") as prep,
        patch.object(
            stylelint_plugin,
            "_run_subprocess",
            return_value=(False, "Error: Cannot find module 'postcss'"),
        ),
        patch.object(
            stylelint_plugin,
            "_get_executable_command",
            return_value=["stylelint"],
        ),
    ):
        prep.return_value = make_ctx(tmp_path, ["a.css"])
        result = stylelint_plugin.check([str(tmp_path / "a.css")], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.output).contains("postcss")


def test_check_per_call_config_reaches_command(
    stylelint_plugin: StylelintPlugin,
    tmp_path: Path,
) -> None:
    """A config passed per-call (not via set_options) lands on the command."""
    (tmp_path / "a.css").write_text("a { color: #fff; }\n")
    seen_cmds: list[list[str]] = []

    def _capture(cmd: list[str], **kwargs: object) -> tuple[bool, str]:
        seen_cmds.append(cmd)
        return (True, CLEAN_JSON)

    with (
        patch.object(stylelint_plugin, "_prepare_execution") as prep,
        patch.object(
            stylelint_plugin,
            "_run_subprocess",
            side_effect=_capture,
        ),
        patch.object(
            stylelint_plugin,
            "_get_executable_command",
            return_value=["stylelint"],
        ),
    ):
        prep.return_value = make_ctx(tmp_path, ["a.css"])
        stylelint_plugin.check(
            [str(tmp_path / "a.css")],
            {"config": "custom/.stylelintrc.json"},
        )

    assert_that(seen_cmds).is_length(1)
    assert_that(seen_cmds[0]).contains("--config", "custom/.stylelintrc.json")
