"""A path starting with ``-`` is linted, never read as an option (#2400).

Runs the real yamllint. With a config in play lintro passes yamllint a path
relative to the directory it runs in, so ``-config.yaml`` or ``-dir/x.yaml``
reaches the command line bare; without an end-of-options marker yamllint's
argument parser takes it for a flag and rejects the run.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.models.core.tool_result import ToolResult
from lintro.tools.yamllint.definition import YamllintPlugin

pytestmark = pytest.mark.skipif(
    shutil.which("yamllint") is None,
    reason="yamllint is not installed",
)

_CONFIG = "extends: default\nrules:\n  document-start: disable\n"

# One trailing-spaces error on line 1.
_DOCUMENT = "key: value   \n"


def _assert_trailing_spaces_reported(result: ToolResult, document: Path) -> None:
    """Assert the run completed and reported the one finding at ``document``.

    Args:
        result: Result of the yamllint check.
        document: The YAML file the finding must name, as an absolute path.
    """
    assert_that(result.output or "").does_not_contain("usage: yamllint")
    found = [
        (issue.file, issue.line, getattr(issue, "rule", None))
        for issue in result.issues or []
    ]
    assert_that(found).is_equal_to([(str(document), 1, "trailing-spaces")])


def test_dash_file_next_to_discovered_config_is_linted(
    yamllint_plugin: YamllintPlugin,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``-config.yaml`` beside a discovered ``.yamllint`` is checked.

    Args:
        yamllint_plugin: The YamllintPlugin instance to test.
        tmp_path: Directory holding the config and the YAML file.
        monkeypatch: Pytest fixture used to change directory.
    """
    (tmp_path / ".yamllint").write_text(_CONFIG)
    document = tmp_path / "-config.yaml"
    document.write_text(_DOCUMENT)
    monkeypatch.chdir(tmp_path.parent)

    result = yamllint_plugin.check([str(document)], {})

    _assert_trailing_spaces_reported(result, document)


def test_dash_file_with_explicit_config_is_linted(
    yamllint_plugin: YamllintPlugin,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``-config.yaml`` in lintro's directory is checked with ``config_file``.

    Args:
        yamllint_plugin: The YamllintPlugin instance to test.
        tmp_path: Project root lintro runs from.
        monkeypatch: Pytest fixture used to change directory.
    """
    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "yl.yaml").write_text(_CONFIG)
    document = tmp_path / "-config.yaml"
    document.write_text(_DOCUMENT)
    monkeypatch.chdir(tmp_path)
    yamllint_plugin.set_options(config_file="configs/yl.yaml")

    result = yamllint_plugin.check([str(document)], {})

    _assert_trailing_spaces_reported(result, document)


def test_file_in_dash_directory_is_linted(
    yamllint_plugin: YamllintPlugin,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A file under ``-dir/`` below a discovered config is checked.

    Args:
        yamllint_plugin: The YamllintPlugin instance to test.
        tmp_path: Directory holding the config and ``-dir``.
        monkeypatch: Pytest fixture used to change directory.
    """
    (tmp_path / ".yamllint").write_text(_CONFIG)
    dash_dir = tmp_path / "-dir"
    dash_dir.mkdir()
    document = dash_dir / "x.yaml"
    document.write_text(_DOCUMENT)
    monkeypatch.chdir(tmp_path)

    result = yamllint_plugin.check([str(dash_dir)], {})

    _assert_trailing_spaces_reported(result, document)
