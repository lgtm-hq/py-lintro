"""A retired model id is refused at validation, naming its replacement (#2277).

Requests to a retired id fail at the vendor, so every surface that accepts a
model refuses one before any request is made: config validation, the
``LINTRO_AI_MODEL`` and ``--model`` overlays, and ``lintro doctor``. The
provider metadata that declares the retirements is checked here too, so a
retirement table cannot contradict the live pricing table.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from assertpy import assert_that
from click.testing import CliRunner
from pydantic import ValidationError

from lintro.ai.budget import CostBudget
from lintro.ai.config import AIConfig
from lintro.ai.effective_config import AICliOverrides, resolve_effective_ai_config
from lintro.ai.enums import AITransport
from lintro.ai.exceptions import (
    AIConfigOverrideError,
    AIProviderError,
    AIRetiredModelError,
)
from lintro.ai.fallback import _with_fallback
from lintro.ai.fix import generate_fixes
from lintro.ai.model_lifecycle import RetiredModel
from lintro.ai.model_pricing import ModelPricing
from lintro.ai.provider_blocks import format_invalid_ai_config
from lintro.ai.providers import get_provider
from lintro.ai.providers.protocol import ProviderMetadata
from lintro.ai.registry import AIProvider, metadata_for, retired_model_error
from lintro.ai.review.custom_agent_runner import (
    CustomAgentPassRequest,
    run_custom_agent_passes,
)
from lintro.ai.review.custom_agent_types import CUSTOM_AGENT_DIR_PARTS
from lintro.ai.review.custom_agents import SelectedCustomAgent, parse_custom_agent
from lintro.ai.review.enums.custom_agent_mode import CustomAgentMode
from lintro.ai.review.exceptions import ReviewPreparationError
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.preparation_resolvers import resolve_custom_agents
from lintro.cli import cli
from lintro.parsers.base_issue import BaseIssue

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
    assert_that(
        retired_model_error(model, provider=AIProvider.ANTHROPIC),
    ).is_equal_to(
        f"{model!r} was retired by Anthropic on 2026-06-15 and requests to it "
        f"fail; use {replacement!r} instead",
    )


def test_a_current_model_is_not_reported_as_retired() -> None:
    """Ids the registry prices, and ids it has never heard of, pass through."""
    for model in ("claude-opus-4-8", "my-local-vllm-model"):
        assert_that(
            retired_model_error(model, provider=AIProvider.ANTHROPIC),
        ).is_none()


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
        AIConfig(
            provider=AIProvider.ANTHROPIC,
            fallback_models=["claude-sonnet-4-6", "claude-opus-4-20250514"],
        )

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


def test_doctor_reports_a_retired_model_as_a_failed_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``lintro doctor`` lists the refusal as a failed check and keeps going.

    Doctor is the diagnosis command: the rest of the report is still printed,
    the exit code is non-zero, and there is no usage text around the error.

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

    result = CliRunner().invoke(cli, ["doctor", "--tools", "ruff", "--json"])

    assert_that(result.exit_code).is_equal_to(1)
    assert_that(result.output).does_not_contain("Usage:")
    payload = json.loads(result.output)
    checks = {check["name"]: check for check in payload["ai"]}
    assert_that(checks["ai.config"]["status"]).is_equal_to("incompatible")
    assert_that(checks["ai.config"]["message"]).is_equal_to(
        "invalid AI configuration: ai.model: 'claude-opus-4-20250514' was "
        "retired by Anthropic on 2026-06-15 and requests to it fail; use "
        "'claude-opus-4-8' instead",
    )
    assert_that(payload["tools"]).contains_key("ruff")


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


# -- Provider scoping (round 1, item 4) --------------------------------------


def test_the_same_string_is_accepted_for_another_provider() -> None:
    """Only the provider that retired an id refuses it.

    An OpenAI-compatible endpoint (vLLM, a gateway) may serve a model under
    any name, including one Anthropic retired, so the string alone is not
    refused when the selected provider is not Anthropic.
    """
    config = AIConfig(
        provider=AIProvider.OPENAI,
        transport=AITransport.API,
        api_base_url="http://localhost:8000/v1",
        model="claude-opus-4-20250514",
        fallback_models=["claude-sonnet-4-20250514"],
    )

    assert_that(config.model).is_equal_to("claude-opus-4-20250514")
    assert_that(
        retired_model_error("claude-opus-4-20250514", provider=AIProvider.OPENAI),
    ).is_none()


def test_switching_the_provider_by_env_brings_its_retirements_into_force(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A config valid for one provider is refused once Anthropic is selected.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setenv("LINTRO_AI_PROVIDER", "anthropic")

    with pytest.raises(AIConfigOverrideError) as caught:
        resolve_effective_ai_config(
            {"provider": "openai", "model": "claude-opus-4-20250514"},
        )

    assert_that(str(caught.value)).contains("use 'claude-opus-4-8' instead")


# -- Provider factory backstop -------------------------------------------------


def test_provider_factory_refuses_a_config_built_without_validation() -> None:
    """``model_copy`` skips validators; the factory still refuses the id."""
    unchecked = AIConfig(
        provider=AIProvider.ANTHROPIC,
        transport=AITransport.API,
    ).model_copy(update={"model": "claude-sonnet-4-20250514"})

    with pytest.raises(AIRetiredModelError, match="use 'claude-sonnet-5-5' instead"):
        get_provider(unchecked)


def test_validated_model_swap_refuses_a_retired_id() -> None:
    """``AIConfig.with_model`` is the checked replacement for ``model_copy``."""
    config = AIConfig(provider=AIProvider.ANTHROPIC, transport=AITransport.API)

    assert_that(config.with_model("claude-opus-4-8").model).is_equal_to(
        "claude-opus-4-8",
    )
    with pytest.raises(ValidationError):
        config.with_model("claude-opus-4-20250514")


# -- Fallback chains (round 1, item 2) -----------------------------------------


def _anthropic_double(*, model: str = "claude-sonnet-4-6") -> MagicMock:
    """Build an Anthropic provider double whose calls are observable.

    Args:
        model: The provider's current (primary) model.

    Returns:
        The provider double; ``complete`` records any call made.
    """
    provider = MagicMock()
    provider.name = "anthropic"
    provider.model_name = model
    provider.complete = AsyncMock(side_effect=AIProviderError("primary down"))
    return provider


async def test_a_retired_fallback_stops_the_chain_before_the_primary_call() -> None:
    """A failing primary is never followed by, or preceded by, a retired id."""
    provider = _anthropic_double()
    attempted: list[str] = []

    async def _attempt(*args: object) -> str:
        attempted.append(str(args[-1]))
        raise AIProviderError("primary down")

    with pytest.raises(AIRetiredModelError) as caught:
        await _with_fallback(
            provider,
            _attempt,
            "prompt",
            fallback_models=["claude-opus-4-20250514"],
        )

    assert_that(str(caught.value)).contains("use 'claude-opus-4-8' instead")
    assert_that(attempted).is_empty()


async def test_fix_generation_refuses_a_retired_programmatic_fallback(
    tmp_path: Path,
) -> None:
    """``generate_fixes`` surfaces the refusal instead of a debug log.

    Its workers swallow per-call failures, so without the up-front check a
    retired fallback would end as zero suggestions and a debug line.

    Args:
        tmp_path: Workspace root holding the file the issue points at.
    """
    source = tmp_path / "mod.py"
    source.write_text("x = 1\n", encoding="utf-8")
    provider = _anthropic_double()
    issue = BaseIssue(file=str(source), line=1, message="example")

    with pytest.raises(AIRetiredModelError) as caught:
        await generate_fixes(
            [issue],
            provider,
            tool_name="ruff",
            workspace_root=tmp_path,
            fallback_models=["claude-sonnet-4-20250514"],
        )

    assert_that(str(caught.value)).contains("use 'claude-sonnet-5-5' instead")
    assert_that(provider.complete.await_args_list).is_empty()


# -- Custom review agents (round 1, item 1) ------------------------------------

_RETIRED_AGENT = """---
name: retired-model-agent
model: claude-opus-4-20250514
include:
  - "src/**/*.py"
---

Review the change.
"""


def test_preparation_refuses_an_agent_naming_a_retired_model(tmp_path: Path) -> None:
    """The run is refused while preparing, before any review call.

    Args:
        tmp_path: Workspace root holding the agent file.
    """
    directory = tmp_path.joinpath(*CUSTOM_AGENT_DIR_PARTS)
    directory.mkdir(parents=True)
    (directory / "retired.md").write_text(_RETIRED_AGENT, encoding="utf-8")

    with pytest.raises(ReviewPreparationError) as caught:
        resolve_custom_agents(
            mode=CustomAgentMode.ENABLED,
            workspace_root=tmp_path,
            provider=AIProvider.ANTHROPIC,
        )

    assert_that(str(caught.value)).starts_with(
        "review agent 'retired-model-agent' sets model: 'claude-opus-4-20250514'",
    ).contains("use 'claude-opus-4-8' instead")


def test_runner_skips_a_retired_agent_without_invoking_a_provider(
    tmp_path: Path,
) -> None:
    """An agent that reached the runner unprepared is skipped, not rerouted.

    The old ``model_copy`` path handed the retired id to the factory, and a
    factory failure fell back to running the agent on the configured model.

    Args:
        tmp_path: Directory used as the agent file location.
    """
    agent = parse_custom_agent(path=tmp_path / "agent.md", text=_RETIRED_AGENT)
    provider = _anthropic_double()
    failed: list[str] = []

    with patch("lintro.ai.providers.get_provider") as factory:
        results = asyncio.run(
            run_custom_agent_passes(
                request=CustomAgentPassRequest(
                    selected=(SelectedCustomAgent(agent=agent, files=("src/a.py",)),),
                    context=ReviewContext(
                        base_ref="base",
                        head_ref="head",
                        changed_files=[],
                        unified_diff="",
                    ),
                    provider=provider,
                    ai_config=AIConfig(
                        provider=AIProvider.ANTHROPIC,
                        transport=AITransport.API,
                    ),
                    provider_cache={},
                    budget=CostBudget(),
                    on_agent_failed=failed.append,
                ),
            ),
        )

    assert_that(results).is_empty()
    assert_that(failed).is_equal_to(["retired-model-agent"])
    assert_that(factory.call_args_list).is_empty()
    assert_that(provider.complete.await_args_list).is_empty()
