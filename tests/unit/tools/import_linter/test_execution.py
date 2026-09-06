"""Execution tests for the import-linter plugin with mocked subprocess calls."""

from __future__ import annotations

import subprocess  # nosec B404 - only TimeoutExpired is used, no process is spawned
from pathlib import Path
from typing import cast
from unittest.mock import patch

from assertpy import assert_that

from lintro.tools.definitions.import_linter import (
    ImportLinterPlugin,
    find_import_linter_config,
)

_VERSION_PATCH = "lintro.plugins.execution_preparation.verify_tool_version"


def test_check_reports_broken_chain(
    import_linter_plugin: ImportLinterPlugin,
    project_with_contracts: Path,
    broken_output: str,
) -> None:
    """A broken contract produces a failing result with the chain attached.

    Args:
        import_linter_plugin: Plugin under test.
        project_with_contracts: Project root carrying import contracts.
        broken_output: Output with one broken contract.
    """
    with (
        patch(_VERSION_PATCH, return_value=None),
        patch.object(
            import_linter_plugin,
            "_run_subprocess",
            return_value=(False, broken_output),
        ),
    ):
        result = import_linter_plugin.check([str(project_with_contracts)], {})

    assert_that(result.name).is_equal_to("import-linter")
    assert_that(result.success).is_false()
    assert_that(result.issues_count).is_equal_to(1)
    assert result.issues is not None  # narrow type for mypy
    assert_that(result.issues[0].message).contains("layered.compat -> layered.api")


def test_check_clean_project_succeeds(
    import_linter_plugin: ImportLinterPlugin,
    project_with_contracts: Path,
    kept_output: str,
) -> None:
    """A kept contract set produces a passing result with no issues.

    Args:
        import_linter_plugin: Plugin under test.
        project_with_contracts: Project root carrying import contracts.
        kept_output: Output where the only contract is kept.
    """
    with (
        patch(_VERSION_PATCH, return_value=None),
        patch.object(
            import_linter_plugin,
            "_run_subprocess",
            return_value=(True, kept_output),
        ) as run,
    ):
        result = import_linter_plugin.check([str(project_with_contracts)], {})

    # A discovery miss would take the no-config path and produce the same
    # success/zero-issue shape, so assert the tool was actually invoked.
    assert_that(run.called).is_true()
    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.issues).is_none()


def test_check_runs_once_regardless_of_file_count(
    import_linter_plugin: ImportLinterPlugin,
    project_with_contracts: Path,
    kept_output: str,
) -> None:
    """The tool is invoked a single time even with many discovered files.

    Args:
        import_linter_plugin: Plugin under test.
        project_with_contracts: Project root carrying import contracts.
        kept_output: Output where the only contract is kept.
    """
    package = project_with_contracts / "pkg"
    for name in ("api.py", "services.py", "storage.py"):
        (package / name).write_text("", encoding="utf-8")
    # Pass the files themselves, not just the root: a per-file implementation
    # would invoke the tool three times here and this assertion would catch it.
    paths = [str(package / name) for name in ("api.py", "services.py", "storage.py")]

    calls: list[dict[str, object]] = []

    def fake_run(**kwargs: object) -> tuple[bool, str]:
        """Record one lint-imports invocation and report a kept contract.

        Args:
            **kwargs: Arguments the plugin passed to ``_run_subprocess``.

        Returns:
            A successful run carrying the kept-contract output.
        """
        calls.append(dict(kwargs))
        return (True, kept_output)

    with (
        patch(_VERSION_PATCH, return_value=None),
        patch.object(import_linter_plugin, "_run_subprocess", side_effect=fake_run),
    ):
        result = import_linter_plugin.check(paths, {})

    assert_that(calls).is_length(1)
    assert_that(calls[0]["cwd"]).is_equal_to(str(project_with_contracts))
    # check() must go through _build_command: the flags are part of the contract
    # (no banner in parsed output, no cache directory written into the project).
    recorded_cmd = calls[0]["cmd"]
    assert_that(recorded_cmd).is_instance_of(list)
    cmd = [str(part) for part in cast("list[str]", recorded_cmd)]
    assert_that(cmd[0]).is_equal_to("lint-imports")
    assert_that(cmd).contains("--no-logo", "--no-cache")
    assert_that(cmd).contains(str(project_with_contracts / "pyproject.toml"))
    # The discovered files are never appended to the command line.
    for path in paths:
        assert_that(cmd).does_not_contain(path)
    assert_that(result.success).is_true()


def test_check_without_configuration_is_clean(
    import_linter_plugin: ImportLinterPlugin,
    project_without_contracts: Path,
) -> None:
    """A project declaring no contracts reports a clean result, not an error.

    Args:
        import_linter_plugin: Plugin under test.
        project_without_contracts: Project root with no import-linter config.
    """
    with (
        patch(_VERSION_PATCH, return_value=None),
        patch.object(import_linter_plugin, "_run_subprocess") as run,
    ):
        result = import_linter_plugin.check([str(project_without_contracts)], {})

    assert_that(run.called).is_false()
    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    assert_that(result.output).contains("No import-linter configuration found")


def test_check_no_python_files_skips(
    import_linter_plugin: ImportLinterPlugin,
    project_with_contracts: Path,
) -> None:
    """A path with no Python files short-circuits before running the tool.

    The path deliberately sits inside a configured project: with configuration
    present, the no-config branch cannot produce this result, so the assertion
    can only be satisfied by the file-discovery short-circuit it names.

    Args:
        import_linter_plugin: Plugin under test.
        project_with_contracts: Project root carrying import-linter config.
    """
    readme = project_with_contracts / "README.md"
    readme.write_text("# nothing to check\n", encoding="utf-8")

    with (
        patch(_VERSION_PATCH, return_value=None),
        patch.object(import_linter_plugin, "_run_subprocess") as run,
    ):
        result = import_linter_plugin.check([str(readme)], {})

    assert_that(run.called).is_false()
    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
    # Not the no-config path: the project this file lives in *is* configured.
    assert_that(find_import_linter_config([str(readme)])).is_equal_to(
        project_with_contracts / "pyproject.toml",
    )


def test_check_timeout_is_reported(
    import_linter_plugin: ImportLinterPlugin,
    project_with_contracts: Path,
) -> None:
    """A timed-out graph build fails the run and sets the timeout flag.

    Args:
        import_linter_plugin: Plugin under test.
        project_with_contracts: Project root carrying import contracts.
    """
    with (
        patch(_VERSION_PATCH, return_value=None),
        patch.object(
            import_linter_plugin,
            "_run_subprocess",
            side_effect=subprocess.TimeoutExpired(cmd="lint-imports", timeout=60),
        ),
    ):
        result = import_linter_plugin.check([str(project_with_contracts)], {})

    assert_that(result.success).is_false()
    assert_that(result.timed_out).is_true()
    assert_that(result.issues_count).is_equal_to(0)


def test_find_config_prefers_setup_cfg(tmp_path: Path) -> None:
    """``setup.cfg`` wins over ``pyproject.toml`` in the same directory.

    Args:
        tmp_path: Pytest temporary directory.
    """
    (tmp_path / "setup.cfg").write_text(
        "[importlinter]\nroot_package = pkg\n",
        encoding="utf-8",
    )
    (tmp_path / "pyproject.toml").write_text(
        '[tool.importlinter]\nroot_package = "pkg"\n',
        encoding="utf-8",
    )

    found = find_import_linter_config([str(tmp_path)])

    assert_that(found).is_equal_to(tmp_path / "setup.cfg")


def test_find_config_order_matches_upstream(tmp_path: Path) -> None:
    """Same-directory precedence follows import-linter's own reader order.

    Upstream registers an ini reader (``setup.cfg`` then ``.importlinter``)
    ahead of the toml reader (``pyproject.toml``) — see
    ``importlinter.configuration`` and ``adapters.user_options``. Lintro must
    pick the same file a bare ``lint-imports`` run would, or the two disagree
    about which contracts apply.

    Args:
        tmp_path: Pytest temporary directory.
    """
    (tmp_path / "pyproject.toml").write_text(
        '[tool.importlinter]\nroot_package = "pkg"\n',
        encoding="utf-8",
    )
    (tmp_path / ".importlinter").write_text(
        "[importlinter]\nroot_package = pkg\n",
        encoding="utf-8",
    )

    assert_that(find_import_linter_config([str(tmp_path)])).is_equal_to(
        tmp_path / ".importlinter",
    )

    (tmp_path / "setup.cfg").write_text(
        "[importlinter]\nroot_package = pkg\n",
        encoding="utf-8",
    )

    assert_that(find_import_linter_config([str(tmp_path)])).is_equal_to(
        tmp_path / "setup.cfg",
    )


def test_find_config_walks_upward_from_a_file(tmp_path: Path) -> None:
    """Config discovery walks up from a file path to the project root.

    Args:
        tmp_path: Pytest temporary directory.
    """
    (tmp_path / ".importlinter").write_text(
        "[importlinter]\nroot_package = pkg\n",
        encoding="utf-8",
    )
    nested = tmp_path / "pkg" / "sub"
    nested.mkdir(parents=True)
    module = nested / "module.py"
    module.write_text("", encoding="utf-8")

    found = find_import_linter_config([str(module)])

    assert_that(found).is_equal_to(tmp_path / ".importlinter")


def test_find_config_ignores_files_without_the_section(tmp_path: Path) -> None:
    """A ``setup.cfg`` with no ``[importlinter]`` section is not a config.

    Args:
        tmp_path: Pytest temporary directory.
    """
    (tmp_path / "setup.cfg").write_text("[metadata]\nname = pkg\n", encoding="utf-8")
    (tmp_path / ".importlinter").write_text(
        "[importlinter]\nroot_package = pkg\n",
        encoding="utf-8",
    )

    found = find_import_linter_config([str(tmp_path)])

    assert_that(found).is_equal_to(tmp_path / ".importlinter")


def test_find_config_accepts_a_non_header_toml_table(tmp_path: Path) -> None:
    """A ``tool.importlinter`` inline table is configuration too.

    TOML can spell the same table several ways; ``lint-imports`` accepts an
    inline ``importlinter = {...}`` under ``[tool]``, so discovery must not
    depend on a literal ``[tool.importlinter]`` header line.

    Args:
        tmp_path: Pytest temporary directory.
    """
    (tmp_path / "pyproject.toml").write_text(
        '[tool]\nimportlinter = { root_package = "pkg" }\n',
        encoding="utf-8",
    )

    found = find_import_linter_config([str(tmp_path)])

    assert_that(found).is_equal_to(tmp_path / "pyproject.toml")


def test_find_config_ignores_an_ordinary_pyproject(tmp_path: Path) -> None:
    """A ``pyproject.toml`` with other tools is not import-linter configuration.

    This is what makes "no configuration found is clean" safe to document: a
    typical Python project is not mistaken for a configured one.

    Args:
        tmp_path: Pytest temporary directory.
    """
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "pkg"\n\n[tool.ruff]\nline-length = 88\n',
        encoding="utf-8",
    )

    assert_that(find_import_linter_config([str(tmp_path)])).is_none()


def test_find_config_ignores_unparseable_toml(tmp_path: Path) -> None:
    """A malformed ``pyproject.toml`` is skipped rather than raising.

    Args:
        tmp_path: Pytest temporary directory.
    """
    (tmp_path / "pyproject.toml").write_text("[tool.importlinter\n", encoding="utf-8")

    assert_that(find_import_linter_config([str(tmp_path)])).is_none()


def test_find_config_accepts_an_ini_contract_section(tmp_path: Path) -> None:
    """``[importlinter:contract:...]`` marks a file as configuration.

    Args:
        tmp_path: Pytest temporary directory.
    """
    (tmp_path / ".importlinter").write_text(
        "[importlinter:contract:layers]\ntype = layers\n",
        encoding="utf-8",
    )

    found = find_import_linter_config([str(tmp_path)])

    assert_that(found).is_equal_to(tmp_path / ".importlinter")


def test_find_config_ignores_non_utf8_pyproject(tmp_path: Path) -> None:
    """A ``pyproject.toml`` that is not valid UTF-8 is skipped, not fatal.

    ``tomllib.load`` decodes the bytes it reads, so an invalid byte raises
    ``UnicodeDecodeError`` rather than ``TOMLDecodeError``; letting that escape
    would abort the whole check run instead of skipping one candidate.

    Args:
        tmp_path: Pytest temporary directory.
    """
    (tmp_path / "pyproject.toml").write_bytes(b'[tool.importlinter]\nroot = "\xff"\n')

    assert_that(find_import_linter_config([str(tmp_path)])).is_none()


def test_find_config_ignores_a_non_contract_importlinter_prefix(tmp_path: Path) -> None:
    """Only ``[importlinter]`` and ``[importlinter:contract:<id>]`` count.

    Upstream reads session options from ``[importlinter]`` and one section per
    contract named ``[importlinter:contract:<id>]``. A lookalike such as
    ``[importlinter:other]`` is not configuration, and treating it as one would
    hand ``lint-imports`` a file it cannot use.

    Args:
        tmp_path: Pytest temporary directory.
    """
    (tmp_path / "setup.cfg").write_text(
        "[importlinter:other]\nvalue = 1\n",
        encoding="utf-8",
    )

    assert_that(find_import_linter_config([str(tmp_path)])).is_none()
