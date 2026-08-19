"""Tests for no-config language-scoped tool selection in get_tools_to_run.

Covers issue #1420: a no-config first run should scope the toolset to the
languages actually present in the project instead of firing every registered
tool. Explicit ``--tools`` and configured projects keep the full behavior.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.config.config_loader import clear_config_cache
from lintro.utils.execution.tool_configuration import (
    format_detection_notice,
    get_tools_to_run,
)


def _write_python_project(root: Path) -> None:
    """Create a minimal Python-only project in *root*.

    Args:
        root: Directory to populate.
    """
    (root / "pyproject.toml").write_text(
        '[project]\nname = "sample"\nversion = "0.0.0"\n',
        encoding="utf-8",
    )
    (root / "main.py").write_text("x = 1\n", encoding="utf-8")


@pytest.fixture(autouse=True)
def _reset_config_cache() -> Iterator[None]:
    """Reset the config singleton before and after each test.

    Yields:
        None: Control back to the test after cache reset.
    """
    clear_config_cache()
    yield
    clear_config_cache()


def test_no_config_python_project_scopes_to_python_tools(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """No-config Python project scopes the toolset to Python-relevant tools.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Temporary project directory.
    """
    _write_python_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    clear_config_cache()

    result = get_tools_to_run(tools=None, action="check")

    assert_that(result.scoped_by_detection).is_true()
    assert_that(result.detected_languages).contains("python")
    # Python tools survive scoping.
    assert_that(result.to_run).contains("ruff", "black", "mypy", "bandit")
    # Tools for absent languages are dropped entirely (no SKIP wall).
    assert_that(result.to_run).does_not_contain(
        "clippy",
        "rustfmt",
        "svelte-check",
        "sqlfluff",
    )
    skipped_names = [s.name for s in result.skipped]
    assert_that(skipped_names).does_not_contain("clippy", "svelte-check")


def test_explicit_tools_overrides_detection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Explicit ``--tools`` bypasses language scoping.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Temporary project directory.
    """
    _write_python_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    clear_config_cache()

    result = get_tools_to_run(tools="yamllint", action="check")

    assert_that(result.scoped_by_detection).is_false()
    assert_that(result.to_run).is_equal_to(["yamllint"])


def test_explicit_all_is_not_scoped(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Explicit ``--tools all`` runs the full toolset without scoping.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Temporary project directory.
    """
    _write_python_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    clear_config_cache()

    result = get_tools_to_run(tools="all", action="check")

    assert_that(result.scoped_by_detection).is_false()
    # Non-Python tools remain candidates under explicit "all".
    assert_that(result.to_run).contains("ruff", "clippy", "sqlfluff")


def test_configured_project_not_scoped(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A project with a config file keeps the full, unscoped behavior.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Temporary project directory.
    """
    _write_python_project(tmp_path)
    (tmp_path / ".lintro-config.yaml").write_text(
        "tools:\n  ruff:\n    enabled: true\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    clear_config_cache()

    result = get_tools_to_run(tools=None, action="check")

    assert_that(result.scoped_by_detection).is_false()
    # With a config present, non-Python tools are candidates (enabled by
    # default), not silently dropped by detection.
    assert_that(result.to_run).contains("ruff", "clippy", "sqlfluff")


def test_format_detection_notice_groups_by_language() -> None:
    """The notice groups tools by language and points at ``lintro init``."""
    notice = format_detection_notice(
        detected_languages=["python"],
        to_run=["ruff", "black", "mypy", "bandit"],
    )

    assert_that(notice).contains("No config found")
    assert_that(notice).contains("python: bandit, black, mypy, ruff")
    assert_that(notice).contains("lintro init")


@pytest.mark.parametrize(
    ("files", "language", "expected_tool"),
    [
        (
            {"package.json": '{"devDependencies":{"svelte":"4.0.0"}}\n'},
            "svelte",
            "svelte-check",
        ),
        (
            {"package.json": '{"devDependencies":{"astro":"4.0.0"}}\n'},
            "astro",
            "astro-check",
        ),
        (
            {"package.json": '{"devDependencies":{"vue":"3.0.0"}}\n'},
            "vue",
            "vue-tsc",
        ),
        ({"index.html": "<html></html>\n"}, "html", "html_validate"),
        ({"app.css": "body { color: black; }\n"}, "css", "stylelint"),
        ({".env": "FOO=bar\n"}, "dotenv", "dotenv_linter"),
    ],
    ids=["svelte", "astro", "vue", "html", "css", "dotenv"],
)
def test_no_config_scopes_hyphenated_and_markup_tools(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    files: dict[str, str],
    language: str,
    expected_tool: str,
) -> None:
    """Hyphenated checkers and HTML/CSS tools survive no-config scoping.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Temporary project directory.
        files: Files to write in the temp project.
        language: Expected detected language identifier.
        expected_tool: Registered tool name that must remain in ``to_run``.
    """
    for relative, contents in files.items():
        (tmp_path / relative).write_text(contents, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    clear_config_cache()

    result = get_tools_to_run(tools=None, action="check")

    assert_that(result.scoped_by_detection).is_true()
    assert_that(result.detected_languages).contains(language)
    assert_that(result.to_run).contains(expected_tool)


def test_format_detection_notice_aliases_hyphenated_tools() -> None:
    """Notice grouping matches hyphenated registry names to underscored maps."""
    notice = format_detection_notice(
        detected_languages=["svelte"],
        to_run=["svelte-check", "gitleaks"],
    )

    assert_that(notice).contains("svelte: svelte-check")
    assert_that(notice).contains("security: gitleaks")


def test_no_config_source_only_python_scopes_to_python_tools(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A lone ``*.py`` with no pyproject still selects Python tools.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Temporary project directory.
    """
    (tmp_path / "main.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    clear_config_cache()

    result = get_tools_to_run(tools=None, action="check")

    assert_that(result.scoped_by_detection).is_true()
    assert_that(result.detected_languages).contains("python")
    assert_that(result.to_run).contains("ruff", "black", "mypy", "bandit")
    assert_that(result.to_run).does_not_contain("clippy", "rustfmt")


def test_no_config_source_only_javascript_scopes_to_js_tools(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A lone ``*.js`` with no package.json still selects JS formatters.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Temporary project directory.
    """
    (tmp_path / "index.js").write_text("console.log(1)\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    clear_config_cache()

    result = get_tools_to_run(tools=None, action="check")

    assert_that(result.scoped_by_detection).is_true()
    assert_that(result.detected_languages).contains("javascript")
    assert_that(result.to_run).contains("prettier")
    assert_that(result.to_run).does_not_contain("ruff", "clippy")


def test_no_config_empty_cwd_keeps_security_tools_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An empty directory still scopes, dropping language tools.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Temporary project directory.
    """
    monkeypatch.chdir(tmp_path)
    clear_config_cache()

    result = get_tools_to_run(tools=None, action="check")

    assert_that(result.scoped_by_detection).is_true()
    assert_that(result.detected_languages).is_empty()
    assert_that(result.to_run).contains("gitleaks")
    assert_that(result.to_run).does_not_contain("ruff", "clippy", "prettier")


def test_no_config_fmt_action_is_language_scoped(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Format (``fmt``) uses the same no-config language allowlist as check.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Temporary project directory.
    """
    _write_python_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    clear_config_cache()

    result = get_tools_to_run(tools=None, action="fmt")

    assert_that(result.scoped_by_detection).is_true()
    assert_that(result.detected_languages).contains("python")
    assert_that(result.to_run).contains("black")
    assert_that(result.to_run).does_not_contain("clippy", "rustfmt")


def test_empty_tool_lintro_table_still_scopes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An empty ``[tool.lintro]`` table is not a config, so scoping applies.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Temporary project directory.
    """
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "sample"\nversion = "0.0.0"\n\n[tool.lintro]\n',
        encoding="utf-8",
    )
    (tmp_path / "main.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    clear_config_cache()

    result = get_tools_to_run(tools=None, action="check")

    assert_that(result.scoped_by_detection).is_true()
    assert_that(result.to_run).contains("ruff")
    assert_that(result.to_run).does_not_contain("clippy")


def test_nonempty_tool_lintro_table_is_not_scoped(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A non-empty ``[tool.lintro]`` table keeps the unscoped toolset.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Temporary project directory.
    """
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "sample"\nversion = "0.0.0"\n\n'
        "[tool.lintro.execution]\nfail_fast = false\n",
        encoding="utf-8",
    )
    (tmp_path / "main.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    clear_config_cache()

    result = get_tools_to_run(tools=None, action="check")

    assert_that(result.scoped_by_detection).is_false()
    assert_that(result.to_run).contains("ruff", "clippy")
