"""Tests for the syntax-highlighting build self-check (#2514)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.cli_utils.commands.doctor import doctor_command
from lintro.cli_utils.highlighting_check import (
    CHECKED_LANGUAGES,
    check_syntax_highlighting,
)


def test_every_checked_language_resolves_a_real_lexer() -> None:
    """Each language resolves a named lexer and produces style spans."""
    result = check_syntax_highlighting()
    assert_that(result.failures).is_empty()
    assert_that(result.ok).is_true()
    assert_that(result.details).is_length(len(CHECKED_LANGUAGES))
    for language, detail in zip(CHECKED_LANGUAGES, result.details, strict=True):
        assert_that(detail).starts_with(f"{language}: ")
        assert_that(detail).contains("style spans")


def test_the_diff_lexer_the_binary_actually_uses_is_checked() -> None:
    """`diff` is covered, because it is pygments' only use in the product.

    `lintro/ai/interactive.py` renders every AI fix with
    `Syntax(fix.diff, "diff", ...)`. A self-check that resolved python, yaml
    and json but never `diff` would pass on a binary whose one real lexer had
    not been packaged.
    """
    assert_that(CHECKED_LANGUAGES).contains("diff")
    result = check_syntax_highlighting(languages=("diff",))
    assert_that(result.ok).is_true()
    assert_that(result.details).is_length(1)
    assert_that(result.details[0]).starts_with("diff: DiffLexer,")


def test_the_checked_language_of_the_interactive_call_site_is_not_drifted() -> None:
    """The language literal in the AI review renderer stays in the checked set.

    Guards the pairing itself: if the renderer is ever switched to another
    lexer, this fails until the self-check follows it.
    """
    source = (
        Path(__file__).resolve().parents[3] / "lintro" / "ai" / "interactive.py"
    ).read_text(encoding="utf-8")
    call = re.search(r"Syntax\(\s*fix\.diff,\s*\"(?P<language>[a-z]+)\"", source)
    assert_that(call).is_not_none()
    assert call is not None  # narrow type for mypy
    assert_that(CHECKED_LANGUAGES).contains(call.group("language"))


def test_an_unknown_language_is_reported_as_a_failure() -> None:
    """A language pygments cannot resolve fails the check instead of passing."""
    result = check_syntax_highlighting(languages=("definitely-not-a-language",))
    assert_that(result.ok).is_false()
    assert_that(result.failures).is_length(1)
    assert_that(result.failures[0]).contains("lexer lookup failed")


def test_a_plain_text_fallback_is_reported_as_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Degrading to pygments' plain-text lexer counts as a failure.

    Args:
        monkeypatch: pytest attribute patcher.
    """
    from pygments.lexers.special import TextLexer

    monkeypatch.setattr(
        "pygments.lexers.get_lexer_by_name",
        lambda *args, **kwargs: TextLexer(),
    )
    result = check_syntax_highlighting(languages=("python",))
    assert_that(result.ok).is_false()
    assert_that(result.failures[0]).contains("plain-text fallback lexer")


def test_doctor_self_check_flag_exits_zero_and_skips_the_probes() -> None:
    """The hidden doctor flag runs the check alone and exits successfully."""
    runner = CliRunner()
    result = runner.invoke(doctor_command, ["--self-check-highlighting"])
    assert_that(result.exit_code).is_equal_to(0)
    assert_that(result.output).contains("syntax highlighting python")
    # A tool probe would render the category headings; none may appear.
    assert_that(result.output).does_not_contain("Bundled Python tools")


def test_doctor_self_check_flag_exits_nonzero_when_the_check_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A degraded lexer fails the gate through the CLI, not just in the check.

    Every other CLI test here drives the passing path, which is also all CI
    ever sees, so nothing proved that a real ``ok=False`` reaches the process
    exit code. That mapping is the whole contract
    ``scripts/build/verify_built_binary.sh`` relies on: without it a binary
    shipping unhighlighted output would verify green (#2514).

    Args:
        monkeypatch: pytest attribute patcher.
    """
    from pygments.lexers.special import TextLexer

    monkeypatch.setattr(
        "pygments.lexers.get_lexer_by_name",
        lambda *args, **kwargs: TextLexer(),
    )
    runner = CliRunner()
    result = runner.invoke(doctor_command, ["--self-check-highlighting"])

    assert_that(result.exit_code).described_as(
        "a degraded lexer must fail the build gate",
    ).is_equal_to(1)
    assert_that(result.output).contains("FAIL syntax highlighting")
    assert_that(result.output).contains("plain-text fallback lexer")


def test_doctor_self_check_flag_is_hidden_from_help() -> None:
    """The flag is build tooling, not user surface, so --help omits it."""
    runner = CliRunner()
    result = runner.invoke(doctor_command, ["--help"])
    assert_that(result.output).does_not_contain("--self-check-highlighting")


def test_a_raising_highlighter_is_reported_as_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lexer that resolves but cannot tokenize fails the check.

    This is the shape a bytecode-packaging regression takes when the lexer
    module loads but its token machinery does not.

    Args:
        monkeypatch: pytest attribute patcher.
    """
    import rich.syntax

    def _boom(self: object, code: str) -> None:
        raise RuntimeError("lexer exploded")

    monkeypatch.setattr(rich.syntax.Syntax, "highlight", _boom)
    result = check_syntax_highlighting(languages=("python",))
    assert_that(result.ok).is_false()
    assert_that(result.failures[0]).contains("highlighting failed")
    assert_that(result.failures[0]).contains("lexer exploded")


def test_highlighting_without_style_spans_is_reported_as_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Output with no style spans means nothing was tokenized.

    Args:
        monkeypatch: pytest attribute patcher.
    """
    import rich.syntax
    from rich.text import Text

    monkeypatch.setattr(
        rich.syntax.Syntax,
        "highlight",
        lambda self, code: Text(code),
    )
    result = check_syntax_highlighting(languages=("python",))
    assert_that(result.ok).is_false()
    assert_that(result.failures[0]).contains("produced no style spans")


def test_doctor_self_check_flag_still_rejects_incoherent_combinations() -> None:
    """The self-check does not short-circuit the flag-combination guard."""
    runner = CliRunner()
    result = runner.invoke(
        doctor_command,
        ["--self-check-highlighting", "--fix", "--json"],
    )
    assert_that(result.exit_code).is_not_equal_to(0)
    assert_that(result.output).contains(
        "--fix cannot be combined with --report or --json",
    )
