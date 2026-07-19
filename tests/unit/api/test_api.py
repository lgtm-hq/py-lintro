"""Unit tests for the real library API (``lintro.api``)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro import api
from lintro.api import LintroResult, check, fmt


def test_api_check_propagates_exceptions() -> None:
    """A failure inside the executor surfaces to the caller unchanged."""
    with patch(
        "lintro.api.core.run_lint_tools_simple",
        side_effect=RuntimeError("boom"),
    ):
        assert_that(check).raises(RuntimeError).when_called_with(
            paths=["."],
            tools="ruff",
        )


def test_api_check_returns_result(tmp_path: Path) -> None:
    """check() returns a structured LintroResult carrying the exit code."""
    sample = tmp_path / "sample.py"
    sample.write_text("x = 1\n", encoding="utf-8")

    with patch("lintro.api.core.run_lint_tools_simple", return_value=0) as mock_run:
        result = check(paths=[str(tmp_path)], tools="ruff")

    assert_that(result).is_instance_of(LintroResult)
    assert_that(result.action).is_equal_to("check")
    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.success).is_true()
    mock_run.assert_called_once()
    assert_that(mock_run.call_args.kwargs["paths"]).is_equal_to([str(tmp_path)])


def test_api_check_result_reports_failure() -> None:
    """A non-zero exit code produces an unsuccessful result (no raise)."""
    with patch("lintro.api.core.run_lint_tools_simple", return_value=1):
        result = check(paths=["."], tools="ruff")

    assert_that(result.exit_code).is_equal_to(1)
    assert_that(result.success).is_false()


def test_api_fmt_is_alias_of_format() -> None:
    """``fmt`` is exported as an alias of ``format``."""
    assert_that(fmt).is_same_as(api.format)


def test_api_format_returns_result() -> None:
    """format() returns a LintroResult tagged with the ``fmt`` action."""
    with patch("lintro.api.core.run_lint_tools_simple", return_value=0) as mock_run:
        result = fmt(paths=["."], tools="ruff")

    assert_that(result.action).is_equal_to("fmt")
    assert_that(result.success).is_true()
    assert_that(mock_run.call_args.kwargs["action"]).is_equal_to("fmt")


def test_api_exports() -> None:
    """The public package exposes the documented entry points."""
    for name in ("check", "format", "fmt", "test", "LintroResult"):
        assert_that(hasattr(api, name)).described_as(name).is_true()


def test_no_clirunner_in_production() -> None:
    """No production module under ``lintro/`` imports ``click.testing``."""
    lintro_dir = Path(__file__).resolve().parents[3] / "lintro"
    assert_that(lintro_dir.is_dir()).described_as(str(lintro_dir)).is_true()

    offenders: list[str] = []
    for py_file in lintro_dir.rglob("*.py"):
        for raw_line in py_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            imports_test_helper = line.startswith(
                ("from click.testing", "import click.testing"),
            )
            instantiates_runner = "CliRunner(" in line
            if imports_test_helper or instantiates_runner:
                offenders.append(f"{py_file}: {line}")

    assert_that(offenders).described_as(
        "click.testing import / CliRunner use found in production lintro package",
    ).is_empty()


@pytest.mark.parametrize(
    "action_name",
    ["check", "format", "test"],
)
def test_api_callables_are_functions(action_name: str) -> None:
    """Each documented API entry point is a real callable."""
    assert_that(callable(getattr(api, action_name))).is_true()
