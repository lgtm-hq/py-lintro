"""Unit tests for the PHPStan tool plugin."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from assertpy import assert_that

from lintro.enums.doc_url_template import DocUrlTemplate
from lintro.enums.tool_type import ToolType
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.phpstan.phpstan_issue import PhpstanIssue
from lintro.plugins import ToolRegistry
from lintro.tools.core.version_parsing import get_minimum_versions
from lintro.tools.definitions.phpstan import PhpstanPlugin

# Build the fake --version banner from the canonical version so the test does
# not hardcode a version literal (enforced by test_no_version_literals).
_VERSION_STDOUT = (
    f"PHPStan - PHP Static Analysis Tool {get_minimum_versions()['phpstan']}"
)


def _fake_run_factory(analyse_stdout: str, analyse_rc: int) -> Any:
    """Build a fake ``subprocess.run`` replacement for PHPStan.

    Args:
        analyse_stdout: Stdout returned for the ``analyse`` invocation.
        analyse_rc: Return code for the ``analyse`` invocation.

    Returns:
        A callable suitable for ``monkeypatch.setattr('subprocess.run', ...)``.
    """

    def fake_run(
        cmd: list[str],
        capture_output: bool = True,
        text: bool = True,
        timeout: int | None = None,
        **kwargs: Any,
    ) -> SimpleNamespace:
        if "--version" in cmd and "analyse" not in cmd:
            return SimpleNamespace(stdout=_VERSION_STDOUT, stderr="", returncode=0)
        return SimpleNamespace(
            stdout=analyse_stdout,
            stderr="Instructions for interpreting errors\n",
            returncode=analyse_rc,
        )

    return fake_run


def test_phpstan_tool_definition() -> None:
    """The PHPStan definition exposes the expected metadata."""
    tool = ToolRegistry.get("phpstan")
    assert_that(tool).is_not_none()
    defn = tool.definition
    assert_that(defn.name).is_equal_to("phpstan")
    assert_that(defn.can_fix).is_false()
    assert_that(defn.tool_type).is_equal_to(ToolType.LINTER | ToolType.TYPE_CHECKER)
    assert_that(defn.min_version).is_equal_to(get_minimum_versions()["phpstan"])
    assert_that("*.php" in defn.file_patterns).is_true()
    assert_that("phpstan.neon" in defn.native_configs).is_true()


def test_phpstan_doc_url() -> None:
    """doc_url builds the error-identifier reference URL."""
    tool = ToolRegistry.get("phpstan")
    url = tool.doc_url("function.notFound")
    assert_that(url).is_equal_to(
        DocUrlTemplate.PHPSTAN.format(code="function.notFound"),
    )
    assert_that(tool.doc_url("")).is_none()


def test_phpstan_set_options_validates_level() -> None:
    """set_options accepts a valid level and rejects out-of-range values."""
    tool = ToolRegistry.get("phpstan")
    tool.set_options(level=5)
    assert_that(tool.options.get("level")).is_equal_to(5)

    with pytest.raises(ValueError, match="level must be at most 9"):
        tool.set_options(level=10)

    with pytest.raises(ValueError, match="level must be at least 0"):
        tool.set_options(level=-1)


def test_phpstan_build_command_adds_level_without_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Without a native config, the command includes --level."""
    monkeypatch.chdir(tmp_path)
    tool = cast(PhpstanPlugin, ToolRegistry.get("phpstan"))
    cmd = tool._build_command(files=["a.php"])
    assert_that(cmd).contains("--level")
    assert_that(cmd).contains("--error-format")
    assert_that(cmd).contains("a.php")


def test_phpstan_build_command_omits_level_with_native_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A native phpstan.neon suppresses the injected --level flag."""
    (tmp_path / "phpstan.neon").write_text("parameters:\n    level: 6\n")
    monkeypatch.chdir(tmp_path)
    tool = cast(PhpstanPlugin, ToolRegistry.get("phpstan"))
    cmd = tool._build_command(files=["a.php"])
    assert_that(cmd).does_not_contain("--level")


def test_phpstan_check_reports_violations(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Check parses PHPStan JSON and reports the seeded violations."""
    php = tmp_path / "bad.php"
    php.write_text("<?php\necho add(1);\n")
    payload = {
        "totals": {"errors": 0, "file_errors": 1},
        "files": {
            str(php): {
                "errors": 1,
                "messages": [
                    {
                        "message": "Function add invoked with 1 parameter, 2 required.",
                        "line": 2,
                        "ignorable": True,
                        "identifier": "arguments.count",
                    },
                ],
            },
        },
        "errors": [],
    }
    monkeypatch.setattr(
        "subprocess.run",
        _fake_run_factory(json.dumps(payload), analyse_rc=1),
    )
    tool = ToolRegistry.get("phpstan")
    result: ToolResult = tool.check([str(php)], {})
    assert_that(result.name).is_equal_to("phpstan")
    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)
    assert_that(result.issues).is_not_none()
    issue = cast(PhpstanIssue, result.issues[0])  # type: ignore[index]
    assert_that(issue.identifier).is_equal_to("arguments.count")


def test_phpstan_check_clean_passes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A clean PHPStan run reports success with no issues."""
    php = tmp_path / "good.php"
    php.write_text("<?php\necho 1;\n")
    payload = {
        "totals": {"errors": 0, "file_errors": 0},
        "files": {},
        "errors": [],
    }
    monkeypatch.setattr(
        "subprocess.run",
        _fake_run_factory(json.dumps(payload), analyse_rc=0),
    )
    tool = ToolRegistry.get("phpstan")
    result: ToolResult = tool.check([str(php)], {})
    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_phpstan_fix_raises() -> None:
    """PHPStan does not support fixing and raises NotImplementedError."""
    tool = ToolRegistry.get("phpstan")
    with pytest.raises(NotImplementedError):
        tool.fix(["a.php"], {})


def test_phpstan_crash_with_fatal_error_output_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A non-zero exit with unparseable stdout is a failure, not a pass."""
    php = tmp_path / "app.php"
    php.write_text("<?php\necho 1;\n")
    monkeypatch.setattr(
        "subprocess.run",
        _fake_run_factory(
            "PHP Fatal error: Allowed memory size exhausted",
            analyse_rc=255,
        ),
    )
    tool = ToolRegistry.get("phpstan")
    result: ToolResult = tool.check([str(php)], {})
    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(0)
