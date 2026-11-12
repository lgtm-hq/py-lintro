"""The one-line description of a rejected ``ai:`` section (#3116).

:func:`~lintro.ai.provider_blocks.describe_config_error` is what every surface
reporting an invalid ``ai:`` value prints: ``fmt``/``chk``/``status``,
``lintro review``, the ``lintro_review`` MCP tool and ``lintro config``. These
tests pin its three shapes directly — a plain field gains its ``ai.`` path, a
validator message that already names one is kept as written, and a
model-level error (empty ``loc``) is never given a dangling ``ai.:`` prefix —
and pin the text ``lintro config`` shows for a plain field.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from assertpy import assert_that
from click.testing import CliRunner
from pydantic import BaseModel, ValidationError, model_validator

from lintro.ai.config import AIConfig
from lintro.ai.exceptions import AIConfigOverrideError
from lintro.ai.provider_blocks import (
    describe_config_error,
    format_invalid_ai_config,
)
from lintro.cli import cli

_PASTED_KEY = "sk-ant-NOT-A-REAL-KEY-0123456789"


class _NeedsOneOf(BaseModel):
    """A model whose only failure is model-level, so its ``loc`` is empty."""

    left: int | None = None
    right: int | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> _NeedsOneOf:
        if (self.left is None) == (self.right is None):
            raise ValueError("set exactly one of left or right")
        return self


def _rejection(build: Callable[[], object]) -> ValidationError:
    """Return the ``ValidationError`` raised by ``build``.

    Args:
        build: Callable expected to fail validation.

    Returns:
        The error it raised.
    """
    with pytest.raises(ValidationError) as caught:
        build()
    return caught.value


def test_a_plain_field_error_gains_its_ai_path() -> None:
    """A field the model rejects is named by its ``ai.`` path, not its value."""
    exc = _rejection(lambda: AIConfig(max_tokens=-7))

    assert_that(describe_config_error(exc)).is_equal_to(
        "ai.max_tokens: Input should be greater than or equal to 1",
    )


def test_an_already_prefixed_validator_message_is_kept_as_written() -> None:
    """A validator that names ``ai.<field>`` itself is not prefixed twice."""
    exc = _rejection(lambda: AIConfig(api_key_env=_PASTED_KEY))
    assert_that(exc.errors()[0]["loc"]).is_equal_to(("api_key_env",))

    line = describe_config_error(exc)

    assert_that(line).starts_with("ai.api_key_env must be the NAME")
    assert_that(line).does_not_contain("ai.api_key_env: ", "Value error", _PASTED_KEY)


def test_a_model_level_provider_block_error_keeps_its_own_path() -> None:
    """An empty ``loc`` whose message names ``ai.providers…`` is kept as is."""
    exc = _rejection(
        lambda: AIConfig.resolve_from_mapping(
            {"providers": {"anthropic": {"cli_bare": "sometimes-maybe"}}},
            diagnostics=False,
        ),
    )
    assert_that(exc.errors()[0]["loc"]).is_empty()

    line = describe_config_error(exc)

    assert_that(line).starts_with("ai.providers.anthropic.cli_bare: ")
    assert_that(line).does_not_contain("Value error", "sometimes-maybe")


def test_a_model_level_error_without_a_path_gets_no_empty_prefix() -> None:
    """An empty ``loc`` never renders as ``ai.: …``; the message stands alone."""
    exc = _rejection(lambda: _NeedsOneOf())
    assert_that(exc.errors()[0]["loc"]).is_empty()

    assert_that(describe_config_error(exc)).is_equal_to(
        "set exactly one of left or right",
    )


def test_a_non_pydantic_failure_reports_its_first_non_blank_line() -> None:
    """An override error keeps its own text, trimmed to one line."""
    exc = AIConfigOverrideError("\n  LINTRO_AI_MAX_TOKENS='x' is not an int  \nmore")

    assert_that(describe_config_error(exc)).is_equal_to(
        "LINTRO_AI_MAX_TOKENS='x' is not an int",
    )


def test_the_shared_usage_error_prefixes_the_described_line() -> None:
    """Every surface's usage error is the prefix plus the one-line description.

    ``chk``/``fmt``, ``lintro review`` and MCP ``lintro_review`` all print
    this text, and the rejected value stays out of it.
    """
    exc = _rejection(lambda: AIConfig(api_key_env=_PASTED_KEY))

    message = format_invalid_ai_config(exc)

    assert_that(message).is_equal_to(
        "invalid AI configuration: ai.api_key_env must be the NAME of an "
        "environment variable (letters, digits and underscores, not starting "
        "with a digit), never the key itself",
    )
    assert_that(message).does_not_contain(_PASTED_KEY, "\n")


def _without_path(text: str, project: Path) -> str:
    """Return *text* with whitespace removed and *project*'s path cut out.

    Whitespace is dropped first so a path the report wrapped across lines is
    whole again; both the path as given and its resolved form (``/tmp`` is
    ``/private/tmp`` on macOS) are removed.

    Args:
        text: Rendered command output.
        project: Project root whose path the output may print.

    Returns:
        str: The output, whitespace-free, with every copy of the path removed.
    """
    squashed = "".join(text.split())
    for path in sorted({str(project), str(project.resolve())}, key=len, reverse=True):
        squashed = squashed.replace("".join(path.split()), "<project>")
    return squashed


def test_without_path_cuts_a_wrapped_project_path(tmp_path: Path) -> None:
    """A path wrapped across lines is still removed, and other text is kept."""
    project = tmp_path / "pytest-712" / "project"
    head, tail = str(project)[:12], str(project)[12:]

    scrubbed = _without_path(
        f"Source: {head}\n  {tail}/.lintro\n-config.yaml -7",
        project,
    )

    assert_that(scrubbed).is_equal_to("Source:<project>/.lintro-config.yaml-7")


@pytest.fixture
def plain_field_project(ai_config_project: Callable[[str], Path]) -> Path:
    """Return a project whose ``ai:`` section has one out-of-range field.

    Args:
        ai_config_project: Writer for an isolated project config.

    Returns:
        Path: The project root, already the working directory.
    """
    return ai_config_project("ai:\n  max_tokens: -7\n")


def test_lintro_config_rich_row_names_the_plain_field(
    plain_field_project: Path,
) -> None:
    """The ``AI Settings`` row reads ``ai.<field>: <constraint>``.

    The report also prints the config's absolute path, which Rich may wrap
    mid-word, and pytest's temp root can itself contain ``-7``
    (``pytest-712``). The rejected value is therefore looked for in the
    report with that path removed, so only the report's own text can trip it.
    """
    result = CliRunner().invoke(cli, ["config"])

    assert_that(result.exit_code).described_as(result.output).is_equal_to(0)
    output = " ".join(result.output.split())
    assert_that(output).contains(
        "AI Settings ai.max_tokens: Input should be greater than or equal to 1",
    )
    assert_that(output).does_not_contain("validation error for")
    assert_that(_without_path(result.output, plain_field_project)).does_not_contain(
        "-7",
    )


def test_lintro_config_json_error_names_the_plain_field(
    plain_field_project: Path,
) -> None:
    """``lintro config --json`` carries the same line under ``ai.error``."""
    del plain_field_project

    result = CliRunner().invoke(cli, ["config", "--json"])

    assert_that(result.exit_code).described_as(result.output).is_equal_to(0)
    payload = json.loads(result.stdout)
    assert_that(payload["ai"]).is_equal_to(
        {"error": "ai.max_tokens: Input should be greater than or equal to 1"},
    )
