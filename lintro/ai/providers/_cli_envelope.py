"""One validated decoder for the JSON envelopes the CLI transports read (#2513).

``claude --output-format json`` and ``agent --output-format json`` answer with
one JSON object; ``codex exec --json`` answers with JSON Lines events and, on
some paths, the same single-object shape. Each provider used to read that
output with best-effort string checks (``json.loads`` then ``.get`` chains),
so a truncated stream, a non-object envelope, or a wrong-typed field surfaced
as a different bare exception per provider — an ``AttributeError`` on a JSON
array, a ``ValueError`` from ``int("n/a")`` — none of them an
:class:`~lintro.ai.exceptions.AIProviderError`, so the review layer could not
classify them.

This module is the single place that knows the envelope's shape. It yields a
frozen :class:`CliEnvelope` or raises :class:`CliEnvelopeError`, an
``AIProviderError`` carrying a :class:`CliEnvelopeErrorKind` and the offending
fragment, so the classification kind downstream stays what it was for a
malformed response while the message says what was wrong.

Precedence is deliberate: an envelope that reports an error is handed back
as one even when its usage block is malformed, because the error markers
(``terminal_reason``, ``api_error_status``) are what the caller needs and a
"usage has the wrong type" message would hide them.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum, auto
from typing import Any, NoReturn

from lintro.ai.exceptions import AIProviderError
from lintro.ai.raw_response import (
    CLI_ENVELOPE_STAGE,
    describe_raw_response,
    recover_prose_envelope,
    sanitize_for_display,
)
from lintro.ai.secrets import redact_secrets

__all__ = [
    "CliEnvelope",
    "CliEnvelopeError",
    "CliEnvelopeErrorKind",
    "JsonlEvents",
    "decode_envelope",
    "decode_envelope_or_prose",
    "decode_jsonl_events",
    "decode_usage",
    "truncated_tail_error",
]

#: Characters of context kept either side of a parse failure in the message.
_FRAGMENT_CONTEXT = 80

#: Upper bound on the fragment quoted in an error message. The full raw text
#: is persisted separately by :func:`describe_raw_response`; the fragment is a
#: pointer, not the evidence.
_FRAGMENT_LIMIT = 200

#: Envelope keys that tell a rejected request apart from an answer that
#: overran its output ceiling; kept on an error envelope for the classifier
#: (#2695).
_ERROR_MARKER_KEYS = ("terminal_reason", "api_error_status")


class CliEnvelopeErrorKind(StrEnum):
    """Why a CLI envelope could not be decoded."""

    #: The text is not JSON at all (prose, a stack trace, an empty stream).
    NOT_JSON = auto()
    #: The text looks like JSON that was cut off before it closed.
    TRUNCATED = auto()
    #: Valid JSON whose top level is not an object.
    NOT_OBJECT = auto()
    #: A JSON object with a required field missing or of the wrong type.
    SCHEMA = auto()


class CliEnvelopeError(AIProviderError):
    """A CLI envelope that does not match the shape the transport expects."""

    def __init__(
        self,
        message: str,
        *,
        kind: CliEnvelopeErrorKind,
        fragment: str = "",
    ) -> None:
        """Initialise the error.

        Args:
            message: Human-readable description, provider-prefixed.
            kind: Which contract the envelope broke.
            fragment: The offending span of the raw text, bounded and
                sanitised. Never the whole output.
        """
        super().__init__(message)
        self.kind = kind
        self.fragment = fragment


@dataclass(frozen=True, slots=True)
class CliEnvelope:
    """A decoded single-object CLI envelope.

    Attributes:
        result: The answer text. A ``structured_output`` block, when present,
            replaces it serialised as JSON; a ``result`` carried as a JSON
            object or array is serialised the same way.
        is_error: Whether the envelope reports a failure (``is_error`` set,
            or ``subtype`` equal to ``"error"``).
        subtype: The envelope's ``subtype``, when it carries one.
        error_markers: ``terminal_reason`` / ``api_error_status`` when present,
            in envelope order, for the error message.
        input_tokens: Prompt tokens reported, 0 when absent.
        output_tokens: Completion tokens reported, 0 when absent.
        total_cost_usd: The cost the CLI reported, or ``None`` when it did not.
        session_id: The session the CLI opened or resumed, when reported.
        num_turns: Agent turns the call took, when reported as a count.
        model_usage: The ``modelUsage`` block verbatim, for
            :func:`lintro.ai.providers.reported_model.parse_model_usage`.
        raw: The decoded object, for fields this module does not model.
    """

    result: str
    is_error: bool
    subtype: str | None
    error_markers: Mapping[str, object]
    input_tokens: int
    output_tokens: int
    total_cost_usd: float | None
    session_id: str | None
    num_turns: int | None
    model_usage: object | None
    raw: Mapping[str, Any] = field(repr=False)


@dataclass(frozen=True, slots=True)
class JsonlEvents:
    """The events decoded from a JSON Lines stream.

    Attributes:
        events: Every line that decoded to a JSON object, in order.
        unparsed_tail: The last line when it did not decode to a JSON
            object, else ``None``. A tail that looks like JSON is the sign of
            a stream cut mid-event; a prose tail is the binary's own chatter.
    """

    events: tuple[Mapping[str, Any], ...]
    unparsed_tail: str | None


def _bounded_fragment(text: str, *, position: int | None = None) -> str:
    """Return a bounded, display-safe span of *text* around *position*.

    Args:
        text: Raw CLI output.
        position: Offset the parse failed at; the start when ``None``.

    Returns:
        The span with terminal escapes neutralised and secrets redacted,
        at most :data:`_FRAGMENT_LIMIT` characters.
    """
    if position is None:
        span = text[:_FRAGMENT_LIMIT]
    else:
        start = max(0, position - _FRAGMENT_CONTEXT)
        span = text[start : start + _FRAGMENT_LIMIT]
    return redact_secrets(sanitize_for_display(text=span)).strip()


def _looks_like_json(text: str) -> bool:
    """Return whether *text* opens the way a JSON document does.

    Args:
        text: Stripped CLI output.

    Returns:
        True when it starts with an object or array opener.
    """
    return text[:1] in {"{", "["}


def _fail_decode(*, provider: str, text: str, exc: json.JSONDecodeError) -> NoReturn:
    """Raise the error for text that ``json.loads`` rejected.

    A document that opens like JSON and fails at its end, or inside an
    unterminated string, was cut off; anything else is simply not JSON.
    The heuristic is only applied to JSON-looking input so that a prose
    answer with an unbalanced bracket is never reported as truncated.

    Args:
        provider: Display name for the message prefix.
        text: Stripped CLI output.
        exc: The decoder's failure.

    Raises:
        CliEnvelopeError: Always; kind ``TRUNCATED`` or ``NOT_JSON``.
    """
    fragment = _bounded_fragment(text, position=exc.pos)
    ran_out = exc.pos >= len(text) - 1 or exc.msg.startswith("Unterminated")
    if _looks_like_json(text) and ran_out:
        raise CliEnvelopeError(
            f"{provider} CLI returned invalid JSON: {exc} "
            f"(output appears truncated at position {exc.pos} of {len(text)})",
            kind=CliEnvelopeErrorKind.TRUNCATED,
            fragment=fragment,
        ) from exc
    raise CliEnvelopeError(
        f"{provider} CLI returned invalid JSON: {exc}",
        kind=CliEnvelopeErrorKind.NOT_JSON,
        fragment=fragment,
    ) from exc


def _schema_message(*, provider: str, name: str, expected: str, value: object) -> str:
    """Word a field-type violation.

    Args:
        provider: Display name for the message prefix.
        name: Dotted field name.
        expected: What the contract allows.
        value: What the envelope carried.

    Returns:
        The message, with the value bounded and sanitised.
    """
    shown = _bounded_fragment(json.dumps(value, default=str))
    return (
        f"{provider} CLI envelope field {name!r}: expected {expected}, "
        f"got {type(value).__name__} {shown}"
    )


def _fail_schema(*, provider: str, name: str, expected: str, value: object) -> NoReturn:
    """Raise the error for a field of the wrong type.

    Args:
        provider: Display name for the message prefix.
        name: Dotted field name.
        expected: What the contract allows.
        value: What the envelope carried.

    Raises:
        CliEnvelopeError: Always; kind ``SCHEMA``.
    """
    raise CliEnvelopeError(
        _schema_message(provider=provider, name=name, expected=expected, value=value),
        kind=CliEnvelopeErrorKind.SCHEMA,
        fragment=_bounded_fragment(json.dumps(value, default=str)),
    )


def _is_count(value: object) -> bool:
    """Return whether *value* is a non-negative int that is not a bool.

    Args:
        value: Any decoded JSON value.

    Returns:
        True for ``0``, ``1``, ...; False for ``True``, ``-1``, ``1.0``.
    """
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _usage_counter(
    usage: Mapping[str, Any],
    *,
    provider: str,
    names: tuple[str, ...],
) -> int:
    """Read one token counter from a usage block under either spelling.

    A value that is present but not a count fails the envelope through
    :func:`_fail_schema`.

    Args:
        usage: The decoded ``usage`` object.
        provider: Display name for the message prefix.
        names: Accepted keys, first match wins (``input_tokens`` and the
            camel-case ``inputTokens`` are both seen in the wild).

    Returns:
        The counter, or 0 when none of *names* is present.
    """
    for name in names:
        if name in usage and usage[name] is not None:
            value = usage[name]
            if not _is_count(value):
                _fail_schema(
                    provider=provider,
                    name=f"usage.{name}",
                    expected="a non-negative integer",
                    value=value,
                )
            return int(value)
    return 0


def decode_usage(usage: object, *, provider: str) -> tuple[int, int]:
    """Validate a ``usage`` block and return its two token counters.

    Shared with the JSON Lines path, where ``turn.completed`` carries the
    same block.

    Args:
        usage: The decoded ``usage`` value, or ``None`` when absent.
        provider: Display name for the message prefix.

    Returns:
        ``(input_tokens, output_tokens)``, zeros when absent.

    Raises:
        CliEnvelopeError: When ``usage`` is present but not an object, or a
            counter is not a non-negative integer.
    """
    if usage is None:
        return 0, 0
    if not isinstance(usage, Mapping):
        raise CliEnvelopeError(
            _schema_message(
                provider=provider,
                name="usage",
                expected="an object",
                value=usage,
            ),
            kind=CliEnvelopeErrorKind.SCHEMA,
            fragment=_bounded_fragment(json.dumps(usage, default=str)),
        )
    return (
        _usage_counter(usage, provider=provider, names=("input_tokens", "inputTokens")),
        _usage_counter(
            usage,
            provider=provider,
            names=("output_tokens", "outputTokens"),
        ),
    )


def _decode_cost(value: object, *, provider: str) -> float | None:
    """Validate ``total_cost_usd``.

    ``json.loads`` accepts ``NaN`` and ``Infinity``, and a bool is an int in
    Python; neither is a price, and both fail the envelope through
    :func:`_fail_schema`, as does a negative number.

    Args:
        value: The decoded value, or ``None`` when absent.
        provider: Display name for the message prefix.

    Returns:
        The cost, or ``None`` when the CLI reported none.
    """
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        _fail_schema(
            provider=provider,
            name="total_cost_usd",
            expected="a finite non-negative number",
            value=value,
        )
    return float(value)


def _decode_result(data: Mapping[str, Any], *, provider: str) -> str:
    """Return the answer text, with ``structured_output`` taking precedence.

    A ``result`` that is a scalar other than a string fails the envelope
    through :func:`_fail_schema`.

    Args:
        data: The decoded envelope.
        provider: Display name for the message prefix.

    Returns:
        The answer as text. An object or array is serialised as JSON; an
        absent or null result is the empty string.
    """
    structured = data.get("structured_output")
    if structured is not None:
        return json.dumps(structured)
    result = data.get("result")
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    if isinstance(result, (Mapping, list)):
        return json.dumps(result)
    _fail_schema(
        provider=provider,
        name="result",
        expected="a string, object or array",
        value=result,
    )


def _decode_optional_str(
    data: Mapping[str, Any],
    *,
    name: str,
    provider: str,
) -> str | None:
    """Return a string field, ``None`` when absent, null or blank.

    A field that is present and not a string fails the envelope through
    :func:`_fail_schema`.

    Args:
        data: The decoded envelope.
        name: Field name.
        provider: Display name for the message prefix.

    Returns:
        The stripped value, or ``None``.
    """
    value = data.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        _fail_schema(provider=provider, name=name, expected="a string", value=value)
    stripped = value.strip()
    return stripped or None


def _error_markers(data: Mapping[str, Any]) -> dict[str, object]:
    """Return the error-marker fields present on *data*, in envelope order.

    Args:
        data: The decoded envelope.

    Returns:
        A mapping of marker name to value, empty when none is set.
    """
    return {key: data[key] for key in _ERROR_MARKER_KEYS if data.get(key) is not None}


def _parse_object(raw: str, *, provider: str) -> Mapping[str, Any]:
    """Decode *raw* and require a JSON object.

    Args:
        raw: The complete CLI stdout.
        provider: Display name for the message prefix.

    Returns:
        The decoded object.

    Raises:
        CliEnvelopeError: With kind ``NOT_JSON`` on blank or non-JSON text,
            ``TRUNCATED`` on JSON cut short, ``NOT_OBJECT`` on any other
            top-level value.
    """
    text = (raw or "").strip()
    if not text:
        raise CliEnvelopeError(
            f"{provider} CLI returned invalid JSON: empty output",
            kind=CliEnvelopeErrorKind.NOT_JSON,
        )
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        _fail_decode(provider=provider, text=text, exc=exc)
    if not isinstance(data, Mapping):
        fragment = _bounded_fragment(text)
        raise CliEnvelopeError(
            f"{provider} CLI envelope is not a JSON object: "
            f"got {type(data).__name__} {fragment}",
            kind=CliEnvelopeErrorKind.NOT_OBJECT,
            fragment=fragment,
        )
    return data


def _error_envelope(data: Mapping[str, Any]) -> CliEnvelope:
    """Build the envelope for a payload that reports an error.

    Read leniently: the cause and the error markers are what the caller
    needs, and a malformed usage block must not hide them.

    Args:
        data: The decoded object, with ``is_error`` set or ``subtype`` equal
            to ``"error"``.

    Returns:
        The envelope with ``is_error`` set and the usage zeroed.
    """
    cause = data.get("result")
    subtype = data.get("subtype")
    return CliEnvelope(
        result=cause if isinstance(cause, str) else "",
        is_error=True,
        subtype=subtype if isinstance(subtype, str) else None,
        error_markers=_error_markers(data),
        input_tokens=0,
        output_tokens=0,
        total_cost_usd=None,
        session_id=None,
        num_turns=None,
        model_usage=data.get("modelUsage"),
        raw=data,
    )


def decode_envelope(raw: str, *, provider: str) -> CliEnvelope:
    """Decode one single-object CLI envelope.

    Args:
        raw: The complete CLI stdout.
        provider: Display name for the message prefix, e.g. ``"Claude"``.

    Returns:
        The validated envelope. An envelope that reports an error is
        returned with ``is_error`` set and its usage read leniently, so the
        caller can raise with the error's own cause and markers.

    Raises:
        CliEnvelopeError: When the text is not JSON, is truncated, is not an
            object, or (on a non-error envelope) a field has the wrong type.
    """
    data = _parse_object(raw, provider=provider)
    subtype = data.get("subtype")
    if data.get("is_error") or subtype == "error":
        return _error_envelope(data)
    if subtype is not None and not isinstance(subtype, str):
        raise CliEnvelopeError(
            _schema_message(
                provider=provider,
                name="subtype",
                expected="a string",
                value=subtype,
            ),
            kind=CliEnvelopeErrorKind.SCHEMA,
            fragment=_bounded_fragment(json.dumps(subtype, default=str)),
        )
    input_tokens, output_tokens = decode_usage(data.get("usage"), provider=provider)
    raw_turns = data.get("num_turns")
    return CliEnvelope(
        result=_decode_result(data, provider=provider),
        is_error=False,
        subtype=subtype,
        error_markers={},
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_cost_usd=_decode_cost(data.get("total_cost_usd"), provider=provider),
        session_id=_decode_optional_str(data, name="session_id", provider=provider),
        # Informational: a count that is not one reads as unknown rather
        # than failing the call, and a bool is never a turn count.
        num_turns=raw_turns if _is_count(raw_turns) else None,
        model_usage=data.get("modelUsage"),
        raw=data,
    )


def decode_envelope_or_prose(raw: str, *, provider: str) -> CliEnvelope | str:
    """Decode an envelope, recovering a prose answer when it is not JSON.

    The CLI has exited zero by the time a caller reaches this. Text that is
    not JSON at all is in practice the agent answering in prose, and
    discarding it threw away real findings (#1853), so it is handed back as
    unstructured content. JSON that is cut short is recovered the same way:
    the review layer extracts what it can and the full text is persisted.
    A well-formed object that breaks the field contract is not an answer and
    is raised, with the full raw output as evidence.

    Args:
        raw: The complete CLI stdout.
        provider: Display name for the message prefix, e.g. ``"Claude"``.

    Returns:
        The decoded envelope, or the recovered prose as a string.

    Raises:
        CliEnvelopeError: On blank output, a non-object envelope, or a
            schema break. The message carries the evidence block.
    """
    try:
        return decode_envelope(raw, provider=provider)
    except CliEnvelopeError as exc:
        if exc.kind in {CliEnvelopeErrorKind.NOT_JSON, CliEnvelopeErrorKind.TRUNCATED}:
            recovered = recover_prose_envelope(
                provider=provider,
                stdout=raw,
                reason=str(exc),
            )
            if recovered is not None:
                return recovered
        evidence = describe_raw_response(
            provider=provider,
            stage=CLI_ENVELOPE_STAGE,
            raw=raw,
        )
        raise CliEnvelopeError(
            f"{exc}\n{evidence}",
            kind=exc.kind,
            fragment=exc.fragment,
        ) from exc


def decode_jsonl_events(raw: str, *, provider: str) -> JsonlEvents:
    """Decode a JSON Lines stream into its object events.

    Lines that are not JSON objects are skipped: ``codex`` interleaves its
    own human-readable lines with the event stream, and a non-JSON line in
    the middle of a stream is not evidence of anything. The last line is
    reported separately when it did not decode, so a caller that found no
    answer can tell a stream cut mid-event from a stream that never carried
    one.

    Args:
        raw: The complete CLI stdout.
        provider: Display name, kept on the signature for parity with the
            other decoders; the stream itself never fails here.

    Returns:
        The decoded events and the undecoded tail, if any.
    """
    del provider
    events: list[Mapping[str, Any]] = []
    unparsed_tail: str | None = None
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError:
            unparsed_tail = stripped
            continue
        unparsed_tail = None
        if isinstance(event, Mapping):
            events.append(event)
    return JsonlEvents(events=tuple(events), unparsed_tail=unparsed_tail)


def truncated_tail_error(*, provider: str, tail: str) -> CliEnvelopeError | None:
    """Return the truncation error for a JSON-looking undecoded tail line.

    Args:
        provider: Display name for the message prefix.
        tail: The last line of the stream, which did not decode.

    Returns:
        The error when *tail* opens like JSON (the event was cut off), else
        ``None`` (the binary's own chatter).
    """
    if not _looks_like_json(tail):
        return None
    return CliEnvelopeError(
        f"{provider} CLI output ends in an incomplete JSON Lines event",
        kind=CliEnvelopeErrorKind.TRUNCATED,
        fragment=_bounded_fragment(tail),
    )
