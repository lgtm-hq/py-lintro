# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""Record and describe the raw HTTP exchange behind one smoke call (#2748).

lintro's providers hand back only the text a model wrote, which is all a review
needs and nothing a failed smoke can be diagnosed from. ``EmptyResponse`` on
the z.ai row reddened every weekly run for a month and the log could not say
whether the gateway sent an empty 200, a quota page, or a 200 whose only block
was the model's reasoning (the answer a thinking model gives when the token
cap runs out before it starts writing).

:func:`capture_http` watches the SDKs' own HTTP clients for the length of one
call, so the exchange described is the one lintro's code path made, not a
second request sent to explain the first. The two SDKs lintro drives send
through different packages (anthropic through ``httpx``, openai through its
fork ``httpx2``), so both are watched. :func:`describe_exchange` turns the
exchange into log lines: the HTTP status, an allowlist of headers, the
protocol-level stop reason, the content blocks with their sizes, the
thinking/text token split, and the first bytes of the body. Request headers
are never recorded, and every rendered line goes through the caller's
redaction before it is printed.
"""

from __future__ import annotations

import importlib
import json
import re
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
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

#: HTTP packages whose ``AsyncClient.send`` the SDKs call: anthropic uses
#: ``httpx``, openai 3.x its fork ``httpx2``. A package that is not installed
#: is skipped; the call cannot have gone through it.
TRANSPORTS: Final[tuple[str, ...]] = ("httpx", "httpx2")

#: Characters that end or break a log line: C0 controls, DEL, and the
#: Unicode line and paragraph separators some log viewers honour.
_LINE_BREAKERS: Final[re.Pattern[str]] = re.compile(
    "[\x00-\x1f\x7f\x85\u2028\u2029]",
)

#: UTF-8 bytes of the redacted response body kept for the log. Bytes, not
#: characters, so a multibyte error page cannot print several times the cap.
BODY_EXCERPT_BYTES: Final[int] = 500


@dataclass
class CapturedExchange:
    """The last HTTP response seen during a capture.

    Attributes:
        status: HTTP status code.
        headers: Allowlisted response headers, names lower-cased.
        body: Raw response body, or None when the SDK never read it (a
            streamed response); an empty body is ``b""``.
        transport: The HTTP package that delivered the response.
    """

    status: int
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes | None = b""
    transport: str = "httpx"


@dataclass
class HttpCapture:
    """Responses recorded while :func:`capture_http` is active.

    Attributes:
        responses: ``(transport, response)`` pairs in the order they arrived;
            the SDK retries, so only the last one is the answer the caller saw.
        transports: The packages that were watched.
    """

    responses: list[tuple[str, Any]] = field(default_factory=list)
    transports: list[str] = field(default_factory=list)

    def last(self) -> CapturedExchange | None:
        """Return the final exchange, reduced to what is safe to describe.

        Returns:
            The last response's status, allowlisted headers, body and
            transport, or None when no request completed.
        """
        if not self.responses:
            return None
        transport, response = self.responses[-1]
        body: bytes | None
        try:
            body = bytes(response.content)
        except Exception:  # an unread stream has no body to show
            body = None
        headers = {
            name.lower(): value
            for name, value in response.headers.items()
            if _header_is_allowed(name)
        }
        return CapturedExchange(
            status=int(response.status_code),
            headers=headers,
            body=body,
            transport=transport,
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
def _watch(*, module_name: str, capture: HttpCapture) -> Iterator[None]:
    """Record every response one HTTP package's async client receives.

    Args:
        module_name: The package to watch, from :data:`TRANSPORTS`.
        capture: Where the responses are appended.

    Yields:
        None: The package is watched for the length of the block, then the
            original ``send`` is restored, error or not.
    """
    try:
        module = importlib.import_module(module_name)
    except ImportError:
        yield
        return
    client = module.AsyncClient
    original = client.send

    async def _send(self: Any, request: Any, **kwargs: Any) -> Any:
        response = await original(self, request, **kwargs)
        capture.responses.append((module_name, response))
        return response

    client.send = _send
    capture.transports.append(module_name)
    try:
        yield
    finally:
        client.send = original


@contextmanager
def capture_http() -> Iterator[HttpCapture]:
    """Record every response the SDK clients receive inside the block.

    Wrapping each transport's ``AsyncClient.send`` for the length of the call
    sees the exact exchange without touching lintro's providers.

    Yields:
        HttpCapture: The capture the responses are appended to.
    """
    capture = HttpCapture()
    with ExitStack() as stack:
        for module_name in TRANSPORTS:
            stack.enter_context(_watch(module_name=module_name, capture=capture))
        yield capture


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


def _shown(value: Any) -> str:
    """Render a token count for the log, or ``n/a`` when it is not one.

    A count is printed only when it is an integer, so a gateway cannot put
    arbitrary text on the token line.

    Args:
        value: Any decoded JSON value.

    Returns:
        The integer as text, or ``n/a``.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return "n/a"
    return str(_count(value))


def _scalar(value: Any) -> str:
    """Render one gateway-chosen value so it stays on its own log line.

    The runner reads any stdout line that starts with ``::`` as a workflow
    command (``::add-mask::``, ``::warning::``, ``::stop-commands::``). A stop
    reason, block type or header value is the gateway's text, so a newline in
    it could start such a line. Line-breaking characters are shown as
    escapes, and a value that itself starts with ``::`` is prefixed with
    ``!``. This is output hygiene, separate from credential redaction.

    Args:
        value: Any decoded JSON value or header value.

    Returns:
        A single-line rendering that cannot begin a workflow command.
    """
    text = _LINE_BREAKERS.sub(
        lambda match: match.group().encode("unicode_escape").decode("ascii"),
        str(value),
    )
    return f"!{text}" if text.lstrip().startswith("::") else text


def _anthropic_shape(payload: dict[str, Any]) -> list[str]:
    """Describe an Anthropic Messages envelope.

    Args:
        payload: The decoded response body.

    Returns:
        Lines naming the stop reason, the blocks and the token split.
    """
    content = payload.get("content")
    blocks = [
        f"{_scalar(block.get('type', '?'))}({len(_block_text(block))} chars)"
        for block in (content if isinstance(content, list) else [])
        if isinstance(block, dict)
    ]
    usage = _mapping(payload.get("usage"))
    thinking = _mapping(usage.get("output_tokens_details")).get("thinking_tokens")
    return [
        f"stop_reason: {_scalar(payload.get('stop_reason'))}",
        f"content blocks: {', '.join(blocks) or 'none'}",
        (
            f"output tokens: {_shown(usage.get('output_tokens'))} "
            f"(thinking {_shown(thinking)})"
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
        f"finish_reason: {_scalar(choice.get('finish_reason'))}",
        (
            f"message: content({len(content)} chars), "
            f"reasoning_content({len(reasoning_content)} chars)"
        ),
        (
            f"output tokens: {_shown(usage.get('completion_tokens'))} "
            f"(reasoning {_shown(reasoning)})"
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
        redact: The caller's redaction. It is applied to the whole body before
            the body is cut to the excerpt, so a credential straddling the cut
            cannot survive as a fragment, and then to every rendered line,
            because header values and parsed fields come from the gateway too.
        protocol: The row's wire protocol. When given, its envelope shape is
            tried first, so a gateway error carrying the other protocol's keys
            is not described in the wrong terms; without it the body's keys
            decide.

    Returns:
        A multi-line description safe to print, summarise and write to disk.
    """
    if exchange is None:
        return "HTTP: no response was recorded for the call"
    lines = [f"HTTP status: {exchange.status} (via {exchange.transport})"]
    for name in sorted(exchange.headers):
        lines.append(f"header {name}: {_scalar(exchange.headers[name])}")
    if exchange.body is None:
        # A streamed response the SDK never read: there is no body to show,
        # which is not the same finding as a body that arrived empty.
        lines.append("body: not read by the SDK")
        return _redact_block(lines, redact=redact)
    text = exchange.body.decode("utf-8", errors="replace")
    try:
        payload = json.loads(text)
    except (ValueError, RecursionError):
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
    # Cut on the UTF-8 encoding and drop a character the cut split, rather
    # than emit half of one.
    excerpt = (
        redact(text)
        .encode("utf-8")[:BODY_EXCERPT_BYTES]
        .decode("utf-8", errors="ignore")
    )
    lines.append(
        f"body ({len(exchange.body)} bytes; first {BODY_EXCERPT_BYTES} "
        f"bytes after redaction): {excerpt!r}",
    )
    return _redact_block(lines, redact=redact)


def _redact_block(lines: list[str], *, redact: Callable[[str], str]) -> str:
    """Join the diagnostic lines with every one of them redacted.

    Every line carries values the gateway chose (header values, stop reasons,
    block types), not only the body excerpt. Each line is redacted on its own,
    so a credential echoed into one field costs that line and not the whole
    diagnostic; the joined block is then redacted once more, so nothing the
    per-line pass could miss survives.

    Args:
        lines: The rendered diagnostic lines.
        redact: The caller's redaction.

    Returns:
        The redacted, newline-joined block.
    """
    return redact("\n".join(redact(line) for line in lines))


def usage_of(exchange: CapturedExchange | None) -> tuple[str, int, int]:
    """Return the model echoed back and the token counts of an exchange.

    Args:
        exchange: The captured exchange, or None.

    Returns:
        ``(model, input_tokens, output_tokens)``; unknowns are ``"?"`` and 0.
    """
    if exchange is None or exchange.body is None:
        return "?", 0, 0
    try:
        payload = json.loads(exchange.body.decode("utf-8", errors="replace"))
    except (ValueError, RecursionError):
        return "?", 0, 0
    if not isinstance(payload, dict):
        return "?", 0, 0
    usage = _mapping(payload.get("usage"))
    input_tokens = _count(usage.get("input_tokens", usage.get("prompt_tokens")))
    output_tokens = _count(
        usage.get("output_tokens", usage.get("completion_tokens")),
    )
    return str(payload.get("model") or "?"), input_tokens, output_tokens
