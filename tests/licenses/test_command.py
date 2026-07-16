"""Tests for the ``lintro licenses`` CLI command."""

from __future__ import annotations

import json

from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli_utils.commands.licenses import licenses_command


def test_licenses_json_output_is_parseable() -> None:
    """The command emits parseable JSON for installed packages."""
    result = CliRunner().invoke(
        licenses_command,
        ["--lang", "python", "--format", "json"],
    )
    assert_that(result.exit_code).is_equal_to(0)
    payload = json.loads(result.output)
    assert_that(payload).is_not_empty()


def test_licenses_grid_output_runs() -> None:
    """The default grid output renders without error."""
    result = CliRunner().invoke(licenses_command, ["--lang", "python"])
    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains("Total packages:")


def test_licenses_check_flag_exits_nonzero_on_violation() -> None:
    """The --check flag exits non-zero when a strict policy denies packages."""
    # Strict policy denies every unlisted license, so installed packages
    # will produce violations and force a non-zero exit.
    runner = CliRunner()
    with runner.isolated_filesystem() as tmp:
        from pathlib import Path

        Path(tmp, ".lintro-config.yaml").write_text(
            "licenses:\n  policy: strict\n",
        )
        result = runner.invoke(
            licenses_command,
            ["--lang", "python", "--check", "--format", "json"],
        )
    assert_that(result.exit_code).is_equal_to(1)


def test_licenses_attribution_outputs_markdown() -> None:
    """The --attribution flag emits a Markdown attribution document."""
    result = CliRunner().invoke(
        licenses_command,
        ["--lang", "python", "--attribution"],
    )
    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains("# Third-Party Licenses")


def test_licenses_spdx_format() -> None:
    """The spdx format emits an SPDX tag-value document."""
    result = CliRunner().invoke(
        licenses_command,
        ["--lang", "python", "--format", "spdx"],
    )
    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains("SPDXVersion: SPDX-2.3")
