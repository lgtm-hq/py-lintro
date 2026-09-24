# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""Tests for the provider smoke's rows and empty-body diagnostic (#2748)."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest
from assertpy import assert_that

from lintro.ai import providers as providers_module

_SMOKE_DIR = (
    Path(__file__).resolve().parents[2] / "scripts" / "ci" / "ai_provider_smoke"
)
_TABLE = _SMOKE_DIR / "providers.json"

#: Stand-in for a live credential value, assembled at runtime so no
#: credential-shaped literal is committed.
_FAKE_CREDENTIAL = "sk-" + "diag" + "-" + ("0" * 24)


def _load(name: str, path: Path) -> ModuleType:
    """Load a script as an importable module.

    Args:
        name: Module name to register it under.
        path: Script path.

    Returns:
        The loaded module.

    Raises:
        RuntimeError: When the module spec cannot be created.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        msg = f"Unable to load module from {path}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def smoke() -> ModuleType:
    """Return the smoke runner module.

    Returns:
        The loaded ``run_smoke`` module.
    """
    return _load("ai_provider_run_smoke_diag", _SMOKE_DIR / "run_smoke.py")


@pytest.fixture
def capture() -> ModuleType:
    """Return the HTTP capture module.

    Returns:
        The loaded ``http_capture`` module.
    """
    return _load("ai_provider_http_capture_diag", _SMOKE_DIR / "http_capture.py")


def _row(**overrides: Any) -> dict[str, Any]:
    """Return a valid table row with optional overrides.

    Args:
        **overrides: Fields to replace.

    Returns:
        A row mapping.
    """
    row: dict[str, Any] = {
        "name": "example-api",
        "protocol": "anthropic",
        "base_url": "https://api.example.com",
        "key_env": "EXAMPLE_CREDENTIAL",
        "model": "example-model",
    }
    row.update(overrides)
    return row


def _table(tmp_path: Path, rows: list[dict[str, Any]]) -> Path:
    """Write a provider table for a validation test.

    Args:
        tmp_path: Directory to write into.
        rows: Rows to place under ``providers``.

    Returns:
        The written path.
    """
    path = tmp_path / "providers.json"
    path.write_text(json.dumps({"providers": rows}), encoding="utf-8")
    return path


def _exchange(
    capture: ModuleType,
    *,
    body: Any,
    status: int = 200,
    headers: dict[str, str] | None = None,
) -> Any:
    """Build a captured exchange from a body.

    Args:
        capture: The loaded capture module.
        body: A mapping to encode as JSON, or raw bytes.
        status: HTTP status.
        headers: Allowlisted headers.

    Returns:
        The exchange.
    """
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    return capture.CapturedExchange(status=status, headers=headers or {}, body=raw)


# --- the rows ---------------------------------------------------------------


def test_the_gateway_rows_pin_the_models_the_project_uses(smoke: ModuleType) -> None:
    """kimi-api and zai-api run the owner-ruled models with room to think.

    Both models reason before every answer; a row left at the 16-token default
    would spend the cap on that and report an empty response.

    Args:
        smoke: The loaded smoke runner module.
    """
    rows = {row.name: row for row in smoke.load_table(path=_TABLE)}

    assert_that(rows["kimi-api"].model).is_equal_to("kimi-k3")
    assert_that(rows["zai-api"].model).is_equal_to("glm-5.3-flash")
    assert_that(rows["kimi-api"].max_tokens).is_equal_to(1024)
    assert_that(rows["zai-api"].max_tokens).is_equal_to(1024)
    assert_that(rows["anthropic-api"].max_tokens).is_equal_to(smoke.SMOKE_MAX_TOKENS)
    assert_that(rows["openai-api"].max_tokens).is_equal_to(smoke.SMOKE_MAX_TOKENS)


@pytest.mark.parametrize("max_tokens", [0, -1, 4097, "1024", True, 1.5, None])
def test_a_row_max_tokens_outside_the_bounds_fails_the_table(
    smoke: ModuleType,
    tmp_path: Path,
    max_tokens: Any,
) -> None:
    """Only an integer in 1..4096 is a usable cap; ``true`` is not 1.

    Args:
        smoke: The loaded smoke runner module.
        tmp_path: Temporary directory for the table.
        max_tokens: The value under test.
    """
    table = _table(tmp_path, [_row(max_tokens=max_tokens)])
    with pytest.raises(ValueError, match="max_tokens"):
        smoke.load_table(path=table)


def test_a_row_without_max_tokens_keeps_the_one_word_cap(
    smoke: ModuleType,
    tmp_path: Path,
) -> None:
    """The field is optional and defaults to the one-word cap.

    Args:
        smoke: The loaded smoke runner module.
        tmp_path: Temporary directory for the table.
    """
    (row,) = smoke.load_table(path=_table(tmp_path, [_row()]))
    (capped,) = smoke.load_table(path=_table(tmp_path, [_row(max_tokens=4096)]))

    assert_that(row.max_tokens).is_equal_to(smoke.SMOKE_MAX_TOKENS)
    assert_that(capped.max_tokens).is_equal_to(4096)


def test_the_row_cap_reaches_the_config_and_the_call(
    smoke: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The row's cap is what lintro's provider is built with and called with.

    AIConfig clamps a per-call cap to its own, so the row has to reach both.

    Args:
        smoke: The loaded smoke runner module.
        monkeypatch: Provider-factory patcher.
    """
    captured: dict[str, Any] = {}

    class _StubProvider:
        async def complete(self, _prompt: str, **kwargs: Any) -> Any:
            captured["call"] = kwargs
            return type("R", (), {"content": "pong"})()

        async def aclose(self) -> None:
            return None

    def _get_provider(config: Any) -> Any:
        captured["config"] = config
        return _StubProvider()

    monkeypatch.setattr(providers_module, "get_provider", _get_provider)
    row = next(r for r in smoke.load_table(path=_TABLE) if r.name == "kimi-api")

    asyncio.run(smoke._complete(row=row, credential_env="LINTRO_SMOKE_CREDENTIAL"))

    assert_that(captured["config"].max_tokens).is_equal_to(1024)
    assert_that(captured["call"]["max_tokens"]).is_equal_to(1024)


# --- the capture ------------------------------------------------------------


def test_the_capture_records_the_sdk_exchange_and_restores_httpx(
    capture: ModuleType,
) -> None:
    """The response the client received is recorded; send is put back.

    Args:
        capture: The loaded capture module.
    """
    original = httpx.AsyncClient.send

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "content-type": "application/json",
                "set-cookie": "session=secret",
                "x-ratelimit-remaining-requests": "0",
            },
            content=b'{"content": []}',
        )

    async def _call() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as c:
            await c.post("https://api.example.com/v1/messages", json={})

    with capture.capture_http() as recorded:
        asyncio.run(_call())

    assert_that(httpx.AsyncClient.send).is_equal_to(original)
    exchange = recorded.last()
    assert_that(exchange.status).is_equal_to(200)
    assert_that(exchange.body).is_equal_to(b'{"content": []}')
    assert_that(exchange.headers).contains_key(
        "content-type",
        "x-ratelimit-remaining-requests",
    )
    assert_that(exchange.headers).does_not_contain_key("set-cookie")


def test_the_capture_restores_httpx_when_the_call_raises(capture: ModuleType) -> None:
    """A failing call must not leave the wrapper installed.

    Args:
        capture: The loaded capture module.
    """
    original = httpx.AsyncClient.send

    def _fail() -> None:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError), capture.capture_http():
        _fail()

    assert_that(httpx.AsyncClient.send).is_equal_to(original)


def test_the_capture_watches_httpx2_the_openai_sdk_sends_through(
    capture: ModuleType,
) -> None:
    """Openai 3.x sends through ``httpx2``, not ``httpx``; both are watched.

    Without this the openai-protocol rows (openai-api, zai-api) record nothing:
    an empty answer loses its status and body, and a pass reports no model.

    Args:
        capture: The loaded capture module.
    """
    httpx2 = pytest.importorskip("httpx2")
    originals = {"httpx": httpx.AsyncClient.send, "httpx2": httpx2.AsyncClient.send}

    def _handler(_request: Any) -> Any:
        return httpx2.Response(429, headers={"retry-after": "30"}, content=b"{}")

    async def _call() -> None:
        transport = httpx2.MockTransport(_handler)
        async with httpx2.AsyncClient(transport=transport) as client:
            await client.post("https://api.example.com/v1/chat/completions", json={})

    with capture.capture_http() as recorded:
        assert_that(httpx.AsyncClient.send).is_not_equal_to(originals["httpx"])
        assert_that(httpx2.AsyncClient.send).is_not_equal_to(originals["httpx2"])
        asyncio.run(_call())

    assert_that(recorded.transports).contains("httpx", "httpx2")
    assert_that(httpx.AsyncClient.send).is_equal_to(originals["httpx"])
    assert_that(httpx2.AsyncClient.send).is_equal_to(originals["httpx2"])
    exchange = recorded.last()
    assert_that(exchange.transport).is_equal_to("httpx2")
    assert_that(exchange.status).is_equal_to(429)
    assert_that(exchange.headers).contains_entry({"retry-after": "30"})


def test_an_unread_body_is_not_reported_as_empty(capture: ModuleType) -> None:
    """A streamed response the SDK never read is a different finding.

    Args:
        capture: The loaded capture module.
    """
    exchange = capture.CapturedExchange(status=200, body=None)

    text = capture.describe_exchange(exchange, redact=lambda t: t)

    assert_that(text).contains("body: not read by the SDK")
    assert_that(text).does_not_contain("body: empty")
    assert_that(capture.usage_of(exchange)).is_equal_to(("?", 0, 0))


def test_no_exchange_is_described_as_such(capture: ModuleType) -> None:
    """A call that never got a response says so rather than inventing one.

    Args:
        capture: The loaded capture module.
    """
    assert_that(capture.HttpCapture().last()).is_none()
    text = capture.describe_exchange(None, redact=lambda t: t)
    assert_that(text).contains("no response was recorded")


# --- the description --------------------------------------------------------


def test_a_reasoning_only_anthropic_answer_names_the_cut_off(
    capture: ModuleType,
) -> None:
    """A thinking block and no text, stopped on the cap, reads as exactly that.

    This is the shape an always-thinking model returns under a 16-token cap.

    Args:
        capture: The loaded capture module.
    """
    exchange = _exchange(
        capture,
        body={
            "model": "glm-5.3-flash",
            "stop_reason": "max_tokens",
            "content": [{"type": "thinking", "thinking": "The user wants pong"}],
            "usage": {
                "input_tokens": 12,
                "output_tokens": 16,
                "output_tokens_details": {"thinking_tokens": 16},
            },
        },
        headers={"content-type": "application/json"},
    )

    text = capture.describe_exchange(exchange, redact=lambda t: t)

    assert_that(text).contains("HTTP status: 200")
    assert_that(text).contains("header content-type: application/json")
    assert_that(text).contains("stop_reason: max_tokens")
    assert_that(text).contains("content blocks: thinking(19 chars)")
    assert_that(text).contains("output tokens: 16 (thinking 16)")


def test_an_empty_anthropic_envelope_lists_no_blocks(capture: ModuleType) -> None:
    """An empty 200 envelope is told apart from a reasoning-only one.

    Args:
        capture: The loaded capture module.
    """
    exchange = _exchange(
        capture,
        body={"stop_reason": "end_turn", "content": [], "usage": {"output_tokens": 0}},
    )

    text = capture.describe_exchange(exchange, redact=lambda t: t)

    assert_that(text).contains("stop_reason: end_turn")
    assert_that(text).contains("content blocks: none")
    assert_that(text).contains("(thinking n/a)")


def test_a_reasoning_only_openai_answer_names_the_split(capture: ModuleType) -> None:
    """The Chat Completions shape reports finish reason and reasoning tokens.

    Args:
        capture: The loaded capture module.
    """
    exchange = _exchange(
        capture,
        body={
            "choices": [
                {
                    "finish_reason": "length",
                    "message": {"content": "", "reasoning_content": "thinking..."},
                },
            ],
            "usage": {
                "completion_tokens": 64,
                "completion_tokens_details": {"reasoning_tokens": 61},
            },
        },
    )

    text = capture.describe_exchange(exchange, redact=lambda t: t)

    assert_that(text).contains("finish_reason: length")
    assert_that(text).contains("content(0 chars), reasoning_content(11 chars)")
    assert_that(text).contains("output tokens: 64 (reasoning 61)")


def test_a_quota_page_shows_its_status_and_first_bytes(capture: ModuleType) -> None:
    """A non-JSON page is shown by status and excerpt, capped at 500 bytes.

    Args:
        capture: The loaded capture module.
    """
    page = b"<html>Insufficient balance</html>" + b"x" * 1000
    exchange = _exchange(
        capture,
        body=page,
        status=429,
        headers={"retry-after": "30"},
    )

    text = capture.describe_exchange(exchange, redact=lambda t: t)

    assert_that(text).contains("HTTP status: 429")
    assert_that(text).contains("header retry-after: 30")
    assert_that(text).contains("body: not a completion envelope")
    assert_that(text).contains(f"body ({len(page)} bytes; first 500 bytes")
    assert_that(text).contains("Insufficient balance")
    assert_that(text).does_not_contain("x" * 500)


def test_a_bodiless_response_says_the_body_is_empty(capture: ModuleType) -> None:
    """A zero-byte body is named as empty, not as a malformed envelope.

    Args:
        capture: The loaded capture module.
    """
    text = capture.describe_exchange(_exchange(capture, body=b""), redact=lambda t: t)

    assert_that(text).contains("body: empty")
    assert_that(text).contains("body (0 bytes")


def test_the_body_excerpt_goes_through_the_callers_redaction(
    capture: ModuleType,
) -> None:
    """The excerpt is printed only after the smoke's own redaction.

    Args:
        capture: The loaded capture module.
    """
    exchange = _exchange(capture, body=f"denied for {_FAKE_CREDENTIAL}".encode())

    text = capture.describe_exchange(
        exchange,
        redact=lambda t: t.replace(_FAKE_CREDENTIAL, "[REDACTED]"),
    )

    assert_that(text).does_not_contain(_FAKE_CREDENTIAL)
    assert_that(text).contains("[REDACTED]")


def test_a_credential_straddling_the_excerpt_cut_leaves_no_fragment(
    capture: ModuleType,
) -> None:
    """Redaction runs on the whole body before the cut, not on the excerpt.

    Cut first, and the head of a key that crosses byte 500 survives as a
    fragment the redaction can no longer recognise as the key.

    Args:
        capture: The loaded capture module.
    """
    prefix = "p" * (capture.BODY_EXCERPT_BYTES - 10)
    exchange = _exchange(capture, body=f"{prefix}{_FAKE_CREDENTIAL} tail".encode())

    text = capture.describe_exchange(
        exchange,
        redact=lambda t: t.replace(_FAKE_CREDENTIAL, "[REDACTED]"),
    )

    assert_that(text).does_not_contain(_FAKE_CREDENTIAL[:10])
    assert_that(text).contains("[REDACTED]")


@pytest.mark.parametrize(
    ("body", "headers"),
    [
        ({"content": []}, {"x-request-id": _FAKE_CREDENTIAL}),
        ({"stop_reason": _FAKE_CREDENTIAL, "content": []}, {}),
        ({"content": [{"type": _FAKE_CREDENTIAL, "text": "x"}]}, {}),
        ({"choices": [{"finish_reason": _FAKE_CREDENTIAL}]}, {}),
        ({"content": [], "usage": {"output_tokens": _FAKE_CREDENTIAL}}, {}),
    ],
)
def test_a_credential_in_any_rendered_field_is_redacted(
    capture: ModuleType,
    smoke: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    body: dict[str, Any],
    headers: dict[str, str],
) -> None:
    """Headers and parsed fields come from the gateway too, not only the body.

    Uses the smoke's own ``_safe_detail`` as the redaction, so the test pins
    what actually reaches the log, the summary and the error file.

    Args:
        capture: The loaded capture module.
        smoke: The loaded smoke runner module.
        monkeypatch: Environment patcher.
        body: An envelope carrying the credential in one field.
        headers: Allowlisted headers, possibly carrying the credential.
    """
    monkeypatch.setenv("LINTRO_SMOKE_CREDENTIAL", _FAKE_CREDENTIAL)
    exchange = _exchange(capture, body=body, headers=headers)

    text = capture.describe_exchange(
        exchange,
        redact=lambda t: smoke._safe_detail(t, env_name="LINTRO_SMOKE_CREDENTIAL"),
    )

    assert_that(text).does_not_contain(_FAKE_CREDENTIAL)
    assert_that(text).contains("HTTP status: 200")


def test_the_excerpt_is_capped_in_bytes_without_splitting_a_character(
    capture: ModuleType,
) -> None:
    """A multibyte body prints at most 500 bytes and no half character.

    Args:
        capture: The loaded capture module.
    """
    body = ("é" * 400).encode("utf-8")  # 800 bytes, 2 per character
    exchange = _exchange(capture, body=body)

    text = capture.describe_exchange(exchange, redact=lambda t: t)

    excerpt = text.rsplit("after redaction): ", 1)[1]
    assert_that(excerpt).is_equal_to(repr("é" * 250))
    assert_that(excerpt).does_not_contain("\\ufffd")


@pytest.mark.parametrize(
    "model",
    [_FAKE_CREDENTIAL, "model with spaces", ""],
)
def test_the_echoed_model_on_a_pass_is_redacted_or_withheld(
    smoke: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    model: str,
) -> None:
    """The gateway's model string is printed only if it is a model id.

    Args:
        smoke: The loaded smoke runner module.
        tmp_path: Temporary directory for the Actions files.
        monkeypatch: Environment and attribute patcher.
        capsys: Captured stdout.
        model: The model field the gateway echoes.
    """
    body = {"model": model, "usage": {"input_tokens": 1, "output_tokens": 1}}

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    async def _complete(**_kwargs: Any) -> str:
        async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as c:
            await c.post("https://api.example.com/v1/messages", json={})
        return "pong"

    summary = tmp_path / "summary"
    monkeypatch.setattr(smoke, "_complete", _complete)
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "output"))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("LINTRO_SMOKE_CREDENTIAL", _FAKE_CREDENTIAL)

    code = smoke.run_smoke(
        row=next(r for r in smoke.load_table(path=_TABLE) if r.name == "kimi-api"),
        credential_env="LINTRO_SMOKE_CREDENTIAL",
        error_file=None,
    )

    out = capsys.readouterr().out
    assert_that(code).is_equal_to(0)
    assert_that(out).contains("model ?,")
    assert_that(out).does_not_contain(_FAKE_CREDENTIAL)
    assert_that(summary.read_text(encoding="utf-8")).does_not_contain(
        _FAKE_CREDENTIAL,
    )


@pytest.mark.parametrize(
    "body",
    [
        {"content": "not a list", "usage": "n/a"},
        {"content": [], "usage": {"output_tokens_details": ["x"]}},
        {"choices": [], "usage": ["x"]},
        {"choices": ["x"], "usage": {"completion_tokens_details": "x"}},
        {"choices": [{"message": "x"}], "usage": None},
    ],
)
def test_a_malformed_envelope_is_described_not_raised(
    capture: ModuleType,
    body: dict[str, Any],
) -> None:
    """A gateway's odd shape must not stop the outcome files being written.

    Args:
        capture: The loaded capture module.
        body: A malformed envelope.
    """
    exchange = _exchange(capture, body=body)

    text = capture.describe_exchange(exchange, redact=lambda t: t)

    assert_that(text).contains("HTTP status: 200")
    assert_that(capture.usage_of(exchange)).is_equal_to(("?", 0, 0))


def test_usage_ignores_token_counts_that_are_not_integers(
    capture: ModuleType,
) -> None:
    """A string or boolean count reads as 0, not as a crash or as 1.

    Args:
        capture: The loaded capture module.
    """
    exchange = _exchange(
        capture,
        body={"model": "m", "usage": {"input_tokens": "9", "output_tokens": True}},
    )

    assert_that(capture.usage_of(exchange)).is_equal_to(("m", 0, 0))


def test_usage_reads_the_echoed_model_from_either_protocol(
    capture: ModuleType,
) -> None:
    """The success line's model and tokens come from the wire, both shapes.

    Args:
        capture: The loaded capture module.
    """
    anthropic = _exchange(
        capture,
        body={"model": "kimi-k3", "usage": {"input_tokens": 9, "output_tokens": 3}},
    )
    openai = _exchange(
        capture,
        body={
            "model": "gpt-4o-mini",
            "usage": {"prompt_tokens": 7, "completion_tokens": 2},
        },
    )

    assert_that(capture.usage_of(anthropic)).is_equal_to(("kimi-k3", 9, 3))
    assert_that(capture.usage_of(openai)).is_equal_to(("gpt-4o-mini", 7, 2))
    assert_that(capture.usage_of(None)).is_equal_to(("?", 0, 0))
    assert_that(capture.usage_of(_exchange(capture, body=b"<html>"))).is_equal_to(
        ("?", 0, 0),
    )


# --- end to end through run_smoke -------------------------------------------


_THINKING_ONLY: dict[str, dict[str, Any]] = {
    # zai-api runs on the OpenAI-compatible path: a Chat Completions envelope.
    "zai-api": {
        "model": "glm-5.3-flash",
        "choices": [
            {
                "finish_reason": "length",
                "message": {
                    "content": "",
                    "reasoning_content": f"key {_FAKE_CREDENTIAL}",
                },
            },
        ],
        "usage": {"prompt_tokens": 12, "completion_tokens": 1024},
    },
    # kimi-api runs on the Anthropic-compatible path: a Messages envelope.
    "kimi-api": {
        "model": "kimi-k3",
        "stop_reason": "max_tokens",
        "content": [{"type": "thinking", "thinking": f"key {_FAKE_CREDENTIAL}"}],
        "usage": {"input_tokens": 12, "output_tokens": 1024},
    },
}


@pytest.mark.parametrize(
    ("row_name", "expected_lines"),
    [
        ("zai-api", ["finish_reason: length", "reasoning_content("]),
        ("kimi-api", ["stop_reason: max_tokens", "content blocks: thinking("]),
    ],
)
def test_an_empty_answer_writes_the_exchange_to_the_error_file(
    smoke: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    row_name: str,
    expected_lines: list[str],
) -> None:
    """The tracker issue quotes what came back, not only that it was empty.

    Each gateway row gets the envelope its own protocol returns. The call is
    driven through a real ``httpx`` client so the capture sees a genuine
    response; the credential in the body must not survive.

    Args:
        smoke: The loaded smoke runner module.
        tmp_path: Temporary directory for the Actions files.
        monkeypatch: Environment and attribute patcher.
        row_name: The committed row under test.
        expected_lines: Diagnostic lines that row's envelope must produce.
    """
    body = _THINKING_ONLY[row_name]

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    async def _complete(**_kwargs: Any) -> str:
        async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as c:
            await c.post("https://api.example.com/v1/messages", json={})
        return ""

    monkeypatch.setattr(smoke, "_complete", _complete)
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "output"))
    monkeypatch.setenv("LINTRO_SMOKE_CREDENTIAL", _FAKE_CREDENTIAL)
    error_file = tmp_path / "smoke-error.md"

    code = smoke.run_smoke(
        row=next(r for r in smoke.load_table(path=_TABLE) if r.name == row_name),
        credential_env="LINTRO_SMOKE_CREDENTIAL",
        error_file=error_file,
    )

    recorded = error_file.read_text(encoding="utf-8")
    assert_that(code).is_equal_to(1)
    assert_that(recorded).contains("EmptyResponse")
    assert_that(recorded).contains("HTTP status: 200")
    for line in expected_lines:
        assert_that(recorded).contains(line)
    assert_that(recorded).does_not_contain(_FAKE_CREDENTIAL)


@pytest.mark.parametrize(
    ("protocol", "expected", "absent"),
    [
        ("anthropic", "stop_reason: end_turn", "finish_reason:"),
        ("openai", "finish_reason: None", "stop_reason:"),
        (None, "finish_reason: None", "stop_reason:"),
    ],
)
def test_the_row_protocol_breaks_a_tie_between_envelope_shapes(
    capture: ModuleType,
    protocol: str | None,
    expected: str,
    absent: str,
) -> None:
    """A body carrying both protocols' keys is read in the row's own terms.

    Args:
        capture: The loaded capture module.
        protocol: The row protocol passed as the hint.
        expected: A line the chosen shape must produce.
        absent: A line only the other shape would produce.
    """
    exchange = _exchange(
        capture,
        body={"choices": [], "content": [], "stop_reason": "end_turn"},
    )

    text = capture.describe_exchange(exchange, redact=lambda t: t, protocol=protocol)

    assert_that(text).contains(expected)
    assert_that(text).does_not_contain(absent)


@pytest.mark.parametrize(
    ("status", "headers", "body", "expected"),
    [
        (
            429,
            {"retry-after": "30", "x-ratelimit-remaining-requests": "0"},
            {"error": {"code": "1113", "message": "Insufficient balance"}},
            [
                "HTTP status: 429",
                "header retry-after: 30",
                "header x-ratelimit-remaining-requests: 0",
                "Insufficient balance",
            ],
        ),
        (
            401,
            {"x-request-id": "req-1"},
            {"error": {"message": "Authentication Failed"}},
            ["HTTP status: 401", "header x-request-id: req-1", "Authentication"],
        ),
    ],
)
def test_a_refused_call_writes_the_exchange_next_to_the_error(
    smoke: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    headers: dict[str, str],
    body: dict[str, Any],
    expected: list[str],
) -> None:
    """A non-2xx carries its headers to the tracker, not only the SDK's text.

    The z.ai 1113 refusal (#2748) surfaced as the SDK's exception text with
    status and body but without retry-after or the rate-limit counters, which
    are what tell a quota refusal from an outage.

    Args:
        smoke: The loaded smoke runner module.
        tmp_path: Temporary directory for the Actions files.
        monkeypatch: Environment and attribute patcher.
        status: HTTP status the gateway returns.
        headers: Response headers.
        body: Response body.
        expected: Lines the error file must carry.
    """

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, headers=headers, json=body)

    async def _complete(**_kwargs: Any) -> str:
        async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as c:
            response = await c.post("https://api.example.com/v1/messages", json={})
        msg = f"Error code: {response.status_code}"
        raise RuntimeError(msg)

    monkeypatch.setattr(smoke, "_complete", _complete)
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "output"))
    monkeypatch.setenv("LINTRO_SMOKE_CREDENTIAL", _FAKE_CREDENTIAL)
    error_file = tmp_path / "smoke-error.md"

    code = smoke.run_smoke(
        row=next(r for r in smoke.load_table(path=_TABLE) if r.name == "zai-api"),
        credential_env="LINTRO_SMOKE_CREDENTIAL",
        error_file=error_file,
    )

    recorded = error_file.read_text(encoding="utf-8")
    assert_that(code).is_equal_to(1)
    assert_that(recorded).contains(f"RuntimeError: Error code: {status}")
    for line in expected:
        assert_that(recorded).contains(line)


def test_a_pass_reports_the_echoed_model_latency_and_tokens(
    smoke: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The success line carries what the PR-body verification table needs.

    Args:
        smoke: The loaded smoke runner module.
        tmp_path: Temporary directory for the Actions files.
        monkeypatch: Environment and attribute patcher.
        capsys: Captured stdout.
    """
    body = {"model": "kimi-k3", "usage": {"input_tokens": 92, "output_tokens": 57}}

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    async def _complete(**_kwargs: Any) -> str:
        async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as c:
            await c.post("https://api.example.com/v1/messages", json={})
        return "pong"

    monkeypatch.setattr(smoke, "_complete", _complete)
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "output"))
    monkeypatch.setenv("LINTRO_SMOKE_CREDENTIAL", _FAKE_CREDENTIAL)

    code = smoke.run_smoke(
        row=next(r for r in smoke.load_table(path=_TABLE) if r.name == "kimi-api"),
        credential_env="LINTRO_SMOKE_CREDENTIAL",
        error_file=None,
    )

    out = capsys.readouterr().out
    assert_that(code).is_equal_to(0)
    assert_that(out).contains("kimi-api: ok")
    assert_that(out).contains("model kimi-k3")
    assert_that(out).contains("92 in / 57 out tokens")
