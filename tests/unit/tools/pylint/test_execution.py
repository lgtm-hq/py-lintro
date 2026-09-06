"""Execution tests for the pylint plugin with mocked subprocess calls."""

from __future__ import annotations

import json
import subprocess  # nosec B404 - only TimeoutExpired is used, no process is spawned
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.tools.definitions.pylint import PylintPlugin, find_pylint_config
from tests.unit.tools.pylint.conftest import make_result

_VERSION_PATCH = "lintro.plugins.execution_preparation.verify_tool_version"
_RUN = "_run_subprocess_result"


def test_check_reports_duplicate_code(
    pylint_plugin: PylintPlugin,
    configured_project: Path,
    duplicate_report: str,
) -> None:
    """An R0801 report becomes a failing result carrying the clone set.

    Args:
        pylint_plugin: Plugin under test.
        configured_project: Project root carrying pylint configuration.
        duplicate_report: json2 output with one R0801 message.
    """
    with (
        patch(_VERSION_PATCH, return_value=None),
        # Exit 8 is pylint's "refactor message issued" bit, not a crash.
        patch.object(
            pylint_plugin,
            _RUN,
            return_value=make_result(returncode=8, stdout=duplicate_report),
        ),
    ):
        result = pylint_plugin.check([str(configured_project)], {})

    assert_that(result.name).is_equal_to("pylint")
    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)
    assert result.issues is not None  # narrow type for mypy
    issue = result.issues[0]
    assert_that(issue.get_code()).is_equal_to("R0801")
    assert_that(issue.message).contains("Similar lines in 2 files", "==first:[12:27]")


def test_check_clean_report_succeeds(
    pylint_plugin: PylintPlugin,
    configured_project: Path,
    clean_report: str,
) -> None:
    """An empty message list produces a passing result with no issues.

    Args:
        pylint_plugin: Plugin under test.
        configured_project: Project root carrying pylint configuration.
        clean_report: json2 output with no messages.
    """
    with (
        patch(_VERSION_PATCH, return_value=None),
        patch.object(
            pylint_plugin,
            _RUN,
            return_value=make_result(returncode=0, stdout=clean_report),
        ) as run,
    ):
        result = pylint_plugin.check([str(configured_project)], {})

    # A skip would produce the same success/zero-issue shape, so assert the
    # tool was actually invoked.
    assert_that(run.called).is_true()
    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.issues).is_none()


def test_check_runs_once_over_every_discovered_file(
    pylint_plugin: PylintPlugin,
    configured_project: Path,
    clean_report: str,
) -> None:
    """All files go to a single invocation, with the rcfile pinned.

    This is the load-bearing behaviour of the plugin: ``duplicate-code`` only
    sees clones that appear inside one run, so a per-file implementation would
    report zero R0801 findings on every codebase.

    Args:
        pylint_plugin: Plugin under test.
        configured_project: Project root carrying pylint configuration.
        clean_report: json2 output with no messages.
    """
    package = configured_project / "pkg"
    for name in ("a.py", "b.py", "c.py"):
        (package / name).write_text("VALUE = 1\n", encoding="utf-8")

    commands: list[list[str]] = []

    def fake_run(**kwargs: object) -> object:
        """Record one pylint invocation and report a clean run.

        Args:
            **kwargs: Arguments the plugin passed to the subprocess helper.

        Returns:
            A successful pylint result carrying the clean json2 report.
        """
        commands.append(list(cast("list[str]", kwargs["cmd"])))
        return make_result(returncode=0, stdout=clean_report)

    with (
        patch(_VERSION_PATCH, return_value=None),
        patch.object(pylint_plugin, _RUN, side_effect=fake_run),
    ):
        result = pylint_plugin.check([str(configured_project)], {})

    assert_that(commands).is_length(1)
    assert_that(commands[0]).contains("--output-format=json2")
    assert_that(commands[0]).contains_sequence(
        "--rcfile",
        str(configured_project / "pyproject.toml"),
    )
    for name in ("a.py", "b.py", "c.py", "module.py", "__init__.py"):
        assert_that(commands[0]).contains(f"pkg/{name}")
    assert_that(result.success).is_true()


def test_check_without_configuration_still_runs(
    pylint_plugin: PylintPlugin,
    unconfigured_project: Path,
    clean_report: str,
) -> None:
    """No pylint config means pylint's own defaults, not a skip.

    This is the deliberate difference from import-linter: pylint runs happily
    with no configuration file, so the plugin must not invent a skip path.

    Args:
        pylint_plugin: Plugin under test.
        unconfigured_project: Project root with no pylint configuration.
        clean_report: json2 output with no messages.
    """
    with (
        patch(_VERSION_PATCH, return_value=None),
        patch.object(
            pylint_plugin,
            _RUN,
            return_value=make_result(returncode=0, stdout=clean_report),
        ) as run,
    ):
        result = pylint_plugin.check([str(unconfigured_project)], {})

    assert_that(run.called).is_true()
    assert_that(run.call_args.kwargs["cmd"]).does_not_contain("--rcfile")
    assert_that(result.success).is_true()
    assert_that(result.skipped).is_false()


def test_check_usage_error_is_a_failure_not_a_pass(
    pylint_plugin: PylintPlugin,
    configured_project: Path,
) -> None:
    """A non-zero exit with no report is an execution failure.

    pylint exits 32 for usage errors (a missing rcfile, an unknown message id)
    and prints them to stderr with no JSON at all. Reporting that as clean
    would hide a permanently broken configuration behind a green run.

    Args:
        pylint_plugin: Plugin under test.
        configured_project: Project root carrying pylint configuration.
    """
    with (
        patch(_VERSION_PATCH, return_value=None),
        patch.object(
            pylint_plugin,
            _RUN,
            return_value=make_result(
                returncode=32,
                stdout="",
                stderr="The config file nope.toml doesn't exist!",
            ),
        ),
    ):
        result = pylint_plugin.check([str(configured_project)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.output).contains("doesn't exist")


@pytest.mark.parametrize(
    ("returncode", "stdout", "stderr"),
    [
        (0, "", ""),
        (32, "No files to lint: exiting.", ""),
        (32, "", "No files to lint: exiting."),
    ],
    ids=[
        "empty-clean-exit",
        "nothing-left-to-lint-stdout",
        "nothing-left-to-lint-stderr",
    ],
)
def test_check_non_report_output_is_clean(
    pylint_plugin: PylintPlugin,
    configured_project: Path,
    returncode: int,
    stdout: str,
    stderr: str,
) -> None:
    """Informational, non-JSON output is a clean pass, not a parse failure.

    pylint prints "No files to lint: exiting." — and exits 32, its usage-error
    status — when nothing is left to analyse after its own ignore filters. The
    sentence therefore has to be recognised *before* the exit code, or a
    legitimate run is reported as broken. Which stream carries the message is
    a pylint implementation detail, so both are pinned here.

    Args:
        pylint_plugin: Plugin under test.
        configured_project: Project root carrying pylint configuration.
        returncode: Exit status accompanying the output.
        stdout: Non-report stdout pylint can emit.
        stderr: Non-report stderr pylint can emit.
    """
    with (
        patch(_VERSION_PATCH, return_value=None),
        patch.object(
            pylint_plugin,
            _RUN,
            return_value=make_result(
                returncode=returncode,
                stdout=stdout,
                stderr=stderr,
            ),
        ),
    ):
        result = pylint_plugin.check([str(configured_project)], {})

    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.output).is_none()


def test_check_report_quoting_the_nothing_to_lint_phrase_is_parsed(
    pylint_plugin: PylintPlugin,
    configured_project: Path,
) -> None:
    """A report is parsed even when a message body quotes pylint's usage text.

    ``R0801`` bodies quote the duplicated source verbatim, so a clone set
    covering code that mentions "No files to lint" would otherwise match the
    nothing-to-lint sentinel and be reported as a clean pass — this plugin's
    own module is such a file.

    Args:
        pylint_plugin: Plugin under test.
        configured_project: Project root carrying pylint configuration.
    """
    report = json.dumps(
        {
            "messages": [
                {
                    "type": "refactor",
                    "symbol": "duplicate-code",
                    "message": (
                        "Similar lines in 2 files\n"
                        '    PYLINT_NOTHING_TO_LINT = "No files to lint"'
                    ),
                    "messageId": "R0801",
                    "line": 1,
                    "column": 0,
                    "path": "second.py",
                },
            ],
            "statistics": {"score": 9.5},
        },
    )

    with (
        patch(_VERSION_PATCH, return_value=None),
        patch.object(
            pylint_plugin,
            _RUN,
            return_value=make_result(returncode=8, stdout=report),
        ),
    ):
        result = pylint_plugin.check([str(configured_project)], {})

    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)


def test_check_unparseable_output_is_a_parse_failure(
    pylint_plugin: PylintPlugin,
    configured_project: Path,
) -> None:
    """Output that is present but not JSON is reported, never swallowed.

    Args:
        pylint_plugin: Plugin under test.
        configured_project: Project root carrying pylint configuration.
    """
    with (
        patch(_VERSION_PATCH, return_value=None),
        patch.object(
            pylint_plugin,
            _RUN,
            # A report that starts but never finishes: truncated JSON, not a
            # message pylint prints deliberately.
            return_value=make_result(
                returncode=1,
                stdout='{"messages": [{"messageId": "R0801"',
            ),
        ),
    ):
        result = pylint_plugin.check([str(configured_project)], {})

    assert_that(result.success).is_false()
    assert_that(result.parse_failures_count).is_equal_to(1)
    assert_that(result.output).contains("R0801")


def test_check_timeout_is_reported(
    pylint_plugin: PylintPlugin,
    configured_project: Path,
) -> None:
    """A timeout produces a failing, timed-out result naming the option.

    Args:
        pylint_plugin: Plugin under test.
        configured_project: Project root carrying pylint configuration.
    """
    with (
        patch(_VERSION_PATCH, return_value=None),
        patch.object(
            pylint_plugin,
            _RUN,
            side_effect=subprocess.TimeoutExpired(cmd="pylint", timeout=1),
        ),
    ):
        result = pylint_plugin.check([str(configured_project)], {})

    assert_that(result.success).is_false()
    assert_that(result.timed_out).is_true()
    assert_that(result.output).contains("pylint:timeout=")


def test_check_no_python_files_skips_early(
    pylint_plugin: PylintPlugin,
    tmp_path: Path,
) -> None:
    """A path with no Python files never spawns the tool.

    Args:
        pylint_plugin: Plugin under test.
        tmp_path: Pytest temporary directory.
    """
    (tmp_path / "README.md").write_text("# nothing to lint\n", encoding="utf-8")

    with (
        patch(_VERSION_PATCH, return_value=None),
        patch.object(pylint_plugin, _RUN) as run,
    ):
        result = pylint_plugin.check([str(tmp_path)], {})

    assert_that(run.called).is_false()
    assert_that(result.issues_count).is_equal_to(0)
    # ToolResult.success defaults to False, so this has to be asserted: a
    # path with nothing to lint is a pass, not a skip and not a failure.
    assert_that(result.success).is_true()
    assert_that(result.skipped).is_false()


def test_find_config_prefers_pylintrc_over_pyproject(tmp_path: Path) -> None:
    """Same-directory precedence follows pylint's own reader order.

    Args:
        tmp_path: Pytest temporary directory.
    """
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pylint.main]\ndisable = ["all"]\n',
        encoding="utf-8",
    )
    (tmp_path / "pylintrc").write_text("[MAIN]\ndisable=all\n", encoding="utf-8")

    assert_that(find_pylint_config([str(tmp_path)])).is_equal_to(
        tmp_path / "pylintrc",
    )


def test_find_config_walks_up_from_a_file(tmp_path: Path) -> None:
    """Discovery starts at a file's directory and walks upward.

    Args:
        tmp_path: Pytest temporary directory.
    """
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pylint.main]\ndisable = ["all"]\n',
        encoding="utf-8",
    )
    nested = tmp_path / "pkg" / "deep"
    nested.mkdir(parents=True)
    module = nested / "module.py"
    module.write_text("VALUE = 1\n", encoding="utf-8")

    assert_that(find_pylint_config([str(module)])).is_equal_to(
        tmp_path / "pyproject.toml",
    )


@pytest.mark.parametrize(
    ("filename", "contents"),
    [
        ("pylintrc.toml", '[tool.pylint.main]\ndisable = ["all"]\n'),
        (".pylintrc", "[MAIN]\ndisable=all\n"),
        (".pylintrc.toml", '[tool.pylint.main]\ndisable = ["all"]\n'),
        ("setup.cfg", "[pylint.main]\ndisable=all\n"),
        ("tox.ini", "[pylint.main]\ndisable=all\n"),
    ],
)
def test_find_config_accepts_pylints_other_filenames(
    tmp_path: Path,
    filename: str,
    contents: str,
) -> None:
    """Discovery covers every filename pylint itself reads.

    Missing one would silently fall through to a parent directory's config, or
    to none, for a project that configures pylint through that file.

    Args:
        tmp_path: Pytest temporary directory.
        filename: Config filename to stage.
        contents: Config body declaring a pylint section.
    """
    project = tmp_path / filename.replace(".", "_")
    project.mkdir()
    (project / filename).write_text(contents, encoding="utf-8")

    assert_that(find_pylint_config([str(project)])).is_equal_to(project / filename)


def test_find_config_ignores_a_toml_rc_without_a_pylint_table(
    tmp_path: Path,
) -> None:
    """A ``pylintrc.toml`` counts only when it declares ``tool.pylint``.

    pylint reads these files for that table alone, so presence is not enough.

    Args:
        tmp_path: Pytest temporary directory.
    """
    project = tmp_path / "toml_rc"
    project.mkdir()
    (project / "pylintrc.toml").write_text("[other]\nkey = 1\n", encoding="utf-8")

    assert_that(find_pylint_config([str(project)])).is_none()


def test_find_config_accepts_a_setup_cfg_section(tmp_path: Path) -> None:
    """A ``setup.cfg`` counts only when it declares a pylint section.

    Args:
        tmp_path: Pytest temporary directory.
    """
    (tmp_path / "setup.cfg").write_text(
        "[pylint.main]\ndisable=all\n",
        encoding="utf-8",
    )

    assert_that(find_pylint_config([str(tmp_path)])).is_equal_to(
        tmp_path / "setup.cfg",
    )


def test_find_config_ignores_a_pyproject_without_a_pylint_table(
    tmp_path: Path,
) -> None:
    """An ordinary ``pyproject.toml`` is not pylint configuration.

    The table is looked up in the parsed document rather than string-matched,
    so a ``pylint`` mention in a dependency list cannot masquerade as config.

    Args:
        tmp_path: Pytest temporary directory.
    """
    project = tmp_path / "plain"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname = "pkg"\ndependencies = ["pylint"]\n\n'
        "[tool.ruff]\nline-length = 88\n",
        encoding="utf-8",
    )

    assert_that(find_pylint_config([str(project)])).is_none()


def test_find_config_ignores_an_unreadable_pyproject(tmp_path: Path) -> None:
    """Malformed TOML is skipped rather than crashing discovery.

    Args:
        tmp_path: Pytest temporary directory.
    """
    project = tmp_path / "broken"
    project.mkdir()
    (project / "pyproject.toml").write_text("[tool.pylint\n", encoding="utf-8")

    assert_that(find_pylint_config([str(project)])).is_none()
