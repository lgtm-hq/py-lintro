"""Cursor AI provider implementation.

``transport: cli`` (the default) invokes the ``agent`` CLI, Cursor's headless
agent; it authenticates with ``CURSOR_API_KEY`` or ``agent login``.
``transport: api`` (#3253) runs one inference-only prompt through Cursor's
first-party ``cursor-sdk`` (``lintro[cursor]``) and needs ``CURSOR_API_KEY``;
see :mod:`lintro.ai.providers.cursor.sdk_transport`. The Cloud Agents
``CreateAgent`` HTTP API is not used: it is an orchestration API, not an
inference endpoint, and it returned internal errors where the CLI worked.

Imported on demand by
:meth:`lintro.ai.providers.cursor.plugin.CursorPlugin.build`; importing
:mod:`lintro.ai.providers.cursor` alone does not pull this module.
"""

from __future__ import annotations

import importlib.util
import json
import os
from dataclasses import replace
from typing import Any

from loguru import logger

from lintro.ai.cost import estimate_cost_with_floor
from lintro.ai.enums import AITransport
from lintro.ai.exceptions import (
    AIAuthenticationError,
    AINotAvailableError,
    AIProviderError,
)
from lintro.ai.json_response import CliSchemaRequest
from lintro.ai.liveness import LIVENESS_TIMEOUT, LivenessResult
from lintro.ai.provider_enum import AIProvider
from lintro.ai.providers._api_common import finish_api_completion
from lintro.ai.providers._cli_envelope import decode_envelope_or_prose
from lintro.ai.providers.base import (
    AIResponse,
    BaseAIProvider,
    ProviderCapabilities,
)
from lintro.ai.providers.cli_contracts import cli_contract_for
from lintro.ai.providers.cli_transport import CliTransport, OptionalArg
from lintro.ai.providers.constants import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_PER_CALL_MAX_TOKENS,
    DEFAULT_TIMEOUT,
)
from lintro.ai.providers.cursor.metadata import CURSOR_CLI_BINARY, CURSOR_METADATA
from lintro.ai.providers.cursor.sdk_transport import (
    CURSOR_SDK_MODULE,
    CursorSdkBridge,
    run_prompt,
)
from lintro.ai.raw_response import CLI_ENVELOPE_STAGE, describe_raw_response
from lintro.ai.token_budget import estimate_tokens
from lintro.ai.transcript import TranscriptDirection, log_transcript_event

CURSOR_MIN_TIMEOUT = 600.0

_AGENT_BIN = CURSOR_CLI_BINARY
DEFAULT_MODEL = CURSOR_METADATA.default_model
DEFAULT_API_KEY_ENV = CURSOR_METADATA.default_api_key_env

# Detected without importing: importing ``cursor_sdk`` is deferred to the first
# API-transport call, so neither module import nor construction loads it.
_has_cursor_sdk = importlib.util.find_spec(CURSOR_SDK_MODULE) is not None


def _find_agent() -> str | None:
    """Return the full path to the ``agent`` binary, or None."""
    return CliTransport.find_binary(_AGENT_BIN)


class _CursorCliTransport(CliTransport):
    """Cursor ``agent --print`` subprocess transport."""

    def parse_stdout(
        self,
        stdout: str,
        *,
        model: str,
    ) -> tuple[AIResponse, str | None]:
        """Parse the JSON envelope from ``agent --output-format json``.

        Args:
            stdout: Raw CLI stdout.
            model: The model the call was invoked with; stamped on the
                response (#2532).

        Returns:
            The response and the session id the envelope reported.

        Raises:
            AIProviderError: When the envelope reports an error, or cannot be
                decoded (:class:`CliEnvelopeError`, #2513).
        """
        decoded = decode_envelope_or_prose(stdout, provider="Cursor agent")
        if isinstance(decoded, str):
            return (
                AIResponse(
                    content=decoded,
                    model=model,
                    provider=AIProvider.CURSOR,
                ),
                None,
            )

        if decoded.is_error:
            cause = decoded.result or describe_raw_response(
                provider="Cursor agent",
                stage=CLI_ENVELOPE_STAGE,
                raw=stdout,
            )
            raise AIProviderError(f"Cursor CLI reported error: {cause}")

        content = decoded.result
        if not content:
            logger.warning(
                f"Cursor CLI returned empty result. "
                f"Full response: {json.dumps(decoded.raw)[:1000]}",
            )

        return (
            AIResponse(
                # The agent CLI may prefix JSON with prose; the extracted span
                # replaces the content only when it parses, so a plain-text
                # answer with incidental braces stays intact.
                content=self.substitute_parsed_json(content),
                model=model,
                input_tokens=decoded.input_tokens,
                output_tokens=decoded.output_tokens,
                cost_estimate=0.0,
                provider=AIProvider.CURSOR,
            ),
            decoded.session_id,
        )

    def __init__(
        self,
        *,
        binary_path: str,
        model: str,
    ) -> None:
        super().__init__(
            binary_path=binary_path,
            binary_name=cli_contract_for(AIProvider.CURSOR).display_name,
            install_hint="Install with: curl https://cursor.com/install -fsS | bash",
            api_key_env=DEFAULT_API_KEY_ENV,
            contract=cli_contract_for(AIProvider.CURSOR),
            provider_name=AIProvider.CURSOR.value,
        )
        self._model = model


class CursorProvider(BaseAIProvider):
    """Cursor provider via the ``agent`` CLI or the ``cursor-sdk`` API."""

    def __init__(
        self,
        *,
        model: str | None = None,
        api_key_env: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        base_url: str | None = None,
        transport: AITransport = AITransport.CLI,
        cursor_trust_workspace: bool,
    ) -> None:
        """Initialize the Cursor provider.

        Args:
            model: Optional model override (defaults to provider config).
            api_key_env: Environment variable name for the API key.
            max_tokens: Default max tokens for provider configuration.
            base_url: Must be unset: Cursor has no endpoint override, so a
                configured ``ai.api_base_url`` is rejected under API transport
                and ignored under CLI.
            transport: ``cli`` for the ``agent`` binary or ``api`` for the
                ``cursor-sdk`` package.
            cursor_trust_workspace: Required. When True, ``--trust`` is
                passed to the ``agent`` CLI, granting Cursor workspace trust;
                when False, the flag is omitted and the ``agent`` CLI keeps
                its interactive trust prompt. This constructor carries no
                default on purpose:
                ``ai.providers.cursor.trust_workspace`` is the single default
                site (``True``, because choosing ``provider: cursor`` is the
                consent), and :func:`lintro.ai.providers.get_provider` always
                forwards the resolved block value. See "Cursor workspace trust" in
                ``docs/ai-features.md`` for the residual injection risk.

        Raises:
            AINotAvailableError: When the transport is not one Cursor serves,
                when API transport is given ``base_url`` or lacks the SDK, or
                when CLI transport cannot find the ``agent`` binary.
        """
        self._trust_workspace = cursor_trust_workspace
        self._session_id: str | None = None
        self._durable_repo_root: str | None = None
        self._cli: _CursorCliTransport | None = None
        self._sdk_bridge: CursorSdkBridge | None = None
        if not CURSOR_METADATA.supports(transport):
            msg = f"cursor provider does not support transport: {transport}"
            raise AINotAvailableError(msg)
        if transport == AITransport.API:
            if base_url:
                raise AINotAvailableError(
                    "cursor API transport does not accept ai.api_base_url",
                )
            # Neither the SDK nor the key is touched here: the bridge starts
            # and the key is read on the first call.
            super().__init__(
                provider_name=AIProvider.CURSOR,
                has_sdk=_has_cursor_sdk,
                sdk_package="cursor-sdk",
                default_model=DEFAULT_MODEL,
                default_api_key_env=DEFAULT_API_KEY_ENV,
                model=model,
                api_key_env=api_key_env,
                max_tokens=max_tokens,
                transport=transport,
                sdk_extra=CURSOR_METADATA.sdk_extra,
            )
            self._sdk_bridge = CursorSdkBridge()
            return

        agent_path = _find_agent()
        if not agent_path:
            raise AINotAvailableError(
                "Cursor provider requires the 'agent' CLI. "
                "Install with: curl https://cursor.com/install -fsS | bash",
            )

        super().__init__(
            provider_name=AIProvider.CURSOR,
            has_sdk=True,
            sdk_package="agent CLI",
            default_model=DEFAULT_MODEL,
            default_api_key_env=DEFAULT_API_KEY_ENV,
            model=model,
            api_key_env=api_key_env,
            max_tokens=max_tokens,
            base_url=base_url,
            transport=transport,
        )
        self._cli = _CursorCliTransport(
            binary_path=agent_path,
            model=self._model,
        )

    def _create_client(self, *, api_key: str) -> Any:
        """No persistent client -- each call spawns a subprocess."""
        del api_key
        return None

    def _get_client(self) -> Any:
        """Override: no shared client; the CLI or the SDK bridge owns auth."""
        return None

    def is_available(self) -> bool:
        """Check the configured transport's presence requirements.

        Returns:
            Under CLI, True when the ``agent`` binary is discoverable; under
            API, True when the SDK is installed and the key variable is set.
        """
        if self._sdk_bridge is not None:
            return super().is_available()
        return _find_agent() is not None

    def _cli_transport(self) -> _CursorCliTransport | None:
        """Return the ``agent`` CLI transport, when one was constructed.

        Returns:
            The CLI transport, or ``None`` under API transport.
        """
        return self._cli

    @property
    def capabilities(self) -> ProviderCapabilities:
        """Declare Cursor capabilities for the configured transport.

        Returns:
            The ``agent`` CLI resumes sessions; the SDK path is one stateless
            prompt per call. Neither exposes native structured output or token
            streaming to lintro.
        """
        return ProviderCapabilities(
            supports_sessions=self._cli is not None,
            supports_structured_output=False,
            supports_streaming=False,
        )

    def begin_durable_session(self, *, repo_root: str) -> None:
        """Start a reusable CLI session for single-chunk reviews.

        A no-op under API transport, which has no session to resume.

        Args:
            repo_root: Absolute path to the git repository under review.
        """
        self.end_durable_session()
        if self._cli is not None:
            self._durable_repo_root = repo_root

    async def check_liveness(
        self,
        *,
        timeout: float = LIVENESS_TIMEOUT,
    ) -> LivenessResult:
        """Probe the credential, then stop any bridge the probe started.

        ``lintro doctor`` runs the probe on a short-lived loop and never closes
        the provider, so under API transport the SDK bridge it starts is
        stopped here rather than left to the exit-time fallback.

        Args:
            timeout: Seconds allowed for the probe.

        Returns:
            The classified liveness result.
        """
        try:
            return await super().check_liveness(timeout=timeout)
        finally:
            if self._sdk_bridge is not None:
                await self._sdk_bridge.aclose()

    async def aclose(self) -> None:
        """Stop the SDK bridge and remove its workdir. Idempotent."""
        if self._sdk_bridge is not None:
            await self._sdk_bridge.aclose()
        await super().aclose()

    def end_durable_session(self) -> None:
        """Clear any active durable CLI session state."""
        self._session_id = None
        self._durable_repo_root = None

    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = DEFAULT_PER_CALL_MAX_TOKENS,
        timeout: float = DEFAULT_TIMEOUT,
        repo_root: str | None = None,
        use_one_shot: bool = False,
        model: str | None = None,
        cli_schema: CliSchemaRequest | None = None,
    ) -> AIResponse:
        """Run a completion via ``agent --print`` or the Cursor SDK.

        Args:
            prompt: User prompt text.
            system: Optional system prompt prepended to the user prompt.
            max_tokens: Per-call token cap. The effective value is the
                lower of this and the provider-level cap; the ``agent`` CLI
                enforces no hard limit, so it is stated to the model in the
                prompt instead.
            timeout: Timeout in seconds (minimum 600 for the ``agent`` CLI,
                180 for the SDK).
            repo_root: Git repository root for ``--workspace``; never used as
                the SDK agent's working directory.
            use_one_shot: When True, do not resume an existing CLI session.
            model: Optional per-call model override.
            cli_schema: Unused; accepted for provider API parity.

        Returns:
            Parsed model response with usage metadata.

        Raises:
            AINotAvailableError: When no transport was constructed.
        """
        effective_model = model or self._model
        effective_max = min(max_tokens, self._max_tokens)
        del cli_schema
        combined_prompt = prompt
        if system:
            combined_prompt = f"{system}\n\n---\n\n{prompt}"
        combined_prompt = (
            f"{combined_prompt}\n\n[Respond in at most {effective_max} tokens.]"
        )
        if self._sdk_bridge is not None:
            return await self._complete_api(
                combined_prompt,
                bridge=self._sdk_bridge,
                model=effective_model,
                max_tokens=effective_max,
                timeout=timeout,
            )
        if self._cli is None:
            raise AINotAvailableError("Cursor CLI transport is not initialized")

        effective_root = repo_root or self._durable_repo_root or os.getcwd()
        resume_session = (
            not use_one_shot
            and self._session_id is not None
            and self._durable_repo_root is not None
        )

        cmd = [
            self._cli._binary_path,
            "--print",
            "--output-format",
            "json",
            "--mode",
            "ask",
            "--model",
            effective_model,
            "--workspace",
            effective_root,
        ]

        candidates: list[OptionalArg] = []
        if self._trust_workspace:
            candidates.append(OptionalArg(flag="--trust"))
        if resume_session and self._session_id is not None:
            candidates.append(
                OptionalArg(flag="--resume", values=(self._session_id,)),
            )

        optional_args = await self._cli.apply_optional_args(cmd, candidates)

        logger.debug(
            f"Cursor CLI request: model={effective_model}, "
            f"max_tokens={effective_max}, "
            f"workspace={effective_root}, resume={resume_session}, "
            f"prompt_len={len(combined_prompt)}",
        )

        effective_timeout = max(timeout, CURSOR_MIN_TIMEOUT)
        result = await self._cli.run_guarded(
            cmd,
            optional_args=optional_args,
            input_text=combined_prompt,
            timeout=effective_timeout,
        )
        self._cli.check_exit_code(
            result,
            auth_patterns=("authentication required", "login"),
            auth_hint="Run 'agent login' or set CURSOR_API_KEY.",
        )
        # The response names the model the call ran as (#2867, #2532).
        response, session_id = self._cli.parse_stdout(
            result.stdout,
            model=effective_model,
        )
        response = self._estimate_usage(
            prompt=combined_prompt,
            model=effective_model,
            response=response,
        )

        if not use_one_shot and self._durable_repo_root is not None and session_id:
            self._session_id = session_id
        return response

    async def _complete_api(
        self,
        prompt: str,
        *,
        bridge: CursorSdkBridge,
        model: str,
        max_tokens: int,
        timeout: float,
    ) -> AIResponse:
        """Run one inference-only prompt through the Cursor SDK.

        Args:
            prompt: The combined prompt, output cap already stated in it.
            bridge: This provider's bridge holder.
            model: Effective model identifier.
            max_tokens: Effective output cap, recorded in the transcript.
            timeout: Requested timeout; the SDK path floors it at 180 s.

        Returns:
            The response, with the SDK's token usage or a local estimate.

        Raises:
            AIAuthenticationError: When the key variable is unset.
        """
        api_key = os.environ.get(self._api_key_env) or ""
        if not api_key:
            raise AIAuthenticationError(
                f"No API key found. Set the {self._api_key_env} environment variable.",
            )
        log_transcript_event(
            provider=AIProvider.CURSOR.value,
            transport=AITransport.API.value,
            direction=TranscriptDirection.REQUEST,
            payload={
                "model": model,
                "max_tokens": max_tokens,
                "prompt": prompt,
                "timeout": timeout,
            },
        )
        run = await run_prompt(
            bridge=bridge,
            message=prompt,
            model=model,
            api_key=api_key,
            timeout=timeout,
        )
        # No usage on the run is recorded as an estimate, like the CLI path.
        input_tokens = run.input_tokens or estimate_tokens(prompt)
        output_tokens = run.output_tokens or estimate_tokens(run.content)
        return finish_api_completion(
            provider=AIProvider.CURSOR,
            model=model,
            content=run.content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=estimate_cost_with_floor(model, input_tokens, output_tokens),
            served_model=run.served_model,
        )

    @staticmethod
    def _estimate_usage(
        *,
        prompt: str,
        model: str,
        response: AIResponse,
    ) -> AIResponse:
        """Fill in local token estimates and a budget-safe cost estimate.

        The Cursor ``agent`` CLI does not reliably expose token usage, and
        its subscription pricing is zero in the model registry, so every
        call would otherwise record zero cost and bypass the session budget.
        This estimates tokens from the prompt and response text (when the CLI
        omits them) and prices them with a non-zero floor so
        :class:`~lintro.ai.budget.CostBudget` accrues a meaningful figure and
        ``ai.max_cost_usd`` acts as a real safety cap.

        Args:
            prompt: The combined prompt sent to the CLI (system + user).
            model: The effective model identifier used for the call.
            response: The parsed provider response to augment.

        Returns:
            AIResponse: A copy with non-zero token counts and cost estimate.
        """
        input_tokens = response.input_tokens or estimate_tokens(prompt)
        output_tokens = response.output_tokens or estimate_tokens(response.content)
        cost = estimate_cost_with_floor(model, input_tokens, output_tokens)
        return replace(
            response,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_estimate=cost,
        )

    @staticmethod
    def _extract_json_object(text: str) -> str:
        """Extract the outermost JSON object ``{...}`` from text."""
        return CliTransport.extract_json_object(text)
