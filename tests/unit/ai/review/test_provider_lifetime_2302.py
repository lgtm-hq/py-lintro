"""The review run owns provider lifetime and closes it once (issue #2302).

Providers have had ``aclose()`` since #1885, but nothing called it: the run was
handed a provider by its adapter and simply dropped it, and a custom agent with
a ``model`` override built a further provider into a cache that was discarded
with the call frame. :class:`~lintro.ai.review.session.ReviewSession` is the
owner, entered once by
:func:`~lintro.ai.review.orchestrator.run_review_async`, and these tests pin
what "owns" means: every provider the run touched is closed exactly once, on
completion, on a provider failure, on a cost-cap or timeout stop, and on
cancellation mid-chunk.

The doubles count ``aclose()`` calls rather than asserting a mock was awaited,
because the property under test is the *count*: closing twice is as much a bug
as never closing, and only a counter distinguishes them.
"""

from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.ai.config import AIConfig
from lintro.ai.enums import AITransport
from lintro.ai.exceptions import AIProviderError
from lintro.ai.providers.capabilities import ProviderCapabilities
from lintro.ai.providers.response import AIResponse
from lintro.ai.review.custom_agents import parse_custom_agent
from lintro.ai.review.enums.changed_file_status import ChangedFileStatus
from lintro.ai.review.exceptions import ReviewExecutionError
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.models.review_context import ReviewContext
from lintro.ai.review.orchestrator import run_review, run_review_async
from lintro.ai.review.session import ReviewSession, ReviewSessionOptions

if TYPE_CHECKING:
    from lintro.ai.review.custom_agent_types import CustomAgentSpec

_MODEL = "claude-sonnet-4-20250514"

_AGENT_TEXT = """---
name: no-raw-sql
description: SQL must go through the repository layer
include:
  - "src/**/*.py"
severity: high
model: {model}
---

Flag raw SQL executed outside the repository layer.
"""

_DIFF = """diff --git a/src/app.py b/src/app.py
--- a/src/app.py
+++ b/src/app.py
@@ -1,2 +1,3 @@
 import db
+cursor.execute("SELECT 1")
"""


class SpyProvider:
    """A provider double that records how often it was closed.

    Only the surface a review run actually touches is implemented; what the
    tests read is :attr:`aclose_calls`.

    ``name`` and ``model_name`` are what the run reads for its error taxonomy
    and context window, ``capabilities`` is declared explicitly so a
    single-chunk run does not take the durable-session path on a truthy
    attribute, ``aclose_calls`` counts the closes, and ``close_error`` makes
    the teardown itself fail.
    """

    def __init__(
        self,
        *,
        name: str = "anthropic",
        model_name: str = _MODEL,
        close_error: Exception | None = None,
    ) -> None:
        """Build a spy provider.

        Args:
            name: Provider name reported to the run.
            model_name: Model name reported to the run.
            close_error: Exception ``aclose`` should raise, if any.
        """
        self.name = name
        self.model_name = model_name
        self.capabilities = ProviderCapabilities(supports_sessions=False)
        self.aclose_calls = 0
        self.close_error = close_error

    async def aclose(self) -> None:
        """Record the close, then re-raise ``close_error`` when one is set."""
        self.aclose_calls += 1
        if self.close_error is not None:
            raise self.close_error


def _ai_config() -> AIConfig:
    """Build the AI config every run in this module uses.

    Returns:
        AIConfig: A minimal enabled config on the API transport.
    """
    return AIConfig(enabled=True, review=True, transport=AITransport.API)


def _context() -> ReviewContext:
    """Build a one-file review context.

    Returns:
        ReviewContext: The context under review.
    """
    return ReviewContext(
        base_ref="base",
        head_ref="head",
        changed_files=[
            ChangedFile(
                path="src/app.py",
                status=ChangedFileStatus.MODIFIED,
                additions=1,
                deletions=0,
            ),
        ],
        unified_diff=_DIFF,
    )


def _options(*, provider: SpyProvider, **overrides: Any) -> ReviewSessionOptions:
    """Build session options around a spy provider.

    Args:
        provider: The spy the run is handed.
        **overrides: Extra option fields to set.

    Returns:
        ReviewSessionOptions: Options for one run.
    """
    return ReviewSessionOptions(
        provider=provider,  # type: ignore[arg-type]
        ai_config=_ai_config(),
        checklist_items=[],
        checklist_text="",
        classifications=[],
        **overrides,
    )


def _review_payload() -> str:
    """Serialize a clean built-in review response.

    Returns:
        str: JSON text with no findings.
    """
    return json.dumps({"summary": "ok", "checklist": [], "findings": []})


def _response(*, content: str) -> AIResponse:
    """Wrap response content in a provider response.

    Args:
        content: Response body.

    Returns:
        AIResponse: The wrapped response.
    """
    return AIResponse(
        content=content,
        model=_MODEL,
        input_tokens=100,
        output_tokens=50,
        cost_estimate=0.01,
        provider="anthropic",
    )


def _agent(*, tmp_path: Path, model: str) -> CustomAgentSpec:
    """Parse a custom agent that overrides the run's model.

    Args:
        tmp_path: Directory the agent file nominally lives in.
        model: Model the agent declares.

    Returns:
        CustomAgentSpec: The parsed agent.
    """
    return parse_custom_agent(
        path=tmp_path / f"{model}.md",
        text=_AGENT_TEXT.format(model=model),
    )


def test_a_completed_run_closes_its_provider_exactly_once() -> None:
    """The happy path closes the provider the adapter handed the run."""
    provider = SpyProvider()

    with patch(
        "lintro.ai.review.provider_call.call_ai",
        return_value=_response(content=_review_payload()),
    ):
        result = run_review(_context(), options=_options(provider=provider))

    assert_that(result.findings).is_empty()
    assert_that(provider.aclose_calls).is_equal_to(1)


def test_a_provider_failure_still_closes_the_provider_once() -> None:
    """A run that dies on a provider error does not leak its client."""
    provider = SpyProvider()

    with (
        patch(
            "lintro.ai.review.provider_call.call_ai",
            side_effect=AIProviderError("credit balance too low"),
        ),
        pytest.raises(ReviewExecutionError),
    ):
        run_review(_context(), options=_options(provider=provider))

    assert_that(provider.aclose_calls).is_equal_to(1)


def test_a_cancelled_run_closes_the_provider_once() -> None:
    """Cancellation mid-chunk closes the provider on the way out.

    ``asyncio.CancelledError`` is not an ``Exception``, so a ``try/except
    Exception`` teardown would miss it entirely; only the session's ``async
    with`` covers this path.
    """
    provider = SpyProvider()

    async def _cancel_mid_chunk(**_kwargs: Any) -> AIResponse:
        """Cancel the run in place of returning a provider response."""
        raise asyncio.CancelledError

    async def _run() -> None:
        """Drive one review run whose provider call cancels it."""
        with patch(
            "lintro.ai.review.provider_call.call_ai",
            side_effect=_cancel_mid_chunk,
        ):
            await run_review_async(_context(), options=_options(provider=provider))

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(_run())

    assert_that(provider.aclose_calls).is_equal_to(1)


def test_a_timeout_stop_closes_the_provider_once() -> None:
    """A graceful timeout stop is still the end of the provider's life.

    The run returns a partial result rather than raising, which is exactly the
    exit path a ``try/except`` teardown would forget.
    """
    provider = SpyProvider()

    with patch(
        "lintro.ai.review.provider_call.call_ai",
        side_effect=TimeoutError("request timed out"),
    ):
        result = run_review(_context(), options=_options(provider=provider))

    assert_that(result.metadata.stopped_reason).contains("timeout")
    assert_that(provider.aclose_calls).is_equal_to(1)


def test_custom_agent_model_overrides_are_closed_with_the_run(
    tmp_path: Path,
) -> None:
    """The 1+N providers of a run are all closed, and each exactly once.

    Two agents declaring two different models add two providers to the
    session's cache; a third agent reusing the first model shares its provider
    and must not cause a second close of it.
    """
    provider = SpyProvider()
    overrides = {
        "gpt-5": SpyProvider(name="openai", model_name="gpt-5"),
        "gpt-5-mini": SpyProvider(name="openai", model_name="gpt-5-mini"),
    }
    agents = (
        _agent(tmp_path=tmp_path, model="gpt-5"),
        _agent(tmp_path=tmp_path, model="gpt-5-mini"),
        _agent(tmp_path=tmp_path, model="gpt-5"),
    )

    def _build(config: Any, *, workspace_root: Any = None) -> SpyProvider:
        """Return the spy standing in for an overridden model.

        Args:
            config: The agent's AI config, carrying the overridden model.
            workspace_root: Ignored.

        Returns:
            SpyProvider: The spy for that model.
        """
        return overrides[str(config.model)]

    with (
        patch(
            "lintro.ai.review.provider_call.call_ai",
            return_value=_response(content=_review_payload()),
        ),
        patch(
            "lintro.ai.review.custom_agent_runner.call_ai",
            return_value=_response(content=json.dumps({"findings": []})),
        ),
        patch("lintro.ai.providers.get_provider", side_effect=_build),
    ):
        run_review(
            _context(),
            options=_options(provider=provider, custom_agents=agents),
        )

    assert_that(provider.aclose_calls).is_equal_to(1)
    assert_that(overrides["gpt-5"].aclose_calls).is_equal_to(1)
    assert_that(overrides["gpt-5-mini"].aclose_calls).is_equal_to(1)


def test_closing_a_session_twice_closes_each_provider_once() -> None:
    """``aclose`` is idempotent, so a belt-and-braces caller cannot double."""
    provider = SpyProvider()
    cached = SpyProvider(name="openai", model_name="gpt-5")

    async def _run() -> None:
        """Enter a session that is also closed by hand inside the block."""
        async with ReviewSession(provider=provider) as session:  # type: ignore[arg-type]
            session.provider_cache["gpt-5"] = cached  # type: ignore[assignment]
            await session.aclose()

    asyncio.run(_run())

    assert_that(provider.aclose_calls).is_equal_to(1)
    assert_that(cached.aclose_calls).is_equal_to(1)


def test_a_provider_aliased_into_the_cache_is_closed_once() -> None:
    """An override that resolved back to the run's provider is not double-closed."""
    provider = SpyProvider()

    async def _run() -> None:
        """Close a session whose cache aliases the primary provider."""
        async with ReviewSession(provider=provider) as session:  # type: ignore[arg-type]
            session.provider_cache["same"] = provider  # type: ignore[assignment]

    asyncio.run(_run())

    assert_that(provider.aclose_calls).is_equal_to(1)


def test_one_failing_teardown_never_orphans_the_others() -> None:
    """Every provider is closed even when the first one's teardown raises."""
    provider = SpyProvider(close_error=RuntimeError("pool already gone"))
    cached = SpyProvider(name="openai", model_name="gpt-5")

    async def _run() -> None:
        """Close a session whose primary provider fails to close."""
        async with ReviewSession(provider=provider) as session:  # type: ignore[arg-type]
            session.provider_cache["gpt-5"] = cached  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="pool already gone"):
        asyncio.run(_run())

    assert_that(provider.aclose_calls).is_equal_to(1)
    assert_that(cached.aclose_calls).is_equal_to(1)


def test_a_failing_teardown_never_masks_the_run_s_own_error() -> None:
    """The run's exception survives a provider that also fails to close."""
    provider = SpyProvider(close_error=RuntimeError("pool already gone"))

    async def _run() -> None:
        """Fail inside an entered session whose teardown also fails.

        Raises:
            ValueError: The run's own failure, which must be the one seen.
        """
        async with ReviewSession(provider=provider):  # type: ignore[arg-type]
            raise ValueError("the review itself failed")

    with pytest.raises(ValueError, match="the review itself failed"):
        asyncio.run(_run())

    assert_that(provider.aclose_calls).is_equal_to(1)


def test_the_session_module_is_the_only_caller_of_aclose() -> None:
    """Acceptance criterion 1: one call site, held as a test.

    ``lintro/ai/providers/`` is excluded because it *defines* the API (#1885)
    -- a subclass calling ``super().aclose()`` is the implementation, not a
    call site. Everywhere else, a call to ``aclose()`` is a second lifetime
    owner, which is exactly what this phase removed.
    """
    package = Path(__file__).resolve().parents[4] / "lintro"
    provider_api = package / "ai" / "providers"
    owner = package / "ai" / "review" / "session.py"

    callers: dict[str, list[int]] = {}
    for path in sorted(package.rglob("*.py")):
        if path == owner or provider_api in path.parents:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        lines = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "aclose"
        ]
        if lines:
            callers[path.relative_to(package.parent).as_posix()] = lines

    assert_that(callers).is_empty()
