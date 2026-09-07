"""Tests for the pylint plugin's ``include`` path scoping (issue #2293)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from assertpy import assert_that

from lintro.tools.pylint.definition import (
    PYLINT_ANALYSED_METADATA_KEY,
    PYLINT_NO_INCLUDED_FILES,
    PylintPlugin,
    filter_included_files,
)
from tests.unit.tools.pylint.conftest import make_result

_VERSION_PATCH = "lintro.plugins.execution_preparation.verify_tool_version"
_RUN = "_run_subprocess_result"


def test_filter_keeps_only_files_under_a_prefix() -> None:
    """Files outside every include prefix are dropped."""
    files = [
        "lintro/tools/definitions/ruff.py",
        "lintro/utils/config.py",
        "lintro/tools/definitions_helper.py",
    ]

    kept = filter_included_files(
        files=files,
        prefixes=("lintro/tools/definitions",),
    )

    assert_that(kept).is_equal_to(["lintro/tools/definitions/ruff.py"])


def test_filter_normalizes_leading_dot_slash() -> None:
    """Discovery's ``./`` prefixes still match a plain configured prefix."""
    kept = filter_included_files(
        files=["./lintro/tools/definitions/ruff.py", "./lintro/utils/config.py"],
        prefixes=("lintro/tools/definitions",),
    )

    assert_that(kept).is_equal_to(["./lintro/tools/definitions/ruff.py"])


def test_filter_without_prefixes_keeps_everything() -> None:
    """An unset ``include`` leaves the discovered set untouched."""
    files = ["a.py", "b/c.py"]

    assert_that(filter_included_files(files=files, prefixes=())).is_equal_to(files)


def test_include_scopes_the_command(
    pylint_plugin: PylintPlugin,
    configured_project: Path,
    clean_report: str,
) -> None:
    """Only files under ``include`` are handed to pylint.

    Args:
        pylint_plugin: Plugin under test.
        configured_project: Project root carrying pylint configuration.
        clean_report: json2 output with no messages.
    """
    (configured_project / "outside.py").write_text("VALUE = 2\n", encoding="utf-8")
    pylint_plugin.set_options(include=["pkg"])

    with (
        patch(_VERSION_PATCH, return_value=None),
        patch.object(
            pylint_plugin,
            _RUN,
            return_value=make_result(returncode=0, stdout=clean_report),
        ) as run,
    ):
        result = pylint_plugin.check([str(configured_project)], {})

    assert_that(result.success).is_true()
    assert_that(result.metadata).contains_entry(
        {PYLINT_ANALYSED_METADATA_KEY: True},
    )
    analysed = [arg for arg in run.call_args.kwargs["cmd"] if arg.endswith(".py")]
    assert_that(analysed).is_not_empty()
    for path in analysed:
        assert_that(path).contains("pkg/")


def test_include_matching_nothing_is_a_clean_pass(
    pylint_plugin: PylintPlugin,
    configured_project: Path,
) -> None:
    """A run with no files under ``include`` passes without invoking pylint.

    The result is a pass rather than a skip: a scoped tool that legitimately
    has nothing to analyse must not trip the no-silent-skip gate.

    Args:
        pylint_plugin: Plugin under test.
        configured_project: Project root carrying pylint configuration.
    """
    pylint_plugin.set_options(include=["does/not/exist"])

    with (
        patch(_VERSION_PATCH, return_value=None),
        patch.object(pylint_plugin, _RUN) as run,
    ):
        result = pylint_plugin.check([str(configured_project)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.output).is_equal_to(PYLINT_NO_INCLUDED_FILES)
    assert_that(run.called).is_false()
    # No marker: the duplicate-code gate must not read this as "no clones".
    assert_that(result.metadata or {}).does_not_contain_key(
        PYLINT_ANALYSED_METADATA_KEY,
    )


def test_gate_owned_options_are_accepted(pylint_plugin: PylintPlugin) -> None:
    """``duplicate_code_baseline`` reaches the plugin without breaking it.

    The key lives in ``[tool.lintro.pylint]`` alongside ``include``, so the
    config manager passes it to ``set_options``; it configures the gate, not
    the pylint command.

    Args:
        pylint_plugin: Plugin under test.
    """
    pylint_plugin.set_options(duplicate_code_baseline=34)

    assert_that(pylint_plugin.options["duplicate_code_baseline"]).is_equal_to(34)
