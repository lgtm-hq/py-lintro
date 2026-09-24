# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""Record and describe the raw HTTP exchange behind one smoke call (#2748).

lintro's providers hand back only the text a model wrote, which is all a review
needs and nothing a failed smoke can be diagnosed from. ``EmptyResponse`` on
the z.ai row reddened every weekly run for a month and the log could not say
whether the gateway sent an empty 200, a quota page, or a 200 whose only block
was the model's reasoning (the answer a thinking model gives when the token
cap runs out before it starts writing).

:func:`capture_http` watches the SDK's own ``httpx`` client for the length of
one call, so the exchange described is the one lintro's code path made, not a
second request sent to explain the first. :func:`describe_exchange` turns it
into log lines: the HTTP status, an allowlist of headers, the protocol-level
stop reason, the content blocks with their sizes, the thinking/text token
split, and the first bytes of the body. Credentials are never on that list:
request headers are not recorded at all, and the body excerpt goes through the
caller's redaction before it is printed.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Final

#: Response headers worth printing. An allowlist, not a denylist: a gateway
#: that sets an unexpected header carrying account data must not have it
#: printed just because nobody thought to exclude it.
_HEADER_ALLOWLIST: Final[frozenset[str]] = frozenset(
    {
        "content-type",
        "content-length",
        "content-encoding",
        "retry-after",
        "request-id",
        "x-request-id",
        "server",
    },
)

#: Header prefixes whose members are rate-limit counters, the usual evidence
#: that an "empty" answer was a quota response.
_HEADER_PREFIXES: Final[tuple[str, ...]] = (
    "x-ratelimit-",
    "anthropic-ratelimit-",
)

#: Characters of the redacted response body kept for the log.
BODY_EXCERPT_CHARS: Final[int] = 500


@dataclass
class CapturedExchange:
    """The last HTTP response seen during a capture.

    Attributes:
        status: HTTP status code.
        headers: Allowlisted response headers, names lower-cased.
        body: Raw response body, or empty when the SDK never read it.
    """

    status: int
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""


@dataclass
class HttpCapture:
    """Responses recorded while :func:`capture_http` is active.

    Attributes:
        responses: The ``httpx`` responses, in the order they arrived; the SDK
            retries, so only the last one is the answer the caller saw.
    """

    responses: list[Any] = field(default_factory=list)

    def last(self) -> CapturedExchange | None:
        """Return the final exchange, reduced to what is safe to describe.

        Returns:
            The last response's status, allowlisted headers and body, or None
            when no request completed.
        """
        if not self.responses:
            return None
        response = self.responses[-1]
        try:
            body = bytes(response.content)
        except Exception:  # an unread stream has no body to show
            body = b""
        headers = {
            name.lower(): value
            for name, value in response.headers.items()
            if _header_is_allowed(name)
        }
        return CapturedExchange(
            status=int(response.status_code),
            headers=headers,
            body=body,
        )


def _header_is_allowed(name: str) -> bool:
    """Return whether a response header may be printed.

    Args:
        name: Header name as the server sent it.

    Returns:
        True for allowlisted names and rate-limit counters.
    """
    lowered = name.lower()
    return lowered in _HEADER_ALLOWLIST or lowered.startswith(_HEADER_PREFIXES)


@contextmanager
def capture_http() -> Iterator[HttpCapture]:
    """Record every response the SDK clients receive inside the block.

    Both SDKs lintro drives (anthropic, openai) send through
    ``httpx.AsyncClient.send``; wrapping it for the length of the call sees the
    exact exchange without touching lintro's providers. The original method is
    restored on exit, error or not.

    Yields:
        HttpCapture: The capture the responses are appended to.
    """
    import httpx

    capture = HttpCapture()
    original = httpx.AsyncClient.send

    async def _send(self: httpx.AsyncClient, request: Any, **kwargs: Any) -> Any:
        response = await original(self, request, **kwargs)
        capture.responses.append(response)
        return response

    httpx.AsyncClient.send = _send  # type: ignore[method-assign]
    try:
        yield capture
    finally:
        httpx.AsyncClient.send = original  # type: ignore[method-assign]


def _mapping(value: Any) -> dict[str, Any]:
    """Return a value when it is a JSON object, else an empty one.

    A gateway's error envelope can put a string or a list where the protocol
    has an object; the diagnostic must describe that, never raise on it.

    Args:
        value: Any decoded JSON value.

    Returns:
        The value itself when it is a dict, otherwise ``{}``.
    """
    return value if isinstance(value, dict) else {}


def _count(value: Any) -> int:
    """Return a token count, or 0 when the field is not an integer.

    Args:
        value: Any decoded JSON value.

    Returns:
        The integer, or 0 for anything else (bool included).
    """
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _anthropic_shape(payload: dict[str, Any]) -> list[str]:
    """Describe an Anthropic Messages envelope.

    Args:
        payload: The decoded response body.

    Returns:
        Lines naming the stop reason, the blocks and the token split.
    """
    content = payload.get("content")
    blocks = [
        f"{block.get('type', '?')}({len(_block_text(block))} chars)"
        for block in (content if isinstance(content, list) else [])
        if isinstance(block, dict)
    ]
    usage = _mapping(payload.get("usage"))
    thinking = _mapping(usage.get("output_tokens_details")).get("thinking_tokens")
    return [
        f"stop_reason: {payload.get('stop_reason')}",
        f"content blocks: {', '.join(blocks) or 'none'}",
        (
            f"output tokens: {usage.get('output_tokens')} "
            f"(thinking {thinking if thinking is not None else 'n/a'})"
        ),
    ]


def _block_text(block: dict[str, Any]) -> str:
    """Return the text a content block carries, answer or reasoning.

    Args:
        block: One Anthropic content block.

    Returns:
        The block's text or thinking, or an empty string.
    """
    return str(block.get("text") or block.get("thinking") or "")


def _openai_shape(payload: dict[str, Any]) -> list[str]:
    """Describe an OpenAI Chat Completions envelope.

    Args:
        payload: The decoded response body.

    Returns:
        Lines naming the finish reason, the message parts and the token split.
    """
    choices = payload.get("choices")
    first = choices[0] if isinstance(choices, list) and choices else None
    choice = _mapping(first)
    message = _mapping(choice.get("message"))
    usage = _mapping(payload.get("usage"))
    details = _mapping(usage.get("completion_tokens_details"))
    reasoning = details.get("reasoning_tokens")
    content = str(message.get("content") or "")
    reasoning_content = str(message.get("reasoning_content") or "")
    return [
        f"finish_reason: {choice.get('finish_reason')}",
        (
            f"message: content({len(content)} chars), "
            f"reasoning_content({len(reasoning_content)} chars)"
        ),
        (
            f"output tokens: {usage.get('completion_tokens')} "
            f"(reasoning {reasoning if reasoning is not None else 'n/a'})"
        ),
    ]


def describe_exchange(
    exchange: CapturedExchange | None,
    *,
    redact: Callable[[str], str],
    protocol: str | None = None,
) -> str:
    """Render an exchange as the diagnostic block a failed smoke prints.

    Args:
        exchange: The captured exchange, or None when no request completed.
        redact: The caller's redaction, applied to the whole body before it is
            cut to the excerpt, so a credential straddling the cut cannot
            survive as a fragment the redaction no longer recognises.
        protocol: The row's wire protocol. When given, its envelope shape is
            tried first, so a gateway error carrying the other protocol's keys
            is not described in the wrong terms; without it the body's keys
            decide.

    Returns:
        A multi-line description safe to print, summarise and write to disk.
    """
    if exchange is None:
        return "HTTP: no response was recorded for the call"
    lines = [f"HTTP status: {exchange.status}"]
    for name in sorted(exchange.headers):
        lines.append(f"header {name}: {exchange.headers[name]}")
    text = exchange.body.decode("utf-8", errors="replace")
    try:
        payload = json.loads(text)
    except ValueError:
        payload = None
    shapes = [("choices", _openai_shape), ("content", _anthropic_shape)]
    if protocol == "anthropic":
        shapes.reverse()
    describe = next(
        (
            shape
            for key, shape in shapes
            if isinstance(payload, dict) and key in payload
        ),
        None,
    )
    if describe is not None and isinstance(payload, dict):
        lines.extend(describe(payload))
    elif not exchange.body:
        lines.append("body: empty")
    else:
        lines.append("body: not a completion envelope")
    excerpt = redact(text)[:BODY_EXCERPT_CHARS]
    lines.append(
        f"body ({len(exchange.body)} bytes; first {BODY_EXCERPT_CHARS} "
        f"characters after redaction): {excerpt!r}",
    )
    return "\n".join(lines)


def usage_of(exchange: CapturedExchange | None) -> tuple[str, int, int]:
    """Return the model echoed back and the token counts of an exchange.

    Args:
        exchange: The captured exchange, or None.

    Returns:
        ``(model, input_tokens, output_tokens)``; unknowns are ``"?"`` and 0.
    """
    if exchange is None:
        return "?", 0, 0
    try:
        payload = json.loads(exchange.body.decode("utf-8", errors="replace"))
    except ValueError:
        return "?", 0, 0
    if not isinstance(payload, dict):
        return "?", 0, 0
    usage = _mapping(payload.get("usage"))
    input_tokens = _count(usage.get("input_tokens", usage.get("prompt_tokens")))
    output_tokens = _count(
        usage.get("output_tokens", usage.get("completion_tokens")),
    )
    return str(payload.get("model") or "?"), input_tokens, output_tokens
