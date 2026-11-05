"""Invalid ``ai:`` config-file values are a usage error on ``fmt``/``chk`` (#3116).

A value the ``AIConfig`` model rejects used to escape the run pipeline as a raw
pydantic ``ValidationError`` traceback (exit 1) — from the ``fmt`` checkpoint
hook, from the pre-execution AI status row, and from the post-run AI layer.
Each seam now reports it the way an invalid ``LINTRO_AI_*`` override already
is: one ``Error:`` line naming the field and the constraint, exit 2, and never
the rejected value itself.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from assertpy import assert_that
from click.testing import CliRunner
from pydantic import BaseModel, ValidationError

from lintro.cli import cli

_SOURCE = "x = 1\n"

#: ``(ai: body, field path the error must name)``. The ``api_key_env`` cases
#: carry a key-shaped value that must never be echoed back: the list form fails
#: on type, the string form on the validator that recognises a pasted key.
_INVALID = {
    "wrong_type": ("  max_tokens: lots\n", "ai.max_tokens"),
    "out_of_range": ("  max_tokens: 0\n", "ai.max_tokens"),
    "unknown_enum": ("  transport: carrier-pigeon\n", "ai.transport"),
    "nested_range": (
        "  transports:\n    api:\n      timeout: -5\n",
        "ai.transports.api.timeout",
    ),
    "secret_shaped": (
        '  api_key_env: ["sk-ant-NOT-A-REAL-KEY-0123456789"]\n',
        "ai.api_key_env",
    ),
    "secret_shaped_string": (
        '  api_key_env: "sk-ant-NOT-A-REAL-KEY-0123456789"\n',
        "ai.api_key_env",
    ),
    "nested_provider": (
        "  providers:\n    anthropic:\n      cli_bare: sometimes-maybe\n",
        "ai.providers.anthropic.cli_bare",
    ),
}


@pytest.fixture
def make_project(
    ai_config_project: Callable[[str], Path],
) -> Callable[[str | None], Path]:
    """Return a factory for a one-file project with an isolated user tier.

    The isolation — home tier redirected, inherited ``LINTRO_AI_*`` overlays
    unset, memoised config cleared — is the shared ``ai_config_project``
    fixture's, so a run resolves only the project's own
    ``.lintro-config.yaml``.

    Args:
        ai_config_project: Writer for an isolated project config.

    Returns:
        A callable taking the indented ``ai:`` body (None for no ``ai:``
        section) and returning the source file the run targets, with the
        project already the working directory.
    """

    def _make(ai_body: str | None) -> Path:
        config = "tools:\n  ruff:\n    enabled: true\n"
        if ai_body is not None:
            config += "ai:\n" + ai_body
        target = ai_config_project(config) / "a.py"
        target.write_text(_SOURCE, encoding="utf-8")
        return target

    return _make


@pytest.mark.parametrize("case", sorted(_INVALID))
@pytest.mark.parametrize(
    "argv",
    [
        ["fmt"],
        ["chk"],
        ["fmt", "--output-format", "json"],
        ["chk", "--output-format", "json"],
    ],
    ids=["fmt", "chk", "fmt-json", "chk-json"],
)
def test_invalid_ai_config_value_is_a_one_line_usage_error(
    make_project: Callable[[str | None], Path],
    case: str,
    argv: list[str],
) -> None:
    """Every run surface exits 2 with the field path, no traceback, no value."""
    ai_body, field_path = _INVALID[case]
    target = make_project(ai_body)

    result = CliRunner().invoke(cli, [*argv, "--tools", "ruff", "--no-art", "a.py"])

    assert_that(result.exception).described_as(repr(result.exception)).is_instance_of(
        SystemExit,
    )
    assert_that(result.exit_code).is_equal_to(2)
    error_lines = [line for line in result.output.splitlines() if "Error:" in line]
    assert_that(error_lines).is_length(1)
    assert_that(error_lines[0]).contains("invalid AI configuration", field_path)
    assert_that(result.output).does_not_contain(
        "Traceback",
        "validation error for",
        "input_value",
        "sk-ant-",
        "carrier-pigeon",
        "sometimes-maybe",
    )
    assert_that(target.read_text(encoding="utf-8")).is_equal_to(_SOURCE)


@pytest.mark.parametrize(
    "ai_body",
    [None, "  enabled: false\n  max_tokens: 1000\n"],
    ids=["no-ai-section", "valid-ai-section"],
)
@pytest.mark.parametrize("command", ["fmt", "chk"])
def test_valid_or_absent_ai_config_still_runs_clean(
    make_project: Callable[[str | None], Path],
    ai_body: str | None,
    command: str,
) -> None:
    """Controls: a valid or missing ``ai:`` section reaches the formatter."""
    make_project(ai_body)

    result = CliRunner().invoke(cli, [command, "--tools", "ruff", "--no-art", "a.py"])

    assert_that(result.exception).described_as(result.output).is_none()
    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).does_not_contain("invalid AI configuration")


class _HookPayload(BaseModel):
    """A model the AI hook might validate that has nothing to do with ``ai:``."""

    count: int


def test_a_validation_error_inside_the_ai_hook_is_not_a_config_error(
    make_project: Callable[[str | None], Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With ``fail_on_ai_error`` the hook's own failure surfaces unrelabelled.

    The config is valid, so the ``ValidationError`` the hook raises is not
    about ``ai:``; reporting it as "invalid AI configuration" would send the
    user to fix a config that is fine. It propagates like any other hook
    failure under ``ai.fail_on_ai_error`` instead.
    """
    from lintro.ai.hook import AIPostExecutionHook

    def _execute(*_args: object, **_kwargs: object) -> None:
        _HookPayload.model_validate({"count": "not-a-number"})

    monkeypatch.setattr(AIPostExecutionHook, "execute", _execute)
    target = make_project(
        "  enabled: true\n  provider: anthropic\n  fail_on_ai_error: true\n",
    )

    result = CliRunner().invoke(
        cli,
        ["chk", "--output-format", "json", "--tools", "ruff", "--no-art", "a.py"],
    )

    assert_that(result.exception).is_instance_of(ValidationError)
    assert_that(str(result.exception)).contains("_HookPayload")
    assert_that(result.exit_code).is_equal_to(1)
    assert_that(result.output).does_not_contain("invalid AI configuration")
    assert_that(target.read_text(encoding="utf-8")).is_equal_to(_SOURCE)


#: The early-exit skip does not depend on which value ``ai:`` rejects, so two
#: representative cases pin it: a plain range error and a pasted key.
_EARLY_EXIT_CASES = ("out_of_range", "secret_shaped_string")


@pytest.mark.parametrize("case", _EARLY_EXIT_CASES)
def test_an_early_exit_run_skips_the_ai_config_check(
    make_project: Callable[[str | None], Path],
    case: str,
) -> None:
    """A run that stops before any tool ran reports its own error, not ``ai:``.

    An unknown ``--tools`` name makes the executor return an ``early_exit``
    artifact. JSON output keeps the pre-execution AI status row (the other
    seam that validates ``ai:``) out of the way, so only the post-run check is
    in play: it is skipped for an early exit, just as the AI hook is, and the
    run exits 1 with the executor's error instead of 2 with the AI one.
    """
    ai_body, field_path = _INVALID[case]
    target = make_project(ai_body)

    result = CliRunner().invoke(
        cli,
        [
            "chk",
            "--output-format",
            "json",
            "--tools",
            "no-such-tool",
            "--no-art",
            "a.py",
        ],
    )

    assert_that(result.exception).described_as(repr(result.exception)).is_instance_of(
        SystemExit,
    )
    assert_that(result.exit_code).described_as(result.output).is_equal_to(1)
    assert_that(result.output).contains("no-such-tool")
    assert_that(result.output).does_not_contain(
        "invalid AI configuration",
        field_path,
        "Traceback",
        "sk-ant-",
    )
    assert_that(target.read_text(encoding="utf-8")).is_equal_to(_SOURCE)
