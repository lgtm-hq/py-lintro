"""Tests for the lintro deps CLI command."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli import cli


@pytest.fixture
def cli_runner() -> CliRunner:
    """Provide a Click CLI runner.

    Returns:
        CliRunner: A test runner.
    """
    return CliRunner()


def _write_pyproject(tmp_path: Path) -> Path:
    """Write a pyproject.toml with mixed specs.

    Args:
        tmp_path: Temporary directory.

    Returns:
        Path: Path to the manifest.
    """
    manifest = tmp_path / "pyproject.toml"
    manifest.write_text(
        "\n".join(
            [
                "[project]",
                'name = "demo"',
                "dependencies = [",
                '  "pydantic==2.0",',
                '  "requests>=2.28.0",',
                "]",
            ],
        ),
    )
    return manifest


def test_deps_grid_reports_violations(
    cli_runner: CliRunner,
    tmp_path: Path,
) -> None:
    """Grid output flags an unbounded dependency and exits non-zero.

    Args:
        cli_runner: The Click runner.
        tmp_path: Temporary directory.
    """
    manifest = _write_pyproject(tmp_path)
    result = cli_runner.invoke(cli, ["deps", "--file", str(manifest)])
    assert_that(result.exit_code).is_equal_to(1)
    assert_that(result.output).contains("No upper bound")


def test_deps_json_output(cli_runner: CliRunner, tmp_path: Path) -> None:
    """JSON output contains structured violation data.

    Args:
        cli_runner: The Click runner.
        tmp_path: Temporary directory.
    """
    manifest = _write_pyproject(tmp_path)
    result = cli_runner.invoke(
        cli,
        ["deps", "--file", str(manifest), "--format", "json"],
    )
    payload = json.loads(result.output)
    assert_that(payload["passed"]).is_false()
    assert_that(payload["violation_count"]).is_equal_to(1)
    assert_that(payload["violations"][0]["package"]).is_equal_to("requests")


def test_deps_loose_policy_passes(
    cli_runner: CliRunner,
    tmp_path: Path,
) -> None:
    """Loose policy passes an unbounded spec (exit 0).

    Args:
        cli_runner: The Click runner.
        tmp_path: Temporary directory.
    """
    manifest = _write_pyproject(tmp_path)
    result = cli_runner.invoke(
        cli,
        ["deps", "--file", str(manifest), "--policy", "loose"],
    )
    assert_that(result.exit_code).is_equal_to(0)


def test_deps_strict_policy_flags_range(
    cli_runner: CliRunner,
    tmp_path: Path,
) -> None:
    """Strict policy flags a non-exact spec.

    Args:
        cli_runner: The Click runner.
        tmp_path: Temporary directory.
    """
    manifest = _write_pyproject(tmp_path)
    result = cli_runner.invoke(
        cli,
        ["deps", "--file", str(manifest), "--policy", "strict"],
    )
    assert_that(result.exit_code).is_equal_to(1)


def test_deps_explicit_missing_file_fails(
    cli_runner: CliRunner,
    tmp_path: Path,
) -> None:
    """Explicit ``--file`` paths that do not exist exit non-zero.

    Args:
        cli_runner: The Click runner.
        tmp_path: Temporary directory.
    """
    missing = tmp_path / "nope.txt"
    result = cli_runner.invoke(cli, ["deps", "--file", str(missing)])
    assert_that(result.exit_code).is_equal_to(1)
    assert_that(result.output).contains("not found")


def test_deps_no_manifest_found_on_discovery(
    cli_runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Auto-discovery with no manifests exits 0.

    Args:
        cli_runner: The Click runner.
        tmp_path: Temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.chdir(tmp_path)
    result = cli_runner.invoke(cli, ["deps"])
    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains("No dependency manifests found")


def test_deps_strict_discovery_fails_on_empty_discovery(
    cli_runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--strict-discovery`` turns an empty discovery into a gate failure.

    Args:
        cli_runner: The Click runner.
        tmp_path: Temporary directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.chdir(tmp_path)
    result = cli_runner.invoke(cli, ["deps", "--strict-discovery"])
    assert_that(result.exit_code).is_equal_to(1)
    assert_that(result.output).contains("No dependency manifests found")


def test_deps_strict_discovery_help_documents_default(
    cli_runner: CliRunner,
) -> None:
    """The command help states the empty-discovery exit behavior.

    Args:
        cli_runner: The Click runner.
    """
    result = cli_runner.invoke(cli, ["deps", "--help"])
    assert_that(result.output).contains("--strict-discovery")
    assert_that(result.output).contains("exits 0")


def test_deps_invalid_requirement_exits_non_zero(
    cli_runner: CliRunner,
    tmp_path: Path,
) -> None:
    """A malformed requirement is a parse error, not a silently dropped line.

    Args:
        cli_runner: The Click runner.
        tmp_path: Temporary directory.
    """
    manifest = tmp_path / "requirements.txt"
    manifest.write_text("requests==2.31.0\nbroken>=\n")
    result = cli_runner.invoke(
        cli,
        ["deps", "--file", str(manifest), "--format", "json"],
    )
    assert_that(result.exit_code).is_equal_to(1)
    payload = json.loads(result.output)
    assert_that(payload["passed"]).is_false()
    assert_that(payload["parse_errors"]).is_length(1)


def test_deps_grid_does_not_claim_success_with_parse_errors(
    cli_runner: CliRunner,
    tmp_path: Path,
) -> None:
    """Grid output never prints the green summary alongside parse errors.

    Args:
        cli_runner: The Click runner.
        tmp_path: Temporary directory.
    """
    good = tmp_path / "pyproject.toml"
    good.write_text(
        "\n".join(
            [
                "[project]",
                'name = "demo"',
                'dependencies = ["pydantic==2.0"]',
            ],
        ),
    )
    bad = tmp_path / "package.json"
    bad.write_text("{not json")
    result = cli_runner.invoke(
        cli,
        ["deps", "--file", str(good), "--file", str(bad)],
    )
    assert_that(result.exit_code).is_equal_to(1)
    assert_that(result.output).does_not_contain("satisfy the policy")
    assert_that(result.output).contains("failed to")


def test_deps_npm_alternatives_flagged_end_to_end(
    cli_runner: CliRunner,
    tmp_path: Path,
) -> None:
    """A mixed ``||`` spec fails the default flexible policy via the CLI.

    Args:
        cli_runner: The Click runner.
        tmp_path: Temporary directory.
    """
    manifest = tmp_path / "package.json"
    manifest.write_text(
        json.dumps({"dependencies": {"mixed": ">=1.0.0 || >=2.0.0 <3.0.0"}}),
    )
    result = cli_runner.invoke(
        cli,
        ["deps", "--file", str(manifest), "--format", "json"],
    )
    assert_that(result.exit_code).is_equal_to(1)
    payload = json.loads(result.output)
    assert_that(payload["violations"][0]["package"]).is_equal_to("mixed")
