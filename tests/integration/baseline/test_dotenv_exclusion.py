"""Lintro configuration excludes dotenv files from real native checks and fixes."""

from pathlib import Path

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli import cli
from lintro.config.config_loader import clear_config_cache
from lintro.utils.config import clear_pyproject_cache
from tests.integration._tools import require_tool

pytestmark = require_tool("dotenv-linter")


@pytest.mark.parametrize("command", ["check", "format"])
def test_configured_exclusion_leaves_dirty_file_unreported(
    workspace: Path,
    command: str,
) -> None:
    """The CLI applies Lintro options to its execution copy of the plugin."""
    file = workspace / ".env"
    source = "lowercase=value\n"
    file.write_text(source, encoding="utf-8")
    runner = CliRunner()
    dirty = runner.invoke(
        cli,
        ["check", str(file), "--tools", "dotenv_linter", "--yes"],
    )
    assert_that(dirty.exit_code).described_as(dirty.output).is_equal_to(1)
    assert_that(dirty.output).contains("LowercaseKey")
    (workspace / "pyproject.toml").write_text(
        f'[tool.lintro.dotenv_linter]\nexclude = ["{file}"]\n',
        encoding="utf-8",
    )
    clear_config_cache()
    clear_pyproject_cache()

    excluded = runner.invoke(
        cli,
        [command, str(file), "--tools", "dotenv_linter", "--yes"],
    )

    assert_that(excluded.exit_code).described_as(excluded.output).is_zero()
    assert_that(excluded.output).does_not_contain("LowercaseKey")
    assert_that(file.read_text(encoding="utf-8")).is_equal_to(source)
