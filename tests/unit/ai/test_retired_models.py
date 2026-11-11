"""A retired model id is refused at validation, naming its replacement (#2277).

Requests to a retired id fail at the vendor, so every surface that accepts a
model refuses one before any request is made: config validation, the
``LINTRO_AI_MODEL`` and ``--model`` overlays, and ``lintro doctor``. The
provider metadata that declares the retirements is checked here too, so a
retirement table cannot contradict the live pricing table.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from assertpy import assert_that
from click.testing import CliRunner
from pydantic import ValidationError

from lintro.ai.config import AIConfig
from lintro.ai.effective_config import AICliOverrides, resolve_effective_ai_config
from lintro.ai.enums import AITransport
from lintro.ai.exceptions import AIConfigOverrideError
from lintro.ai.model_lifecycle import RetiredModel
from lintro.ai.model_pricing import ModelPricing
from lintro.ai.provider_blocks import format_invalid_ai_config
from lintro.ai.providers.protocol import ProviderMetadata
from lintro.ai.registry import AIProvider, metadata_for, retired_model_error
from lintro.cli import cli

#: Anthropic's deprecations page, checked 2026-11-11: both dated Claude 4 ids
#: were retired on 2026-06-15, with these listed replacements.
_RETIRED = (
    ("claude-sonnet-4-20250514", "claude-sonnet-5-5"),
    ("claude-opus-4-20250514", "claude-opus-4-8"),
)


@pytest.mark.parametrize(("model", "replacement"), _RETIRED)
def test_retired_model_error_names_the_date_and_the_replacement(
    model: str,
    replacement: str,
) -> None:
    """The shared refusal names the vendor, the date and the replacement.

    Args:
        model: Retired model identifier.
        replacement: The replacement Anthropic lists for it.
    """
    assert_that(retired_model_error(model)).is_equal_to(
        f"{model!r} was retired by Anthropic on 2026-06-15 and requests to it "
        f"fail; use {replacement!r} instead",
    )


def test_a_current_model_is_not_reported_as_retired() -> None:
    """Ids the registry prices, and ids it has never heard of, pass through."""
    assert_that(retired_model_error("claude-opus-4-8")).is_none()
    assert_that(retired_model_error("my-local-vllm-model")).is_none()


@pytest.mark.parametrize(("model", "replacement"), _RETIRED)
def test_config_file_model_is_refused_with_the_replacement(
    model: str,
    replacement: str,
) -> None:
    """``ai.model`` naming a retired id fails as the one-line config error.

    Args:
        model: Retired model identifier.
        replacement: The replacement Anthropic lists for it.
    """
    with pytest.raises(ValidationError) as caught:
        resolve_effective_ai_config({"provider": "anthropic", "model": model})

    line = format_invalid_ai_config(caught.value)
    assert_that(line).starts_with(f"invalid AI configuration: ai.model: {model!r}")
    assert_that(line).contains(f"use {replacement!r} instead")


def test_retired_fallback_model_is_refused() -> None:
    """A retired id in ``ai.fallback_models`` fails before it is ever needed."""
    with pytest.raises(ValidationError) as caught:
        AIConfig(fallback_models=["claude-sonnet-4-6", "claude-opus-4-20250514"])

    line = format_invalid_ai_config(caught.value)
    assert_that(line).starts_with("invalid AI configuration: ai.fallback_models: ")
    assert_that(line).contains("use 'claude-opus-4-8' instead")


def test_env_override_naming_a_retired_model_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``LINTRO_AI_MODEL`` is refused with the variable and the replacement.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setenv("LINTRO_AI_MODEL", "claude-sonnet-4-20250514")

    with pytest.raises(AIConfigOverrideError) as caught:
        resolve_effective_ai_config({"provider": "anthropic"})

    assert_that(str(caught.value)).starts_with(
        "LINTRO_AI_MODEL='claude-sonnet-4-20250514' is not a valid value for "
        "ai.model: ",
    ).contains("use 'claude-sonnet-5-5' instead")


def test_model_flag_naming_a_retired_model_is_refused() -> None:
    """``--model`` is refused the same way, so no run starts on it."""
    with pytest.raises(AIConfigOverrideError) as caught:
        resolve_effective_ai_config(
            {"provider": "anthropic", "model": "claude-sonnet-4-6"},
            cli_overrides=AICliOverrides(model="claude-opus-4-20250514"),
        )

    assert_that(str(caught.value)).starts_with(
        "--model='claude-opus-4-20250514'",
    ).contains("use 'claude-opus-4-8' instead")


def test_replacement_models_validate() -> None:
    """Following the advice works: each replacement is accepted as is."""
    for _model, replacement in _RETIRED:
        config = AIConfig(
            provider=AIProvider.ANTHROPIC,
            transport=AITransport.API,
            model=replacement,
        )
        assert_that(config.model).is_equal_to(replacement)


def test_doctor_reports_a_retired_model_as_a_usage_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``lintro doctor`` exits 2 naming the replacement rather than a traceback.

    Args:
        tmp_path: Pytest temporary directory holding the project config.
        monkeypatch: Pytest monkeypatch fixture.
    """
    (tmp_path / ".lintro-config.yaml").write_text(
        "ai:\n"
        "  enabled: true\n"
        "  provider: anthropic\n"
        "  transport: api\n"
        "  model: claude-opus-4-20250514\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(cli, ["doctor", "--tools", "ruff"])

    assert_that(result.exit_code).is_equal_to(2)
    assert_that(result.exception).is_instance_of(SystemExit)
    assert_that(" ".join(result.output.split())).contains(
        "invalid AI configuration: ai.model: 'claude-opus-4-20250514' was "
        "retired by Anthropic on 2026-06-15",
    ).contains("use 'claude-opus-4-8' instead")


def _metadata(
    *,
    pricing: dict[str, ModelPricing],
    retired: dict[str, RetiredModel],
    default_model: str = "live-model",
) -> ProviderMetadata:
    """Build provider metadata with the given model tables.

    Args:
        pricing: The live pricing table.
        retired: The retirement table.
        default_model: The declared default model.

    Returns:
        The constructed metadata record.
    """
    return ProviderMetadata(
        provider=AIProvider.ANTHROPIC,
        display_name="Example",
        default_model=default_model,
        default_api_key_env="EXAMPLE_API_KEY",
        supported_transports=frozenset({AITransport.API}),
        default_transport=AITransport.API,
        pricing=pricing,
        retired_models=retired,
    )


_LIVE = {"live-model": ModelPricing(1.0, 2.0)}
_GONE = RetiredModel(retired_on=date(2026, 1, 1), replacement="live-model")


def test_metadata_refuses_a_retired_model_that_is_still_priced() -> None:
    """A retired id left in ``pricing`` is the regression #2277 fixed."""
    with pytest.raises(ValueError, match="prices retired model 'old-model'"):
        _metadata(
            pricing={**_LIVE, "old-model": ModelPricing(1.0, 2.0)},
            retired={"old-model": _GONE},
        )


def test_metadata_refuses_a_retired_default_model() -> None:
    """A provider cannot default to a model its vendor no longer serves."""
    with pytest.raises(ValueError, match="retired model 'old-model' as its default"):
        _metadata(
            pricing=_LIVE,
            retired={"old-model": _GONE},
            default_model="old-model",
        )


def test_metadata_refuses_an_unpriced_replacement() -> None:
    """The advice must land on a model lintro prices, not the fallback."""
    with pytest.raises(ValueError, match="replacement 'elsewhere'"):
        _metadata(
            pricing=_LIVE,
            retired={
                "old-model": RetiredModel(
                    retired_on=date(2026, 1, 1),
                    replacement="elsewhere",
                ),
            },
        )


def test_metadata_retirement_table_is_read_only() -> None:
    """Consumers treat metadata as a constant, so the table cannot be edited."""
    record = metadata_for(AIProvider.ANTHROPIC)

    with pytest.raises(TypeError):
        record.retired_models["x"] = _GONE  # type: ignore[index]
    assert_that(dict(record.retired_models)).does_not_contain_key("x")
