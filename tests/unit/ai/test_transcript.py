"""Tests for opt-in AI provider transcript NDJSON logging."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.ai.config import AIConfig
from lintro.ai.doctor_checks import check_ai_configuration
from lintro.ai.enums import AITransport
from lintro.ai.providers.cli_transport import CliTransport
from lintro.ai.registry import AIProvider
from lintro.ai.transcript import (
    ENV_TRANSCRIPT,
    TRANSCRIPT_DIR,
    TranscriptDirection,
    TranscriptWriter,
    clear_active_transcript,
    is_transcript_enabled,
    log_transcript_event,
    maybe_start_transcript,
    set_active_transcript,
)
from lintro.enums.tool_status import ToolStatus


@pytest.fixture(autouse=True)
def _reset_transcript_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Ensure each test starts without an active writer or env override."""
    clear_active_transcript()
    monkeypatch.delenv(ENV_TRANSCRIPT, raising=False)
    yield
    clear_active_transcript()
    monkeypatch.delenv(ENV_TRANSCRIPT, raising=False)


def _read_events(path: Path) -> list[dict[str, Any]]:
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line]
    return [json.loads(line) for line in lines]


def _make_cli_transport(**kwargs: object) -> CliTransport:
    """Build a concrete CliTransport for hook tests without a test class."""

    class _Concrete(CliTransport):
        def parse_stdout(self, stdout: str) -> str:
            return stdout

    return _Concrete(**kwargs)  # type: ignore[arg-type]


def test_disabled_by_default_writes_nothing(tmp_path: Path) -> None:
    """Default config does not create a transcript file."""
    writer = maybe_start_transcript(
        workspace_root=tmp_path,
        config_enabled=False,
    )
    assert_that(writer).is_none()
    assert_that(list((tmp_path / TRANSCRIPT_DIR).glob("*.ndjson"))).is_empty()

    log_transcript_event(
        provider="anthropic",
        transport="api",
        direction=TranscriptDirection.REQUEST,
        payload={"prompt": "hello"},
    )
    assert_that(list((tmp_path / TRANSCRIPT_DIR).glob("*.ndjson"))).is_empty()


def test_events_written_in_order(tmp_path: Path) -> None:
    """Request then response events appear in append order."""
    writer = TranscriptWriter(
        workspace_root=tmp_path,
        command="check",
        retention=10,
        enabled=True,
    )
    set_active_transcript(writer)

    log_transcript_event(
        provider="anthropic",
        transport="api",
        direction=TranscriptDirection.REQUEST,
        payload={"prompt": "one"},
    )
    log_transcript_event(
        provider="anthropic",
        transport="api",
        direction=TranscriptDirection.RESPONSE,
        payload={"content": "two"},
    )
    log_transcript_event(
        provider="anthropic",
        transport="api",
        direction=TranscriptDirection.EVENT,
        payload={"chunk": "three"},
    )

    assert_that(writer.path).is_not_none()
    assert writer.path is not None
    events = _read_events(writer.path)
    assert_that(events).is_length(3)
    assert_that([e["direction"] for e in events]).is_equal_to(
        ["request", "response", "event"],
    )
    assert_that(events[0]["payload"]["prompt"]).is_equal_to("one")
    assert_that(events[1]["payload"]["content"]).is_equal_to("two")
    assert_that(events[2]["payload"]["chunk"]).is_equal_to("three")
    for event in events:
        assert_that(event).contains_key(
            "ts",
            "provider",
            "transport",
            "direction",
            "payload",
        )


def test_planted_secret_is_redacted(tmp_path: Path) -> None:
    """Planted API-key material is redacted before write."""
    writer = TranscriptWriter(workspace_root=tmp_path, command="review", enabled=True)
    planted = "sk-abcdefghijklmnopqrstuvwxyz0123456789"
    writer.log(
        provider="openai",
        transport="api",
        direction=TranscriptDirection.REQUEST,
        payload={
            "messages": [{"role": "user", "content": f"key={planted}"}],
            "authorization": "Bearer should-not-appear",
            "api_key": "should-also-be-gone",
        },
    )

    assert writer.path is not None
    text = writer.path.read_text(encoding="utf-8")
    assert_that(text).contains("[REDACTED]")
    assert_that(text).does_not_contain(planted)
    assert_that(text).does_not_contain("should-not-appear")
    assert_that(text).does_not_contain("should-also-be-gone")


def test_retention_prunes_old_transcripts(tmp_path: Path) -> None:
    """Writer init keeps only the newest N transcript files."""
    import os
    import time

    directory = tmp_path / TRANSCRIPT_DIR
    directory.mkdir(parents=True)
    base = time.time() - 100
    for index in range(5):
        path = directory / f"2025010{index}T000000-old.ndjson"
        path.write_text("{}\n", encoding="utf-8")
        os.utime(path, (base + index, base + index))

    writer = TranscriptWriter(
        workspace_root=tmp_path,
        command="check",
        retention=3,
        enabled=True,
    )
    assert writer.path is not None
    remaining = sorted(directory.glob("*.ndjson"))
    assert_that(remaining).is_length(3)
    assert_that(remaining).contains(writer.path)


def test_writer_failure_does_not_break_ai_call(tmp_path: Path) -> None:
    """I/O failures during logging must not raise to the caller."""
    writer = TranscriptWriter(workspace_root=tmp_path, command="check", enabled=True)
    assert writer.path is not None
    writer._path = tmp_path / "not-a-file-dir"
    writer._path.mkdir()

    writer.log(
        provider="anthropic",
        transport="api",
        direction=TranscriptDirection.REQUEST,
        payload={"ok": True},
    )


def test_env_override_enables_logging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LINTRO_AI_TRANSCRIPT=1 enables logging even when config is false."""
    monkeypatch.setenv(ENV_TRANSCRIPT, "1")
    assert_that(is_transcript_enabled(config_enabled=False)).is_true()

    writer = maybe_start_transcript(
        workspace_root=tmp_path,
        config_enabled=False,
        command="doctor",
    )
    assert_that(writer).is_not_none()
    assert writer is not None
    assert_that(writer.path).is_not_none()


def test_config_enablement_starts_writer(tmp_path: Path) -> None:
    """ai.transcript_logging true starts a transcript without env override."""
    config = AIConfig(transcript_logging=True, transcript_retention=5)
    assert_that(config.transcript_logging).is_true()
    assert_that(config.transcript_retention).is_equal_to(5)

    writer = maybe_start_transcript(
        workspace_root=tmp_path,
        config_enabled=config.transcript_logging,
        retention=config.transcript_retention,
        command="check",
    )
    assert_that(writer).is_not_none()


def test_doctor_mentions_transcript_dir_when_enabled() -> None:
    """Doctor reports transcript directory when logging is enabled."""
    results = check_ai_configuration(AIConfig(transcript_logging=True))
    names = [item.name for item in results]
    assert_that(names).contains("ai.transcript")
    transcript = next(item for item in results if item.name == "ai.transcript")
    assert_that(transcript.status).is_equal_to(ToolStatus.OK)
    assert_that(transcript.message).contains(TRANSCRIPT_DIR)


def test_doctor_omits_transcript_when_disabled() -> None:
    """Doctor does not mention transcripts when logging is off and AI is off."""
    results = check_ai_configuration(AIConfig())
    assert_that([item.name for item in results]).does_not_contain("ai.transcript")


def test_cli_transport_logs_spawn_args_and_output(tmp_path: Path) -> None:
    """CLI transport records request argv and response stdout/stderr."""
    writer = TranscriptWriter(workspace_root=tmp_path, command="check", enabled=True)
    set_active_transcript(writer)

    transport = _make_cli_transport(
        binary_path="/usr/bin/true",
        binary_name="true",
        install_hint="n/a",
        provider_name="anthropic",
    )
    fake = MagicMock()
    fake.returncode = 0
    fake.stdout = "cli-out"
    fake.stderr = "cli-err"

    with patch(
        "lintro.ai.providers.cli_transport.subprocess.run",
        return_value=fake,
    ) as run_mock:
        result = transport.run(["/usr/bin/true", "--flag"], timeout=5.0, cwd="/tmp")

    assert_that(result.stdout).is_equal_to("cli-out")
    run_mock.assert_called_once()
    assert writer.path is not None
    events = _read_events(writer.path)
    assert_that([e["direction"] for e in events]).is_equal_to(["request", "response"])
    assert_that(events[0]["transport"]).is_equal_to("cli")
    assert_that(events[0]["payload"]["cmd"]).is_equal_to(["/usr/bin/true", "--flag"])
    assert_that(events[1]["payload"]["stdout"]).is_equal_to("cli-out")
    assert_that(events[1]["payload"]["stderr"]).is_equal_to("cli-err")


def test_get_provider_starts_transcript_when_enabled(tmp_path: Path) -> None:
    """Factory starts a transcript session when config enables logging."""
    from lintro.ai.providers import get_provider

    clear_active_transcript()
    config = AIConfig(
        enabled=True,
        provider=AIProvider.ANTHROPIC,
        transport=AITransport.API,
        transcript_logging=True,
        api_base_url="http://localhost:9",
    )
    from lintro.ai.exceptions import AINotAvailableError

    try:
        get_provider(config, workspace_root=tmp_path)
    except (AINotAvailableError, ValueError, ImportError, OSError):
        # Provider construction may fail without SDK; session start happens first.
        assert list((tmp_path / TRANSCRIPT_DIR).glob("*.ndjson"))

    files = list((tmp_path / TRANSCRIPT_DIR).glob("*.ndjson"))
    assert_that(files).is_not_empty()
