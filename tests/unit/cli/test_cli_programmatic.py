"""Programmatic invocation tests for CLI command functions."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from assertpy import assert_that

from lintro.cli_utils.commands.check import check as check_prog
from lintro.cli_utils.commands.format import format_code
from lintro.enums.action import Action


def test_check_programmatic_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Programmatic check returns None on success.

    Args:
        monkeypatch: Pytest monkeypatch fixture to stub executor return.
    """
    import lintro.api.core as api_core

    mock_run = MagicMock(return_value=0)
    monkeypatch.setattr(api_core, "run_lint_tools_simple", mock_run, raising=True)

    # Function returns None on success (no exception raised)
    check_prog(
        paths=(".",),
        tools="ruff",
        tool_options=None,
        exclude=None,
        include_venv=False,
        output=None,
        output_format="grid",
        group_by="auto",
        ignore_conflicts=False,
        verbose=False,
        no_log=False,
    )

    mock_run.assert_called_once()
    call_kwargs = mock_run.call_args.kwargs
    assert_that(call_kwargs["paths"]).is_equal_to(["."])
    assert_that(call_kwargs["tools"]).is_equal_to("ruff")
    assert_that(call_kwargs["action"]).is_equal_to(Action.CHECK)


def test_check_programmatic_failure_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Programmatic check raises SystemExit when executor returns non-zero.

    Args:
        monkeypatch: Pytest fixture for patching modules and attributes.
    """
    import lintro.api.core as api_core

    monkeypatch.setattr(
        api_core,
        "run_lint_tools_simple",
        lambda **k: 1,
        raising=True,
    )
    with pytest.raises(SystemExit) as exc_info:
        check_prog(
            paths=(".",),
            tools="ruff",
            tool_options=None,
            exclude=None,
            include_venv=False,
            output=None,
            output_format="grid",
            group_by="auto",
            ignore_conflicts=False,
            verbose=False,
            no_log=False,
        )
    assert_that(exc_info.value.code).is_equal_to(1)


def test_format_programmatic_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Programmatic format returns None on success.

    Args:
        monkeypatch: Pytest fixture for patching modules and attributes.
    """
    import lintro.api.core as api_core

    mock_run = MagicMock(return_value=0)
    monkeypatch.setattr(
        api_core,
        "run_lint_tools_simple",
        mock_run,
        raising=True,
    )

    # Function returns None on success (no exception raised)
    format_code(
        paths=["."],
        tools="prettier",
        tool_options=None,
        exclude=None,
        include_venv=False,
        group_by="auto",
        output_format="grid",
        verbose=False,
    )

    mock_run.assert_called_once()
    call_kwargs = mock_run.call_args.kwargs
    assert_that(call_kwargs["paths"]).is_equal_to(["."])
    assert_that(call_kwargs["tools"]).is_equal_to("prettier")
    assert_that(call_kwargs["action"]).is_equal_to("fmt")


def test_format_programmatic_failure_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Programmatic format raises when executor returns non-zero.

    Args:
        monkeypatch: Pytest fixture for patching modules and attributes.
    """
    import lintro.api.core as api_core

    monkeypatch.setattr(
        api_core,
        "run_lint_tools_simple",
        lambda **k: 1,
        raising=True,
    )
    with pytest.raises(RuntimeError):
        format_code(
            paths=["."],
            tools="prettier",
            tool_options=None,
            exclude=None,
            include_venv=False,
            group_by="auto",
            output_format="grid",
            verbose=False,
        )
