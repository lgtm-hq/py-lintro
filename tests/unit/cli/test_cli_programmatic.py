"""Programmatic invocation tests for CLI command functions."""

from __future__ import annotations

from typing import Any

import pytest
from assertpy import assert_that

from lintro.cli_utils.commands.check import check as check_prog
from lintro.cli_utils.commands.format import format_code
from lintro.enums.action import Action


def _recorder(calls: list[dict[str, Any]]) -> Any:
    """Build a plain stand-in for the pipeline that records its arguments.

    Args:
        calls: List the stand-in appends each invocation's keyword arguments to.

    Returns:
        A callable accepting the pipeline's keyword arguments and returning 0.
    """

    def _run(**kwargs: Any) -> int:
        calls.append(dict(kwargs))
        return 0

    return _run


def test_check_programmatic_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Programmatic check returns None on success.

    Args:
        monkeypatch: Pytest monkeypatch fixture to stub executor return.
    """
    import lintro.api.core as api_core

    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(api_core, "run_lint_with_ai", _recorder(calls), raising=True)

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

    assert_that(calls).is_length(1)
    assert_that(calls[0]["paths"]).is_equal_to(["."])
    assert_that(calls[0]["tools"]).is_equal_to("ruff")
    assert_that(calls[0]["action"]).is_equal_to(Action.CHECK)


def test_check_programmatic_failure_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Programmatic check raises SystemExit when executor returns non-zero.

    Args:
        monkeypatch: Pytest fixture for patching modules and attributes.
    """
    import lintro.api.core as api_core

    monkeypatch.setattr(
        api_core,
        "run_lint_with_ai",
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

    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        api_core,
        "run_lint_with_ai",
        _recorder(calls),
        raising=True,
    )

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

    assert_that(calls).is_length(1)
    assert_that(calls[0]["paths"]).is_equal_to(["."])
    assert_that(calls[0]["tools"]).is_equal_to("prettier")
    assert_that(calls[0]["action"]).is_equal_to("fmt")


def test_format_programmatic_failure_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Programmatic format raises when executor returns non-zero.

    Args:
        monkeypatch: Pytest fixture for patching modules and attributes.
    """
    import lintro.api.core as api_core

    monkeypatch.setattr(
        api_core,
        "run_lint_with_ai",
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
