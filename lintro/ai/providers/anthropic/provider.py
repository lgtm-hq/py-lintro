"""Anthropic AI provider implementation.

Uses the Anthropic Python SDK for ``transport: api`` and the ``claude`` CLI
for ``transport: cli``.

Imported on demand by
:meth:`lintro.ai.providers.anthropic.plugin.AnthropicPlugin.build`; importing
:mod:`lintro.ai.providers.anthropic` alone does not pull the vendor SDK.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from loguru import logger

from lintro.ai.cli_bounds import current_cli_call_options
from lintro.ai.cost import estimate_cost
from lintro.ai.enums import AITransport, CliBareMode
from lintro.ai.exceptions import (
    AIAuthenticationError,
    AINotAvailableError,
    AIProviderError,
    AIRateLimitError,
    AITurnLimitError,
)
from lintro.ai.json_response import CliSchemaRequest
from lintro.ai.provider_enum import AIProvider
from lintro.ai.providers._api_common import (
    ApiStreamingProvider,
    finish_api_completion,
)
from lintro.ai.providers.anthropic.metadata import (
    ANTHROPIC_CLI_BINARY,
    ANTHROPIC_METADATA,
)
from lintro.ai.providers.base import (
    AIResponse,
    AsyncAIStreamResult,
    ProviderCapabilities,
)
from lintro.ai.providers.claude_auth import should_send_bare
from lintro.ai.providers.cli_contracts import cli_contract_for, flag_named_in
from lintro.ai.providers.cli_transport import CliTransport, OptionalArg
from lintro.ai.providers.constants import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_PER_CALL_MAX_TOKENS,
    DEFAULT_TIMEOUT,
)
from lintro.ai.rate_limit import retry_after_from_exception
from lintro.ai.raw_response import (
    CLI_ENVELOPE_STAGE,
    describe_raw_response,
    recover_prose_envelope,
)
from lintro.ai.transcript import TranscriptDirection, log_transcript_event

_has_anthropic = False
try:
    import anthropic

    _has_anthropic = True
except ImportError:
    pass

DEFAULT_MODEL = ANTHROPIC_METADATA.default_model
DEFAULT_API_KEY_ENV = ANTHROPIC_METADATA.default_api_key_env
_CLAUDE_BIN = ANTHROPIC_CLI_BINARY


def _find_claude() -> str | None:
    """Return the full path to the ``claude`` binary, or None."""
    return CliTransport.find_binary(_CLAUDE_BIN)


def _auth_hint(*, bare: bool) -> str:
    """Return guidance matching the auth mode the failed call actually used.

    A subscription user told to "run /login" after a bare invocation has
    already done so; the real remedy differs per mode, so the hint must too.

    Args:
        bare: Whether ``--bare`` was sent on the failing invocation.

    Returns:
        Actionable guidance for the mode that failed.
    """
    if bare:
        return (
            "Set ANTHROPIC_API_KEY or configure apiKeyHelper "
            "(--bare mode does not use OAuth login), or set "
            "ai.cli_bare: never / LINTRO_CLI_BARE=never to use the CLI's "
            "own login session."
        )
    return (
        "Log in with 'claude /login', or set ANTHROPIC_API_KEY to "
        "authenticate the CLI with an API key."
    )


#: The built-in tools a review call may use: read-only under
#: ``--permission-mode dontAsk`` (#2685).
_READ_ONLY_TOOLS = "Read,Grep,Glob"

#: The envelope subtype the CLI reports when ``--max-turns`` stopped the loop.
_MAX_TURNS_SUBTYPE = "error_max_turns"


def _raise_if_turn_limited(*, stdout: str, max_turns: int | None) -> None:
    """Raise :class:`AITurnLimitError` when *stdout* carries a turn-limit envelope.

    Args:
        stdout: Raw CLI stdout; the envelope is its last non-empty line.
        max_turns: The limit that was sent, when any.

    Raises:
        AITurnLimitError: When the envelope says the loop stopped at the limit.
    """
    lines = [line for line in stdout.strip().splitlines() if line.strip()]
    if not lines:
        return
    try:
        data = json.loads(lines[-1])
    except ValueError:
        return
    if isinstance(data, dict) and _hit_turn_limit(data=data, max_turns=max_turns):
        raw_usage = data.get("usage")
        usage: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
        cost = data.get("total_cost_usd")
        raise AITurnLimitError(
            "Claude CLI stopped at the per-call turn limit"
            f"{f' ({max_turns} turns)' if max_turns is not None else ''} "
            "before answering (#2685).",
            input_tokens=_usage_int(usage.get("input_tokens")),
            output_tokens=_usage_int(usage.get("output_tokens")),
            cost_estimate=float(cost) if isinstance(cost, (int, float)) else 0.0,
            turns=_usage_int(data.get("num_turns")) or None,
        )


def _usage_int(value: object) -> int:
    """Return *value* as a non-negative int, or 0 when it is not one.

    Args:
        value: A usage counter from the envelope.

    Returns:
        The counter, or 0.
    """
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
        else 0
    )


def _hit_turn_limit(*, data: Mapping[str, Any], max_turns: int | None) -> bool:
    """Return whether the envelope says the per-call turn limit stopped the run.

    Keys on the dedicated subtype, with a fallback on an error envelope whose
    reported turn count reached the limit that was sent, so a binary that
    words the subtype differently is still recognised (#2685).

    Args:
        data: Decoded ``claude --output-format json`` envelope.
        max_turns: The limit that was sent, when any.

    Returns:
        True when the loop stopped at the limit rather than by answering.
    """
    if data.get("subtype") == _MAX_TURNS_SUBTYPE:
        return True
    if max_turns is None or not data.get("is_error"):
        return False
    # An envelope that names an API failure is that failure, not a turn limit,
    # however many turns it reports.
    if data.get("api_error_status") is not None or data.get("terminal_reason"):
        return False
    turns = data.get("num_turns")
    return isinstance(turns, int) and not isinstance(turns, bool) and turns >= max_turns


class _AnthropicCliTransport(CliTransport):
    """Anthropic ``claude -p`` subprocess transport."""

    def __init__(
        self,
        *,
        binary_path: str,
        model: str,
    ) -> None:
        super().__init__(
            binary_path=binary_path,
            binary_name=cli_contract_for(AIProvider.ANTHROPIC).display_name,
            install_hint="Install Claude Code: https://code.claude.com/docs/en/setup",
            api_key_env=DEFAULT_API_KEY_ENV,
            contract=cli_contract_for(AIProvider.ANTHROPIC),
            provider_name=AIProvider.ANTHROPIC.value,
        )
        self._model = model

    def parse_stdout(
        self,
        stdout: str,
        *,
        max_turns: int | None = None,
    ) -> tuple[AIResponse, str | None]:
        """Parse JSON envelope from ``claude --output-format json``."""
        try:
            data = json.loads(stdout.strip())
        except json.JSONDecodeError as exc:
            recovered = recover_prose_envelope(
                provider="Claude",
                stdout=stdout,
                reason=str(exc),
            )
            if recovered is None:
                evidence = describe_raw_response(
                    provider="Claude",
                    stage=CLI_ENVELOPE_STAGE,
                    raw=stdout,
                )
                raise AIProviderError(
                    f"Claude CLI returned invalid JSON: {exc}\n{evidence}",
                ) from exc
            return (
                AIResponse(
                    content=recovered,
                    model=self._model,
                    provider=AIProvider.ANTHROPIC,
                ),
                None,
            )

        if _hit_turn_limit(data=data, max_turns=max_turns):
            raise AITurnLimitError(
                "Claude CLI stopped at the per-call turn limit"
                f"{f' ({max_turns} turns)' if max_turns is not None else ''} "
                "before answering (#2685).",
            )
        if data.get("is_error") or data.get("subtype") == "error":
            cause = data.get("result") or describe_raw_response(
                provider="Claude",
                stage=CLI_ENVELOPE_STAGE,
                raw=stdout,
            )
            # Keep the envelope's API-error fields next to the prose: they are
            # what tells a rejected request (400, 429) apart from an answer
            # that overran the output ceiling, and the output-exhaustion
            # classifier reads them from the message (#2695).
            markers = {
                key: data[key]
                for key in ("terminal_reason", "api_error_status")
                if data.get(key) is not None
            }
            suffix = (
                f" ({json.dumps(markers, separators=(',', ':'))})" if markers else ""
            )
            raise AIProviderError(f"Claude CLI reported error: {cause}{suffix}")

        content = data.get("result", "")
        structured = data.get("structured_output")
        if structured is not None:
            content = json.dumps(structured)
        elif isinstance(content, dict):
            content = json.dumps(content)
        elif not isinstance(content, str):
            content = str(content)

        usage = data.get("usage", {})
        input_tokens = int(
            usage.get("input_tokens", usage.get("inputTokens", 0)),
        )
        output_tokens = int(
            usage.get("output_tokens", usage.get("outputTokens", 0)),
        )
        cost = data.get("total_cost_usd")
        if cost is None:
            cost = estimate_cost(self._model, input_tokens, output_tokens)
        else:
            cost = float(cost)

        session_id = data.get("session_id")
        if isinstance(session_id, str) and session_id.strip():
            session_id = session_id.strip()
        else:
            session_id = None

        # The CLI envelope reports how many agent turns the call took; it is
        # carried onto the per-chunk timings (lintro-ops #37). A bool is an
        # int in Python and never a turn count, so it is rejected explicitly.
        raw_turns = data.get("num_turns")
        turns = (
            raw_turns
            if isinstance(raw_turns, int)
            and not isinstance(raw_turns, bool)
            and raw_turns >= 0
            else None
        )

        return (
            AIResponse(
                content=self.substitute_parsed_json(content),
                model=self._model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_estimate=cost,
                provider=AIProvider.ANTHROPIC,
                turns=turns,
            ),
            session_id,
        )


class AnthropicProvider(ApiStreamingProvider):
    """Anthropic Claude provider."""

    @staticmethod
    @contextmanager
    def _map_errors() -> Iterator[None]:
        """Map Anthropic SDK exceptions to AI exceptions.

        Safe to call only when the ``anthropic`` SDK is installed —
        the base class ``__init__`` raises ``AINotAvailableError``
        before any method can be called if the SDK is missing.
        """
        try:
            yield
        except anthropic.AuthenticationError as e:
            raise AIAuthenticationError(
                f"Anthropic authentication failed: {e}",
            ) from e
        except anthropic.RateLimitError as e:
            # Carry the server's own Retry-After so with_retry waits the
            # advertised window instead of guessing a backoff (#2506).
            raise AIRateLimitError(
                f"Anthropic rate limit exceeded: {e}",
                retry_after=retry_after_from_exception(e),
            ) from e
        except anthropic.AnthropicError as e:
            logger.debug(f"Anthropic API error: {e}")
            raise AIProviderError(
                f"Anthropic API error: {e}",
            ) from e

    def __init__(
        self,
        *,
        model: str | None = None,
        api_key_env: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        base_url: str | None = None,
        transport: AITransport = AITransport.API,
        cli_bare: CliBareMode = CliBareMode.AUTO,
    ) -> None:
        """Initialize the Anthropic provider.

        Args:
            model: Model identifier. Defaults to claude-sonnet-4-6.
            api_key_env: Environment variable for API key.
                Defaults to ANTHROPIC_API_KEY.
            max_tokens: Default max tokens for completions.
            base_url: Custom API base URL for Anthropic-compatible
                endpoints (proxies, self-hosted, etc.).
            transport: ``api`` for SDK or ``cli`` for ``claude -p``.
            cli_bare: Whether CLI transport passes ``--bare``. Defaults to
                auto-detection from the CLI's own API-key sources; see
                :mod:`lintro.ai.providers.claude_auth`.

        Raises:
            AINotAvailableError: When CLI transport is selected but the
                ``claude`` binary is not on PATH.
        """
        self._transport = transport
        self._cli_bare = cli_bare
        self._cli: _AnthropicCliTransport | None = None

        if transport == AITransport.CLI:
            claude_path = _find_claude()
            if not claude_path:
                raise AINotAvailableError(
                    "Anthropic CLI transport requires the 'claude' binary. "
                    "Install Claude Code: https://code.claude.com/docs/en/setup",
                )
            super().__init__(
                provider_name=AIProvider.ANTHROPIC,
                has_sdk=True,
                sdk_package="claude CLI",
                default_model=DEFAULT_MODEL,
                default_api_key_env=DEFAULT_API_KEY_ENV,
                model=model,
                api_key_env=api_key_env,
                max_tokens=max_tokens,
                base_url=base_url,
                transport=transport,
            )
            self._cli = _AnthropicCliTransport(
                binary_path=claude_path,
                model=self._model,
            )
            self._session_id: str | None = None
            self._session_lock = threading.Lock()
            return

        super().__init__(
            provider_name=AIProvider.ANTHROPIC,
            has_sdk=_has_anthropic,
            sdk_package="anthropic",
            default_model=DEFAULT_MODEL,
            default_api_key_env=DEFAULT_API_KEY_ENV,
            model=model,
            api_key_env=api_key_env,
            max_tokens=max_tokens,
            base_url=base_url,
            transport=transport,
        )

    def _create_client(self, *, api_key: str) -> Any:
        """Create the Anthropic SDK client.

        Args:
            api_key: The resolved API key.

        Returns:
            anthropic.AsyncAnthropic: The async API client.
        """
        kwargs: dict[str, Any] = {"api_key": api_key}
        if self._base_url:
            kwargs["base_url"] = self._base_url
        return anthropic.AsyncAnthropic(**kwargs)

    def is_available(self) -> bool:
        """Return True when the configured transport is usable."""
        if self._transport == AITransport.CLI:
            return _find_claude() is not None
        return super().is_available()

    def _cli_transport(self) -> _AnthropicCliTransport | None:
        """Return the ``claude`` CLI transport, when one was constructed.

        Returns:
            The CLI transport, or ``None`` under API transport.
        """
        return self._cli

    @property
    def capabilities(self) -> ProviderCapabilities:
        """Declare Anthropic capabilities for the configured transport.

        Returns:
            CLI transport resumes ``claude`` sessions and accepts a native JSON
            schema but streams only via the base fallback; API transport streams
            natively and has no server-side session to resume.
        """
        if self._transport == AITransport.CLI:
            return ProviderCapabilities(
                supports_sessions=True,
                supports_structured_output=True,
                supports_streaming=False,
            )
        return ProviderCapabilities(
            supports_sessions=False,
            supports_structured_output=False,
            supports_streaming=True,
        )

    def begin_durable_session(self, *, repo_root: str) -> None:
        """Start a fresh reusable ``claude`` session.

        Args:
            repo_root: Absolute path to the repository under review. Unused;
                the working directory is passed per call instead.
        """
        del repo_root
        self.end_durable_session()

    def end_durable_session(self) -> None:
        """Drop the resumable session id so the next call starts clean."""
        if self._transport != AITransport.CLI:
            return
        with self._session_lock:
            self._session_id = None

    async def aclose(self) -> None:
        """Close the Anthropic SDK client and any superseded loop-stale clients.

        Idempotent. CLI transport holds no poolable HTTP client; this still
        clears any API-transport client created earlier on this instance.
        """
        await super().aclose()

    async def _complete_cli(
        self,
        prompt: str,
        *,
        system: str | None,
        timeout: float,
        repo_root: str | None,
        use_one_shot: bool,
        model: str | None = None,
        cli_schema: CliSchemaRequest | None = None,
    ) -> AIResponse:
        if self._cli is None:
            raise AINotAvailableError("Claude CLI transport is not initialized")

        effective_model = model or self._model
        working_dir = repo_root or os.getcwd()
        # `--bare` disables the CLI's OAuth session login, so it may only be
        # sent when the binary can reach an API key. Forcing it locked every
        # subscription-authenticated user out of this transport (#1838).
        bare = should_send_bare(configured=self._cli_bare, cwd=working_dir)
        # Fail closed on the read-only bound (#2685): ``--tools`` is a
        # required contract flag, and a binary that cannot restrict its tool
        # surface is refused before any session starts rather than reviewing
        # prompt-injectable repository content with writable tools. This
        # reads the help text directly instead of ``supports_flag``, whose
        # optimistic answer on an unreadable ``--help`` is right for optional
        # flags and wrong for a security bound: no help, no session.
        help_text = await self._cli.help_text()
        if help_text is None or not flag_named_in(help_text.lower(), "--tools"):
            contract = self._cli.contract
            hint = contract.upgrade_hint if contract is not None else ""
            why = (
                "its --help could not be read"
                if help_text is None
                else "it does not offer --tools"
            )
            raise AINotAvailableError(
                f"Claude CLI cannot be restricted to read-only tools ({why}), "
                f"so no review session is started. {hint}",
            )
        # Prompt rides on stdin (#1967): a single argv element on Linux is
        # capped at MAX_ARG_STRLEN (128 KiB), so large review diffs must not
        # be passed as the ``-p``/``--print`` value.
        bounds = current_cli_call_options()
        cmd = [
            self._cli._binary_path,
            *(("--bare",) if bare else ()),
            "--print",
            "--output-format",
            "json",
            "--permission-mode",
            "dontAsk",
            "--tools",
            # A single-shot call (#2731) keeps the flag and empties the list:
            # the read-only bound is a required contract flag either way.
            "" if bounds is not None and bounds.tools_disabled else _READ_ONLY_TOOLS,
            "--model",
            effective_model,
        ]
        if system:
            cmd.extend(["--append-system-prompt", system])

        candidates: list[OptionalArg] = []
        if cli_schema is not None:
            cmd.extend(["--json-schema", json.dumps(cli_schema.schema)])
            if cli_schema.schema_name:
                candidates.append(
                    OptionalArg(
                        flag="--json-schema-name",
                        values=(cli_schema.schema_name,),
                    ),
                )
        with self._session_lock:
            resume_session_id = None if use_one_shot else self._session_id
        if resume_session_id is not None:
            candidates.append(
                OptionalArg(flag="--resume", values=(resume_session_id,)),
            )
        # Bound the agent per call (#2685). The read-only tool surface is a
        # security bound and fails closed: ``--tools`` is a required contract
        # flag, a binary that does not advertise it is refused above, and it
        # is sent on every call. ``--max-turns`` is a time bound: accepted by
        # claude 2.x but not listed by ``--help``, so it is sent regardless
        # and only the reactive unknown-option backstop drops it. ``call_ai``
        # sets the turn limit for every call kind; a caller that reaches
        # ``complete()`` without bounds is read-only but turn-unlimited.
        max_turns = bounds.max_turns if bounds is not None else None
        if max_turns is not None:
            candidates.append(
                OptionalArg(
                    flag="--max-turns",
                    values=(str(max_turns),),
                    gate_on_help=False,
                ),
            )

        optional_args = await self._cli.apply_optional_args(cmd, candidates)

        logger.debug(
            f"Claude CLI request: model={effective_model}, "
            f"resume={resume_session_id is not None}, "
            f"bare={bare}, "
            f"prompt_len={len(prompt)}",
        )

        result = await self._cli.run_guarded(
            cmd,
            optional_args=optional_args,
            input_text=prompt,
            timeout=timeout,
            cwd=working_dir,
        )
        # A turn-limited run exits non-zero with a well-formed envelope on
        # stdout. Recognise it before the exit-code mapping, whose auth
        # heuristic greps stderr for "login" and would otherwise misread the
        # CLI's own auth-source warning as an authentication failure (#2685).
        # The limit used for detection is the one the executed argv carried:
        # the backstop may have dropped ``--max-turns``, and an unbounded
        # error must not be read as a turn limit through the count fallback.
        executed = list(result.args) if isinstance(result.args, (list, tuple)) else []
        sent_limit = max_turns if "--max-turns" in executed else None
        _raise_if_turn_limited(stdout=result.stdout, max_turns=sent_limit)
        self._cli.check_exit_code(
            result,
            auth_patterns=("authentication", "login", "not logged in"),
            auth_hint=_auth_hint(bare=bare),
        )

        response, session_id = self._cli.parse_stdout(
            result.stdout,
            max_turns=sent_limit,
        )
        if not use_one_shot and session_id is not None:
            with self._session_lock:
                self._session_id = session_id
        return response

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
        """Generate a completion using Claude (API or CLI).

        Args:
            prompt: The user prompt.
            system: Optional system prompt.
            max_tokens: Maximum tokens to generate (API only).
            timeout: Request timeout in seconds.
            repo_root: Working directory for CLI transport.
            use_one_shot: When True, avoid resuming CLI sessions.
            model: Optional per-call model override.
            cli_schema: Optional native CLI JSON schema request.

        Returns:
            AIResponse: The model's response with usage metadata.
        """
        if self._transport == AITransport.CLI:
            del max_tokens
            return await self._complete_cli(
                prompt,
                system=system,
                timeout=timeout,
                repo_root=repo_root,
                use_one_shot=use_one_shot,
                model=model,
                cli_schema=cli_schema,
            )

        del repo_root, use_one_shot, cli_schema
        client = self._get_client()
        effective_model = model or self._model
        # Per-call cap: the lower of the caller's request and the
        # provider-level cap set at init time.
        effective_max = min(max_tokens, self._max_tokens)

        with self._map_errors():
            kwargs: dict[str, Any] = {
                "model": effective_model,
                "max_tokens": effective_max,
                "messages": [{"role": "user", "content": prompt}],
                "timeout": timeout,
            }
            if system:
                kwargs["system"] = system

            log_transcript_event(
                provider=AIProvider.ANTHROPIC.value,
                transport=AITransport.API.value,
                direction=TranscriptDirection.REQUEST,
                payload={
                    "model": effective_model,
                    "max_tokens": effective_max,
                    "system": system,
                    "messages": kwargs["messages"],
                    "timeout": timeout,
                },
            )

            response = await client.messages.create(**kwargs)

            content = ""
            for block in response.content:
                if hasattr(block, "text"):
                    content += block.text

            input_tokens = response.usage.input_tokens
            output_tokens = response.usage.output_tokens
            cost = estimate_cost(effective_model, input_tokens, output_tokens)

            return finish_api_completion(
                provider=AIProvider.ANTHROPIC,
                model=effective_model,
                content=content,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost=cost,
            )

    async def _stream_api(
        self,
        prompt: str,
        *,
        system: str | None,
        max_tokens: int,
        timeout: float,
        model: str | None,
    ) -> AsyncAIStreamResult:
        """Stream a completion from the Anthropic API token-by-token.

        Args:
            prompt: The user prompt.
            system: Optional system prompt.
            max_tokens: Maximum tokens to generate.
            timeout: Request timeout in seconds.
            model: Optional per-call model override.

        Returns:
            An AsyncAIStreamResult wrapping the token stream.
        """
        client = self._get_client()
        effective_max = min(max_tokens, self._max_tokens)
        effective_model = model or self._model

        kwargs: dict[str, Any] = {
            "model": effective_model,
            "max_tokens": effective_max,
            "messages": [{"role": "user", "content": prompt}],
            "timeout": timeout,
        }
        if system:
            kwargs["system"] = system

        logger.debug(
            f"Anthropic stream request: model={effective_model}, "
            f"max_tokens={effective_max}",
        )

        log_transcript_event(
            provider=AIProvider.ANTHROPIC.value,
            transport=AITransport.API.value,
            direction=TranscriptDirection.REQUEST,
            payload={
                "model": effective_model,
                "max_tokens": effective_max,
                "system": system,
                "messages": kwargs["messages"],
                "timeout": timeout,
                "stream": True,
            },
        )

        final_response: list[AIResponse] = []

        async def _generate() -> AsyncIterator[str]:
            """Yield tokens from the Anthropic stream and capture usage.

            Yields:
                str: Text deltas in arrival order.
            """
            with self._map_errors():
                async with client.messages.stream(**kwargs) as stream:
                    async for text in stream.text_stream:
                        yield text
                    final_message = await stream.get_final_message()

                input_tokens = final_message.usage.input_tokens
                output_tokens = final_message.usage.output_tokens
                cost = estimate_cost(effective_model, input_tokens, output_tokens)
                log_transcript_event(
                    provider=AIProvider.ANTHROPIC.value,
                    transport=AITransport.API.value,
                    direction=TranscriptDirection.RESPONSE,
                    payload={
                        "model": effective_model,
                        "stream": True,
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "cost_estimate": cost,
                    },
                )
                final_response.append(
                    AIResponse(
                        content="",
                        model=effective_model,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        cost_estimate=cost,
                        provider=AIProvider.ANTHROPIC,
                    ),
                )

        def _on_done() -> AIResponse:
            if not final_response:
                raise AIProviderError(
                    "Anthropic stream was not fully consumed",
                )
            return final_response[0]

        return AsyncAIStreamResult(
            _chunks=_generate(),
            _on_done=_on_done,
        )
