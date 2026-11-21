"""A file named like an option or argument file cannot switch a real tool off (#3584).

Each test plants a file whose name the tool would read as an option next to a
file with a known finding. Before the fix the tool took the planted name as
an option, skipped the finding and the run passed.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.models.core.tool_result import ToolResult
from lintro.plugins import ToolRegistry
from tests.integration._tools import require_command, require_tool

# Built at runtime so the repository's own typos pass does not correct it.
_MISSPELLING = "t" + "eh"


def _run(
    tool_name: str,
    files: dict[str, str],
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    paths: list[str] | None = None,
) -> ToolResult:
    """Write ``files`` under ``root`` and check them with a fresh plugin.

    Args:
        tool_name: Registered plugin to run.
        files: Relative path to file content.
        root: Project directory.
        monkeypatch: Fixture restoring the working directory.
        paths: Paths to check, as a user names them; the project root when
            omitted.

    Returns:
        The plugin's check result.
    """
    for relative, content in files.items():
        (root / relative).write_text(content, encoding="utf-8")
    monkeypatch.chdir(root)
    plugin = type(ToolRegistry.get(tool_name))()
    return plugin.check(paths or ["."], {})


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


# ``b`` holds the text of an option; ``@b`` would make an argument-file-aware
# tool read ``b`` as its arguments and print its version instead of checking.
_ARGUMENT_FILE_TRAP = {"b": "--version\n"}


@require_tool("tsc")
def test_tsc_at_file_is_checked_not_read_as_arguments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``@b.ts`` is a source file, not a tsc response file naming ``b.ts``.

    Args:
        tmp_path: Project directory without a tsconfig.json.
        monkeypatch: Fixture restoring the working directory.
    """
    result = _run(
        "tsc",
        {
            "a.ts": "const x: number = 's';\nexport {};\n",
            "b.ts": _ARGUMENT_FILE_TRAP["b"],
            "@b.ts": "export {};\n",
        },
        tmp_path,
        monkeypatch,
    )
    files = [Path(file).name for file, _ in _reported(result)]
    assert_that(files).contains("a.ts")
    assert_that(result.success).is_false()


@require_tool("mypy")
def test_mypy_named_at_file_is_checked_not_read_as_arguments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Named explicitly, as the pre-commit hook does, ``@b.py`` stays a file.

    Args:
        tmp_path: Project directory.
        monkeypatch: Fixture restoring the working directory.
    """
    result = _run(
        "mypy",
        {
            "a.py": 'x: int = "s"\n',
            "b.py": _ARGUMENT_FILE_TRAP["b"],
            "@b.py": "x = 1\n",
        },
        tmp_path,
        monkeypatch,
        paths=["a.py", "b.py", "@b.py"],
    )
    files = [Path(file).name for file, _ in _reported(result)]
    assert_that(files).contains("a.py")
    assert_that(result.success).is_false()


# lintro registers markdownlint-cli2 as ``markdownlint``; the pin applies the
# right version floor (see test_markdownlint_integration.py).
@require_command(
    "markdownlint-cli2",
    ["markdownlint-cli2"] if shutil.which("markdownlint-cli2") else None,
    pin="markdownlint",
)
def test_markdownlint_hash_file_is_linted_not_negated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """markdownlint-cli2 reads a leading ``#`` as a negated glob; ``./`` stops it.

    Args:
        tmp_path: Project directory.
        monkeypatch: Fixture restoring the working directory.
    """
    result = _run("markdownlint", {"#x.md": "#Title\n"}, tmp_path, monkeypatch)
    assert_that({file for file, _ in _reported(result)}).is_equal_to({"#x.md"})
    assert_that(result.success).is_false()
