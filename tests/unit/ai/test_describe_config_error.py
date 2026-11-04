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
import os
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from assertpy import assert_that
from click.testing import CliRunner
from pydantic import BaseModel, ValidationError, model_validator

from lintro.ai.config import AIConfig
from lintro.ai.exceptions import AIConfigOverrideError
from lintro.ai.provider_blocks import describe_config_error
from lintro.cli import cli
from lintro.config.config_loader import clear_config_cache

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


@pytest.fixture
def plain_field_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Path]:
    """Return a project whose ``ai:`` section has one out-of-range field.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    Yields:
        Path: The project root, already the working directory.
    """
    home = tmp_path / "home"
    home.mkdir()
    for name in [key for key in os.environ if key.startswith("LINTRO_AI_")]:
        monkeypatch.delenv(name)
    monkeypatch.delenv("LINTRO_GLOBAL_CONFIG", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.chdir(root)
    (root / ".lintro-config.yaml").write_text(
        "ai:\n  max_tokens: -7\n",
        encoding="utf-8",
    )
    clear_config_cache()
    yield root
    clear_config_cache()


def test_lintro_config_rich_row_names_the_plain_field(
    plain_field_project: Path,
) -> None:
    """The ``AI Settings`` row reads ``ai.<field>: <constraint>``."""
    del plain_field_project

    result = CliRunner().invoke(cli, ["config"])

    assert_that(result.exit_code).described_as(result.output).is_equal_to(0)
    output = " ".join(result.output.split())
    assert_that(output).contains(
        "AI Settings ai.max_tokens: Input should be greater than or equal to 1",
    )
    assert_that(output).does_not_contain("-7", "validation error for")


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
