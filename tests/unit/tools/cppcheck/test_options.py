"""Unit tests for cppcheck plugin options and definition."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that

from lintro.enums.tool_type import ToolType
from lintro.tools.cppcheck.definition import (
    CPPCHECK_FILE_PATTERNS,
    CppcheckPlugin,
)
from lintro.utils.project_detection import detect_project_languages
from lintro.utils.tool_options import parse_tool_options


def test_definition_metadata(cppcheck_plugin: CppcheckPlugin) -> None:
    """The definition exposes the expected metadata.

    Args:
        cppcheck_plugin: The plugin under test.
    """
    definition = cppcheck_plugin.definition
    assert_that(definition.name).is_equal_to("cppcheck")
    assert_that(definition.can_fix).is_false()
    assert_that(definition.tool_type).is_equal_to(
        ToolType.LINTER | ToolType.SECURITY,
    )
    assert_that(definition.file_patterns).contains("*.c", "*.cpp", "*.cc")
    # Headers are deliberately absent: cppcheck analyses a header handed to it
    # directly as a standalone translation unit and misfires without the source
    # that includes it, so headers are covered through those sources instead.
    assert_that(definition.file_patterns).does_not_contain("*.h", "*.hpp")
    assert_that(definition.version_command).is_equal_to(["cppcheck", "--version"])


def test_set_options_updates_enable(cppcheck_plugin: CppcheckPlugin) -> None:
    """Setting the enable option updates the stored value.

    Args:
        cppcheck_plugin: The plugin under test.
    """
    cppcheck_plugin.set_options(enable="warning")
    assert_that(cppcheck_plugin.options.get("enable")).is_equal_to("warning")


def test_set_options_inconclusive_and_std(cppcheck_plugin: CppcheckPlugin) -> None:
    """Boolean and string options are stored correctly.

    Args:
        cppcheck_plugin: The plugin under test.
    """
    cppcheck_plugin.set_options(inconclusive=True, std="c11")
    assert_that(cppcheck_plugin.options.get("inconclusive")).is_true()
    assert_that(cppcheck_plugin.options.get("std")).is_equal_to("c11")


def test_set_options_suppress_list(cppcheck_plugin: CppcheckPlugin) -> None:
    """Suppress accepts a list and reaches the built command.

    Args:
        cppcheck_plugin: The plugin under test.
    """
    cppcheck_plugin.set_options(suppress=["missingInclude", "unusedFunction"])
    cmd = cppcheck_plugin._build_command(files=["a.c"])
    assert_that(cmd).contains("--suppress=missingInclude")
    assert_that(cmd).contains("--suppress=unusedFunction")


def test_set_options_inline_suppr_flag(cppcheck_plugin: CppcheckPlugin) -> None:
    """The inline-suppr flag is added when enabled.

    Args:
        cppcheck_plugin: The plugin under test.
    """
    cppcheck_plugin.set_options(inline_suppr=True)
    assert_that(cppcheck_plugin._build_command(files=["a.c"])).contains(
        "--inline-suppr",
    )


def test_set_options_rejects_bad_type(cppcheck_plugin: CppcheckPlugin) -> None:
    """A non-boolean inconclusive value raises ValueError.

    Args:
        cppcheck_plugin: The plugin under test.
    """
    with pytest.raises(ValueError):
        cppcheck_plugin.set_options(inconclusive="yes")  # type: ignore[arg-type]


def test_doc_url_returns_manual(cppcheck_plugin: CppcheckPlugin) -> None:
    """doc_url returns the manual URL for a code and None for empty input.

    Args:
        cppcheck_plugin: The plugin under test.
    """
    assert_that(cppcheck_plugin.doc_url("uninitvar")).contains("cppcheck")
    assert_that(cppcheck_plugin.doc_url("")).is_none()


def test_set_options_accepts_pipe_delimited_enable_list(
    cppcheck_plugin: CppcheckPlugin,
) -> None:
    """A list of categories is joined into cppcheck's comma-separated form.

    ``--tool-options`` splits on commas, so several categories can only reach
    the plugin as the pipe-delimited list the CLI coerces (``enable=a|b``).

    Args:
        cppcheck_plugin: The plugin under test.
    """
    cppcheck_plugin.set_options(enable=["warning", "style"])

    assert_that(cppcheck_plugin.options.get("enable")).is_equal_to("warning,style")
    assert_that(cppcheck_plugin._build_command(files=["a.c"])).contains(
        "--enable=warning,style",
    )


def test_set_options_accepts_a_single_suppress_string(
    cppcheck_plugin: CppcheckPlugin,
) -> None:
    """A bare suppression string is normalized to a one-element list.

    Args:
        cppcheck_plugin: The plugin under test.
    """
    cppcheck_plugin.set_options(suppress="missingInclude")

    assert_that(cppcheck_plugin._build_command(files=["a.c"])).contains(
        "--suppress=missingInclude",
    )


def test_documented_tool_options_examples_parse(
    cppcheck_plugin: CppcheckPlugin,
) -> None:
    """The ``--tool-options`` strings in the docs reach cppcheck's argv.

    Args:
        cppcheck_plugin: The plugin under test.
    """
    parsed = parse_tool_options(
        "cppcheck:enable=warning|style,cppcheck:std=c11,"
        "cppcheck:inconclusive=true,cppcheck:suppress=missingInclude",
    )
    options: dict[str, Any] = dict(parsed["cppcheck"])
    cppcheck_plugin.set_options(**options)
    cmd = cppcheck_plugin._build_command(files=["a.c"])

    assert_that(cmd).contains(
        "--enable=warning,style",
        "--std=c11",
        "--inconclusive",
        "--suppress=missingInclude",
    )


def test_language_detection_suffixes_match_the_file_patterns(tmp_path: Path) -> None:
    """C/C++ detection and cppcheck's globs stay in lockstep.

    ``detect_project_languages`` re-lists the suffixes that
    ``CPPCHECK_FILE_PATTERNS`` declares. If the two drift, either a header-only
    tree selects a tool that can match nothing, or a real source tree stops
    selecting cppcheck at all.

    Args:
        tmp_path: Temporary project directory.
    """
    for pattern in CPPCHECK_FILE_PATTERNS:
        suffix = pattern.removeprefix("*")
        (tmp_path / f"probe{suffix}").write_text("int main(void){return 0;}\n")
        languages = detect_project_languages(root=tmp_path)
        detected = [lang for lang in languages if lang in {"c", "cpp"}]
        assert_that(detected).described_as(pattern).is_not_empty()
        (tmp_path / f"probe{suffix}").unlink()


def test_header_only_tree_is_not_detected_as_c_or_cpp(tmp_path: Path) -> None:
    """Headers alone do not select cppcheck, which cannot analyse them alone.

    Args:
        tmp_path: Temporary project directory.
    """
    (tmp_path / "api.h").write_text("int f(void);\n")
    (tmp_path / "api.hpp").write_text("int g();\n")

    languages = detect_project_languages(root=tmp_path)

    assert_that(languages).does_not_contain("c", "cpp")
