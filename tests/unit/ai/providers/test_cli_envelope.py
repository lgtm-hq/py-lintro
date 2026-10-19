"""The validated CLI envelope decoder and its adoption by every CLI path (#2513).

Before the decoder each CLI transport read its envelope with ``json.loads``
and ``.get`` chains, so the same malformed output surfaced as a different bare
exception per provider. These tests pin the decoder's contract and then drive
each provider with the same malformed envelopes to show every one of them
surfaces as an ``AIProviderError`` carrying the offending fragment.
"""

from __future__ import annotations

import json
import subprocess  # nosec B404 - only CompletedProcess objects are constructed here
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.ai.enums import AITransport, CliBareMode
from lintro.ai.exceptions import AIProviderError
from lintro.ai.providers._cli_envelope import (
    CliEnvelopeError,
    CliEnvelopeErrorKind,
    decode_envelope,
    decode_envelope_or_prose,
    decode_jsonl_events,
    decode_usage,
    truncated_tail_error,
)
from lintro.ai.providers.anthropic.provider import AnthropicProvider
from lintro.ai.providers.base import BaseAIProvider
from lintro.ai.providers.cursor.provider import CursorProvider
from lintro.ai.providers.openai.provider import OpenAIProvider
from tests.unit.ai.conftest import CLAUDE_HELP, patch_cli_exec

_PROVIDER = "Test"


# --- decode_envelope -----------------------------------------------------------


def test_a_complete_envelope_decodes_every_field() -> None:
    """Every modelled field is read, with both counter spellings accepted."""
    raw = json.dumps(
        {
            "result": "hello",
            "subtype": "success",
            "usage": {"input_tokens": 10, "outputTokens": 5},
            "total_cost_usd": 0.25,
            "session_id": " sess-1 ",
            "num_turns": 3,
            "modelUsage": {"claude-x": {"inputTokens": 10}},
        },
    )

    envelope = decode_envelope(raw, provider=_PROVIDER)

    assert_that(envelope.result).is_equal_to("hello")
    assert_that(envelope.is_error).is_false()
    assert_that(envelope.subtype).is_equal_to("success")
    assert_that((envelope.input_tokens, envelope.output_tokens)).is_equal_to((10, 5))
    assert_that(envelope.total_cost_usd).is_equal_to(0.25)
    assert_that(envelope.session_id).is_equal_to("sess-1")
    assert_that(envelope.num_turns).is_equal_to(3)
    assert_that(envelope.model_usage).is_equal_to({"claude-x": {"inputTokens": 10}})
    assert_that(envelope.raw["result"]).is_equal_to("hello")


def test_a_minimal_envelope_reads_absent_fields_as_empty() -> None:
    """Nothing but ``result`` is required; the rest read as zero or None."""
    envelope = decode_envelope('{"result": "ok"}', provider=_PROVIDER)

    assert_that(envelope.result).is_equal_to("ok")
    assert_that((envelope.input_tokens, envelope.output_tokens)).is_equal_to((0, 0))
    assert_that(envelope.total_cost_usd).is_none()
    assert_that(envelope.session_id).is_none()
    assert_that(envelope.num_turns).is_none()
    assert_that(envelope.subtype).is_none()


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"result": {"summary": "ok"}}, '{"summary": "ok"}'),
        ({"result": ["a", "b"]}, '["a", "b"]'),
        ({"result": None}, ""),
        ({}, ""),
        ({"result": "ignored", "structured_output": {"k": 1}}, '{"k": 1}'),
    ],
)
def test_result_is_serialised_and_structured_output_wins(
    payload: dict[str, object],
    expected: str,
) -> None:
    """An object or array result is JSON text; ``structured_output`` replaces it."""
    envelope = decode_envelope(json.dumps(payload), provider=_PROVIDER)

    assert_that(envelope.result).is_equal_to(expected)


@pytest.mark.parametrize(
    ("raw", "kind", "fragment_part"),
    [
        ("", CliEnvelopeErrorKind.NOT_JSON, ""),
        ("   \n ", CliEnvelopeErrorKind.NOT_JSON, ""),
        ("Here are my findings.", CliEnvelopeErrorKind.NOT_JSON, "findings"),
        ('{"result": "cut off', CliEnvelopeErrorKind.TRUNCATED, "cut off"),
        (
            '{"result": "ok", "usage": {"input_tokens": 1',
            CliEnvelopeErrorKind.TRUNCATED,
            "input_tokens",
        ),
        ('["a", "b"]', CliEnvelopeErrorKind.NOT_OBJECT, '["a", "b"]'),
        ('"just a string"', CliEnvelopeErrorKind.NOT_OBJECT, "just a string"),
        ("42", CliEnvelopeErrorKind.NOT_OBJECT, "42"),
    ],
)
def test_undecodable_text_is_classified(
    raw: str,
    kind: CliEnvelopeErrorKind,
    fragment_part: str,
) -> None:
    """Blank, prose, cut-off JSON and non-object JSON each get their own kind."""
    with pytest.raises(CliEnvelopeError) as excinfo:
        decode_envelope(raw, provider=_PROVIDER)

    assert_that(excinfo.value.kind).is_equal_to(kind)
    assert_that(excinfo.value.fragment).contains(fragment_part)
    assert_that(str(excinfo.value)).starts_with(f"{_PROVIDER} CLI")


def test_prose_with_an_unbalanced_bracket_is_not_called_truncated() -> None:
    """The truncation heuristic is only applied to JSON-looking text."""
    with pytest.raises(CliEnvelopeError) as excinfo:
        decode_envelope("See foo[ for details", provider=_PROVIDER)

    assert_that(excinfo.value.kind).is_equal_to(CliEnvelopeErrorKind.NOT_JSON)


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({"result": "ok", "usage": "lots"}, "usage"),
        ({"result": "ok", "usage": {"input_tokens": "many"}}, "usage.input_tokens"),
        ({"result": "ok", "usage": {"input_tokens": True}}, "usage.input_tokens"),
        ({"result": "ok", "usage": {"outputTokens": -1}}, "usage.outputTokens"),
        ({"result": "ok", "usage": {"output_tokens": 1.5}}, "usage.output_tokens"),
        ({"result": "ok", "total_cost_usd": "free"}, "total_cost_usd"),
        ({"result": "ok", "total_cost_usd": True}, "total_cost_usd"),
        ({"result": "ok", "total_cost_usd": -0.1}, "total_cost_usd"),
        ({"result": "ok", "subtype": 1}, "subtype"),
        ({"result": 12}, "result"),
        ({"result": True}, "result"),
    ],
)
def test_a_wrong_typed_field_is_a_schema_error_naming_the_field(
    payload: dict[str, object],
    field: str,
) -> None:
    """A well-formed object with a wrong-typed field names the field and value."""
    with pytest.raises(CliEnvelopeError) as excinfo:
        decode_envelope(json.dumps(payload), provider=_PROVIDER)

    assert_that(excinfo.value.kind).is_equal_to(CliEnvelopeErrorKind.SCHEMA)
    assert_that(str(excinfo.value)).contains(repr(field))


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"usage": {"input_tokens": 12.0, "output_tokens": "7"}}, (12, 7)),
        ({"usage": {"inputTokens": " 3 ", "outputTokens": 0.0}}, (3, 0)),
    ],
)
def test_counters_keep_the_old_int_coercion(
    payload: dict[str, object],
    expected: tuple[int, int],
) -> None:
    """An integral float or a digit string was a count under ``int()`` and still is."""
    envelope = decode_envelope(
        json.dumps({"result": "ok", **payload}),
        provider=_PROVIDER,
    )

    assert_that((envelope.input_tokens, envelope.output_tokens)).is_equal_to(expected)


def test_a_numeric_string_cost_is_still_a_price() -> None:
    """``float()`` accepted it before; a quoted number is a reported cost."""
    envelope = decode_envelope(
        '{"result": "ok", "total_cost_usd": "0.25"}',
        provider=_PROVIDER,
    )

    assert_that(envelope.total_cost_usd).is_equal_to(0.25)


@pytest.mark.parametrize("raw_session", [7, True, ["a"], "   "])
def test_a_session_id_that_is_not_a_string_reads_as_unknown(
    raw_session: object,
) -> None:
    """``session_id`` only feeds session reuse, so a bad value is None, not a failure."""
    envelope = decode_envelope(
        json.dumps({"result": "ok", "session_id": raw_session}),
        provider=_PROVIDER,
    )

    assert_that(envelope.session_id).is_none()


def test_a_closed_document_with_junk_after_it_is_not_truncated() -> None:
    """``{...}x`` closed; the failure is extra data, not a cut-off stream."""
    with pytest.raises(CliEnvelopeError) as excinfo:
        decode_envelope('{"result": "ok"}x', provider=_PROVIDER)

    assert_that(excinfo.value.kind).is_equal_to(CliEnvelopeErrorKind.NOT_JSON)
    assert_that(str(excinfo.value)).does_not_contain("truncated")


def test_decode_usage_keeps_the_defaults_for_a_missing_counter() -> None:
    """A partial block updates only the counter it carries."""
    counters = decode_usage({"input_tokens": 9}, provider=_PROVIDER, defaults=(1, 5))

    assert_that(counters).is_equal_to((9, 5))


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({"usage": {"input_tokens": "\u00b2"}}, "usage.input_tokens"),
        ({"usage": {"input_tokens": "-3"}}, "usage.input_tokens"),
        ({"total_cost_usd": 10**400}, "total_cost_usd"),
        ({"total_cost_usd": "1e999"}, "total_cost_usd"),
    ],
)
def test_a_value_the_conversion_rejects_is_a_schema_error_not_a_bare_exception(
    payload: dict[str, object],
    field: str,
) -> None:
    """A superscript digit passes ``isdigit``; a huge integer overflows ``float``."""
    with pytest.raises(CliEnvelopeError) as excinfo:
        decode_envelope(json.dumps({"result": "ok", **payload}), provider=_PROVIDER)

    assert_that(excinfo.value.kind).is_equal_to(CliEnvelopeErrorKind.SCHEMA)
    assert_that(str(excinfo.value)).contains(repr(field))


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_a_non_finite_cost_is_a_schema_error(literal: str) -> None:
    """``json.loads`` accepts the non-finite literals; a price is never one."""
    with pytest.raises(CliEnvelopeError) as excinfo:
        decode_envelope(
            f'{{"result": "ok", "total_cost_usd": {literal}}}',
            provider=_PROVIDER,
        )

    assert_that(excinfo.value.kind).is_equal_to(CliEnvelopeErrorKind.SCHEMA)


@pytest.mark.parametrize("raw_turns", [True, -1, "3", 2.0, None])
def test_a_turn_count_that_is_not_one_reads_as_unknown(raw_turns: object) -> None:
    """``num_turns`` is informational, so a bad value is None, not a failure."""
    envelope = decode_envelope(
        json.dumps({"result": "ok", "num_turns": raw_turns}),
        provider=_PROVIDER,
    )

    assert_that(envelope.num_turns).is_none()


def test_an_error_envelope_keeps_its_markers_over_a_malformed_usage() -> None:
    """Error precedence: the cause and markers survive a usage block of the wrong type."""
    raw = json.dumps(
        {
            "is_error": True,
            "subtype": "error",
            "result": "rate limited",
            "terminal_reason": "api_error",
            "api_error_status": 429,
            "usage": "n/a",
            "total_cost_usd": "n/a",
        },
    )

    envelope = decode_envelope(raw, provider=_PROVIDER)

    assert_that(envelope.is_error).is_true()
    assert_that(envelope.result).is_equal_to("rate limited")
    assert_that(dict(envelope.error_markers)).is_equal_to(
        {"terminal_reason": "api_error", "api_error_status": 429},
    )
    assert_that((envelope.input_tokens, envelope.output_tokens)).is_equal_to((0, 0))


def test_a_subtype_of_error_alone_marks_the_envelope_as_an_error() -> None:
    """``subtype: error`` without ``is_error`` is still an error envelope."""
    envelope = decode_envelope(
        '{"subtype": "error", "result": "boom"}',
        provider=_PROVIDER,
    )

    assert_that(envelope.is_error).is_true()
    assert_that(envelope.error_markers).is_empty()


def test_the_fragment_is_bounded_sanitised_and_redacted() -> None:
    """The quoted span never carries the whole output, escapes, or a secret."""
    secret = "sk-ant-api03-" + "a" * 90
    raw = "\x1b[31mnot json " + secret + " " + "x" * 5000

    with pytest.raises(CliEnvelopeError) as excinfo:
        decode_envelope(raw, provider=_PROVIDER)

    fragment = excinfo.value.fragment
    assert_that(len(fragment)).is_less_than_or_equal_to(200)
    assert_that(fragment).does_not_contain("\x1b")
    assert_that(fragment).does_not_contain(secret)
    assert_that(str(excinfo.value)).does_not_contain(secret)


def test_the_error_is_a_provider_error_for_the_classifier() -> None:
    """The classification kind downstream must not change."""
    with pytest.raises(AIProviderError):
        decode_envelope("[]", provider=_PROVIDER)


# --- decode_usage ----------------------------------------------------------------


def test_decode_usage_reads_zeros_when_absent() -> None:
    """A missing block is two zero counters, not an error."""
    assert_that(decode_usage(None, provider=_PROVIDER)).is_equal_to((0, 0))


def test_decode_usage_rejects_a_non_object() -> None:
    """A usage value that is not an object is a schema error naming ``usage``."""
    with pytest.raises(CliEnvelopeError, match="'usage'"):
        decode_usage([1, 2], provider=_PROVIDER)


# --- decode_envelope_or_prose ---------------------------------------------------


@pytest.fixture()
def _isolated_captures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep persisted raw responses inside the test's temporary directory."""
    monkeypatch.chdir(tmp_path)


@pytest.mark.usefixtures("_isolated_captures")
def test_prose_is_recovered_as_a_string() -> None:
    """A non-JSON answer is handed back as prose, stripped (#1853)."""
    decoded = decode_envelope_or_prose("  Findings follow.\n", provider=_PROVIDER)

    assert_that(decoded).is_equal_to("Findings follow.")


@pytest.mark.usefixtures("_isolated_captures")
def test_truncated_json_is_recovered_as_prose_too() -> None:
    """JSON cut short is handed back as text for the review layer to mine."""
    decoded = decode_envelope_or_prose('{"result": "cut', provider=_PROVIDER)

    assert_that(decoded).is_equal_to('{"result": "cut')


@pytest.mark.usefixtures("_isolated_captures")
@pytest.mark.parametrize(
    ("raw", "kind"),
    [
        ("", CliEnvelopeErrorKind.NOT_JSON),
        ("[1]", CliEnvelopeErrorKind.NOT_OBJECT),
        ('{"result": "ok", "usage": 1}', CliEnvelopeErrorKind.SCHEMA),
    ],
)
def test_unrecoverable_output_raises_with_the_evidence_block(
    raw: str,
    kind: CliEnvelopeErrorKind,
) -> None:
    """Blank, non-object and schema-broken output carry the full raw evidence."""
    with pytest.raises(CliEnvelopeError) as excinfo:
        decode_envelope_or_prose(raw, provider=_PROVIDER)

    assert_that(excinfo.value.kind).is_equal_to(kind)
    assert_that(str(excinfo.value)).contains("Full raw output")


# --- decode_jsonl_events ---------------------------------------------------------


def test_jsonl_events_skip_chatter_and_non_objects() -> None:
    """Only object lines are events; prose and scalars in the middle are skipped."""
    raw = '{"type": "a"}\nstarting...\n[1, 2]\n{"type": "b"}\n'

    stream = decode_jsonl_events(raw, provider=_PROVIDER)

    assert_that([e["type"] for e in stream.events]).is_equal_to(["a", "b"])
    assert_that(stream.unparsed_tail).is_none()


def test_a_complete_non_object_final_line_is_not_a_tail() -> None:
    """A trailing array or scalar is valid JSON: not an event, not a cut-off one."""
    stream = decode_jsonl_events('{"type": "a"}\n[1, 2]\n', provider=_PROVIDER)

    assert_that(len(stream.events)).is_equal_to(1)
    assert_that(stream.unparsed_tail).is_none()


def test_jsonl_events_report_an_undecoded_tail() -> None:
    """The last line is reported when it did not decode."""
    raw = '{"type": "a"}\n{"type": "b", "item": {"te'

    stream = decode_jsonl_events(raw, provider=_PROVIDER)

    assert_that(len(stream.events)).is_equal_to(1)
    assert_that(stream.unparsed_tail).is_equal_to('{"type": "b", "item": {"te')


def test_a_json_looking_tail_is_a_truncation_and_chatter_is_not() -> None:
    """A cut-off event is reported; the binary's own trailing prose is not."""
    cut = truncated_tail_error(provider=_PROVIDER, tail='{"type": "b", "ite')
    chatter = truncated_tail_error(provider=_PROVIDER, tail="done.")

    assert_that(cut).is_instance_of(CliEnvelopeError)
    assert_that(cut.kind if cut else None).is_equal_to(CliEnvelopeErrorKind.TRUNCATED)
    assert_that(chatter).is_none()


# --- adoption by every CLI transport ------------------------------------------------


@pytest.fixture()
def _binaries_on_path() -> Iterator[None]:
    """Patch every CLI binary lookup so each provider constructs."""
    with (
        patch(
            "lintro.ai.providers.anthropic.provider._find_claude",
            return_value="/usr/local/bin/claude",
        ),
        patch(
            "lintro.ai.providers.cursor.provider._find_agent",
            return_value="/usr/local/bin/agent",
        ),
        patch(
            "lintro.ai.providers.openai.provider._find_codex",
            return_value="/usr/local/bin/codex",
        ),
    ):
        yield


def _provider(name: str) -> BaseAIProvider:
    """Return one CLI-transport provider by label.

    Args:
        name: ``anthropic``, ``cursor`` or ``openai``.

    Returns:
        The provider instance.
    """
    if name == "anthropic":
        return AnthropicProvider(transport=AITransport.CLI, cli_bare=CliBareMode.NEVER)
    if name == "cursor":
        return CursorProvider(transport=AITransport.CLI, cursor_trust_workspace=True)
    return OpenAIProvider(transport=AITransport.CLI)


def _completed(stdout: str) -> subprocess.CompletedProcess[str]:
    """Return a clean-exit completed process carrying *stdout*.

    Args:
        stdout: Standard output text.

    Returns:
        A zero-exit completed process.
    """
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


@pytest.mark.usefixtures("_binaries_on_path", "_isolated_captures")
@pytest.mark.parametrize("name", ["anthropic", "cursor", "openai"])
@pytest.mark.parametrize(
    ("stdout", "kind", "fragment_part"),
    [
        ('["not", "an", "object"]', CliEnvelopeErrorKind.NOT_OBJECT, "not"),
        ('{"result": "ok", "usage": "n/a"}', CliEnvelopeErrorKind.SCHEMA, "n/a"),
        (
            '{"result": "ok", "usage": {"input_tokens": "many"}}',
            CliEnvelopeErrorKind.SCHEMA,
            "many",
        ),
    ],
)
async def test_every_cli_transport_maps_a_malformed_envelope_to_the_error(
    name: str,
    stdout: str,
    kind: CliEnvelopeErrorKind,
    fragment_part: str,
) -> None:
    """The same malformed envelope is the same ``AIProviderError`` on every CLI."""
    provider = _provider(name)
    with patch_cli_exec(help_text=CLAUDE_HELP) as mock_run:
        mock_run.return_value = _completed(stdout)
        with pytest.raises(AIProviderError) as excinfo:
            await provider.complete("Review this diff")

    error = excinfo.value
    assert_that(error).is_instance_of(CliEnvelopeError)
    assert_that(getattr(error, "kind", None)).is_equal_to(kind)
    assert_that(getattr(error, "fragment", "")).contains(fragment_part)
    assert_that(str(error)).contains("Full raw output")


@pytest.mark.usefixtures("_binaries_on_path", "_isolated_captures")
async def test_codex_reports_a_stream_cut_mid_event_as_truncated() -> None:
    """A JSONL stream that stops inside an event, with no answer, is truncated."""
    provider = _provider("openai")
    stdout = '{"type": "thread.started"}\n{"type": "item.completed", "item": {"ty'
    with patch_cli_exec() as mock_run:
        mock_run.return_value = _completed(stdout)
        with pytest.raises(CliEnvelopeError) as excinfo:
            await provider.complete("Review this diff")

    assert_that(excinfo.value.kind).is_equal_to(CliEnvelopeErrorKind.TRUNCATED)
    assert_that(excinfo.value.fragment).contains("item.completed")


@pytest.mark.usefixtures("_binaries_on_path", "_isolated_captures")
async def test_codex_turn_usage_of_the_wrong_type_is_a_schema_error() -> None:
    """The JSONL ``turn.completed`` usage block is validated like the envelope's."""
    provider = _provider("openai")
    stdout = "\n".join(
        [
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": "hi"},
                },
            ),
            json.dumps({"type": "turn.completed", "usage": {"input_tokens": "many"}}),
        ],
    )
    with patch_cli_exec() as mock_run:
        mock_run.return_value = _completed(stdout)
        with pytest.raises(CliEnvelopeError, match="'usage.input_tokens'"):
            await provider.complete("Review this diff")


@pytest.mark.usefixtures("_binaries_on_path", "_isolated_captures")
async def test_codex_partial_turn_usage_keeps_the_other_counter() -> None:
    """A second usage block naming one counter does not zero the other."""
    provider = _provider("openai")
    stdout = "\n".join(
        [
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {"input_tokens": 3, "output_tokens": 8},
                },
            ),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": "hi"},
                },
            ),
            json.dumps({"type": "turn.completed", "usage": {"input_tokens": 5}}),
        ],
    )
    with patch_cli_exec() as mock_run:
        mock_run.return_value = _completed(stdout)
        response = await provider.complete("Review this diff")

    assert_that((response.input_tokens, response.output_tokens)).is_equal_to((5, 8))


@pytest.mark.usefixtures("_binaries_on_path", "_isolated_captures")
async def test_codex_single_object_error_envelope_is_raised_not_answered() -> None:
    """The single-object fallback raises on an error envelope like the other CLIs."""
    provider = _provider("openai")
    stdout = json.dumps(
        {"is_error": True, "subtype": "error", "result": "quota exhausted"},
    )
    with patch_cli_exec() as mock_run:
        mock_run.return_value = _completed(stdout)
        with pytest.raises(AIProviderError, match="reported error: quota exhausted"):
            await provider.complete("Review this diff")


@pytest.mark.usefixtures("_binaries_on_path", "_isolated_captures")
async def test_codex_still_skips_chatter_lines_between_events() -> None:
    """A non-JSON line in the middle of the stream changes nothing (#2537 shape)."""
    provider = _provider("openai")
    stdout = "\n".join(
        [
            "Reading prompt from stdin...",
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": "hi"},
                },
            ),
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {"input_tokens": 3, "output_tokens": 1},
                },
            ),
            "done.",
        ],
    )
    with patch_cli_exec() as mock_run:
        mock_run.return_value = _completed(stdout)
        response = await provider.complete("Review this diff")

    assert_that(response.content).is_equal_to("hi")
    assert_that((response.input_tokens, response.output_tokens)).is_equal_to((3, 1))
