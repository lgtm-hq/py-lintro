"""A file named like an option cannot switch a real tool's checks off (#3584).

Each test plants a file whose name the tool would read as an option next to a
file with a known finding. Before the fix the tool took the planted name as
an option, skipped the finding and the run passed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.models.core.tool_result import ToolResult
from lintro.plugins import ToolRegistry
from tests.integration._tools import require_tool

# Built at runtime so the repository's own typos pass does not correct it.
_MISSPELLING = "t" + "eh"


def _run(
    tool_name: str,
    files: dict[str, str],
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> ToolResult:
    """Write ``files`` under ``root`` and check the project with a fresh plugin.

    Args:
        tool_name: Registered plugin to run.
        files: Relative path to file content.
        root: Project directory.
        monkeypatch: Fixture restoring the working directory.

    Returns:
        The plugin's check result.
    """
    for relative, content in files.items():
        (root / relative).write_text(content, encoding="utf-8")
    monkeypatch.chdir(root)
    plugin = type(ToolRegistry.get(tool_name))()
    return plugin.check(["."], {})


def _reported(result: ToolResult) -> list[tuple[str, str]]:
    """List (file, code) pairs for every reported issue.

    Args:
        result: A check result.

    Returns:
        The reported file paths and rule codes.
    """
    return [(str(issue.file), issue.get_code()) for issue in result.issues or []]


@require_tool("ruff")
def test_ruff_config_override_file_does_not_disable_rules(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--config=lint.ignore=['ALL']#.py`` no longer ignores every rule.

    Args:
        tmp_path: Project directory.
        monkeypatch: Fixture restoring the working directory.
    """
    result = _run(
        "ruff",
        {"a.py": "import os\n", "--config=lint.ignore=['ALL']#.py": ""},
        tmp_path,
        monkeypatch,
    )
    codes = [code for _, code in _reported(result)]
    assert_that(codes).contains("F401")
    assert_that(result.success).is_false()


@require_tool("pylint")
def test_pylint_ignore_patterns_file_does_not_hide_modules(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--ignore-patterns=.*.py`` no longer hides every module from pylint.

    Args:
        tmp_path: Project directory.
        monkeypatch: Fixture restoring the working directory.
    """
    result = _run(
        "pylint",
        {"a.py": '"""Doc."""\nimport os\n', "--ignore-patterns=.*.py": ""},
        tmp_path,
        monkeypatch,
    )
    reported = _reported(result)
    assert_that([code for _, code in reported]).contains("W0611")
    # The planted module is linted under its own name, without ``./``.
    assert_that([file for file, _ in reported]).contains("--ignore-patterns=.*.py")


@require_tool("black")
def test_black_force_exclude_file_does_not_skip_formatting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--force-exclude=.py`` no longer makes black skip every file.

    Args:
        tmp_path: Project directory.
        monkeypatch: Fixture restoring the working directory.
    """
    result = _run(
        "black",
        {"a.py": "x=1\n", "--force-exclude=.py": ""},
        tmp_path,
        monkeypatch,
    )
    files = [Path(file).name for file, _ in _reported(result)]
    assert_that(files).contains("a.py")
    assert_that(result.success).is_false()


@require_tool("typos")
def test_typos_dash_file_is_read_as_a_file_not_stdin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A file named ``-`` is checked, not replaced by stdin, and keeps its name.

    Args:
        tmp_path: Project directory.
        monkeypatch: Fixture restoring the working directory.
    """
    result = _run(
        "typos",
        {"a.txt": "ok\n", "-": f"{_MISSPELLING}\n"},
        tmp_path,
        monkeypatch,
    )
    assert_that([file for file, _ in _reported(result)]).is_equal_to(["-"])
    assert_that(result.success).is_false()
