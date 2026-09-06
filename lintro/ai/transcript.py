"""Opt-in NDJSON transcript logging for AI provider traffic.

Complements the summary-level audit log with raw request/response events
written under ``.lintro-cache/ai/transcripts/``. Disabled by default; enable
via ``ai.transcript_logging`` or ``LINTRO_AI_TRANSCRIPT=1``.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from enum import StrEnum, auto
from pathlib import Path
from typing import Any

from loguru import logger

from lintro.ai.enums import AITransport
from lintro.ai.secrets import redact_secrets

__all__ = [
    "DEFAULT_COMMAND_LABEL",
    "TRANSCRIPT_DIR",
    "TranscriptDirection",
    "TranscriptWriter",
    "clear_active_transcript",
    "cli_transcript",
    "get_active_transcript",
    "is_transcript_enabled",
    "log_transcript_event",
    "maybe_start_transcript",
    "set_active_transcript",
]

TRANSCRIPT_DIR = ".lintro-cache/ai/transcripts"
"""Relative path under the workspace root for transcript files."""

ENV_TRANSCRIPT = "LINTRO_AI_TRANSCRIPT"
"""Environment variable that forces transcript logging on when set to 1/true."""

DEFAULT_RETENTION = 10
"""Default number of transcript files to keep."""

DEFAULT_COMMAND_LABEL = "ai"
"""Filename label used when a caller does not name the command it runs under.

Transcript filenames are cosmetic: every event payload already carries the
provider, transport and direction. The label is therefore only ever what a
caller states about itself -- lintro does not guess it from ``sys.argv`` (#1998).
"""

_REDACTED = "[REDACTED]"
_SENSITIVE_KEY_RE = re.compile(
    r"^(authorization|api[_-]?key|x-api-key|cookie|set-cookie|"
    r"proxy-authorization|passwd|password|secret|token)$",
    re.I,
)


class TranscriptDirection(StrEnum):
    """Direction of a logged provider traffic event."""

    REQUEST = auto()
    RESPONSE = auto()
    EVENT = auto()


_active_writer: TranscriptWriter | None = None


def is_transcript_enabled(*, config_enabled: bool = False) -> bool:
    """Return whether transcript logging should be active.

    The environment variable ``LINTRO_AI_TRANSCRIPT=1`` (also ``true``/``yes``)
    overrides config and forces logging on. Config alone enables when
    ``ai.transcript_logging`` is true.

    Args:
        config_enabled: Value of ``ai.transcript_logging`` from config.

    Returns:
        True when logging should run for this process.
    """
    env = os.environ.get(ENV_TRANSCRIPT, "").strip().lower()
    if env in {"1", "true", "yes", "on"}:
        return True
    return config_enabled


def _sanitize_command(command: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", command.strip()) or DEFAULT_COMMAND_LABEL
    return cleaned[:64]


def _redact_value(value: Any) -> Any:
    """Recursively redact secrets and strip auth-like keys from payloads."""
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_str = str(key)
            if _SENSITIVE_KEY_RE.search(key_str):
                redacted[key_str] = _REDACTED
            else:
                redacted[key_str] = _redact_value(item)
        return redacted
    if isinstance(value, (list, tuple)):
        return [_redact_value(item) for item in value]
    return value


class TranscriptWriter:
    """Append-only NDJSON writer for one AI run.

    Write failures are logged and swallowed so transcript I/O never breaks
    the surrounding AI operation.
    """

    def __init__(
        self,
        *,
        workspace_root: Path,
        command: str = DEFAULT_COMMAND_LABEL,
        retention: int = DEFAULT_RETENTION,
        enabled: bool = True,
    ) -> None:
        """Create a per-run transcript file and prune older ones.

        Args:
            workspace_root: Project root that owns ``.lintro-cache``.
            command: CLI command label embedded in the filename.
            retention: Maximum number of ``*.ndjson`` files to keep.
            enabled: When False, ``log`` becomes a no-op (still safe to call).
        """
        self._enabled = enabled
        self._path: Path | None = None
        if not enabled:
            return

        try:
            directory = workspace_root / TRANSCRIPT_DIR
            directory.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
            # Include fractional seconds to avoid collisions in fast tests.
            stamp = f"{stamp}-{int((time.time() % 1) * 1_000_000):06d}"
            safe_command = _sanitize_command(command)
            self._path = directory / f"{stamp}-{safe_command}.ndjson"
            self._path.touch(exist_ok=True)
            self._prune(
                directory,
                retention=max(1, retention),
                keep=self._path,
            )
        except Exception as exc:
            logger.debug(f"AI transcript init failed: {exc}")
            self._enabled = False
            self._path = None

    @property
    def path(self) -> Path | None:
        """Path of the active transcript file, or None when disabled."""
        return self._path

    @staticmethod
    def _prune(
        directory: Path,
        *,
        retention: int,
        keep: Path | None = None,
    ) -> None:
        """Keep the newest *retention* transcript files; delete the rest."""
        try:
            files = sorted(
                directory.glob("*.ndjson"),
                key=lambda p: (p.stat().st_mtime, p.name),
                reverse=True,
            )
        except OSError as exc:
            logger.debug(f"AI transcript prune listing failed: {exc}")
            return

        # Always retain the file created for this run, even on mtime ties.
        retained: list[Path] = []
        if keep is not None and keep in files:
            retained.append(keep)
            files = [path for path in files if path != keep]
        retained.extend(files)
        for stale in retained[retention:]:
            try:
                stale.unlink(missing_ok=True)
            except OSError as exc:
                logger.debug(f"AI transcript prune delete failed: {exc}")

    def log(
        self,
        *,
        provider: str,
        transport: str,
        direction: TranscriptDirection | str,
        payload: Any,
    ) -> None:
        """Append one redacted NDJSON event line.

        Args:
            provider: Provider identifier (e.g. ``anthropic``).
            transport: Transport label (``api`` or ``cli``).
            direction: ``request``, ``response``, or ``event``.
            payload: JSON-serializable event body (redacted before write).
        """
        if not self._enabled or self._path is None:
            return

        try:
            direction_value = (
                direction.value
                if isinstance(direction, TranscriptDirection)
                else str(direction)
            )
            event = {
                "ts": time.time(),
                "provider": provider,
                "transport": transport,
                "direction": direction_value,
                "payload": _redact_value(payload),
            }
            line = json.dumps(event, ensure_ascii=False, default=str)
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except Exception as exc:
            logger.debug(f"AI transcript write failed: {exc}")


def get_active_transcript() -> TranscriptWriter | None:
    """Return the process-wide active transcript writer, if any."""
    return _active_writer


def set_active_transcript(writer: TranscriptWriter | None) -> None:
    """Install or clear the process-wide active transcript writer."""
    global _active_writer
    _active_writer = writer


def clear_active_transcript() -> None:
    """Clear the process-wide active transcript writer."""
    set_active_transcript(None)


def maybe_start_transcript(
    *,
    workspace_root: Path,
    config_enabled: bool = False,
    retention: int = DEFAULT_RETENTION,
    command: str | None = None,
) -> TranscriptWriter | None:
    """Start a transcript session when enabled; reuse an existing one.

    Args:
        workspace_root: Project root for the cache directory.
        config_enabled: ``ai.transcript_logging`` value.
        retention: ``ai.transcript_retention`` value.
        command: Optional CLI command label embedded in the transcript
            filename. Defaults to ``DEFAULT_COMMAND_LABEL`` when omitted;
            lintro never infers it from ``sys.argv``.

    Returns:
        The active writer when logging is enabled, otherwise ``None``.
    """
    global _active_writer
    if not is_transcript_enabled(config_enabled=config_enabled):
        return None
    if _active_writer is not None:
        return _active_writer
    _active_writer = TranscriptWriter(
        workspace_root=workspace_root,
        command=command or DEFAULT_COMMAND_LABEL,
        retention=retention,
        enabled=True,
    )
    return _active_writer


def log_transcript_event(
    *,
    provider: str,
    transport: str,
    direction: TranscriptDirection | str,
    payload: Any,
) -> None:
    """Log an event to the active transcript writer when one exists.

    Safe to call when logging is disabled or no writer is active.
    """
    writer = _active_writer
    if writer is None:
        return
    writer.log(
        provider=provider,
        transport=transport,
        direction=direction,
        payload=payload,
    )


@contextmanager
def cli_transcript(
    *,
    provider: str,
    cmd: list[str],
    cwd: str | None,
    timeout: float,
    stdin: str | None,
) -> Iterator[Callable[..., None]]:
    """Record one CLI subprocess call as a request/response transcript pair.

    The spawn arguments are logged on entry. The caller records the outcome
    through the yielded callable; if the block leaves via an exception before
    doing so, the exception itself is recorded instead, so a transcript never
    shows a request with no matching response.

    Args:
        provider: Provider identifier (e.g. ``anthropic``).
        cmd: Full argv including the binary path.
        cwd: Working directory the child is spawned in.
        timeout: Subprocess timeout in seconds.
        stdin: Optional stdin payload.

    Yields:
        Callable[..., None]: Records the response event from keyword arguments.

    Raises:
        BaseException: Re-raised unchanged after the failure is recorded.
    """
    transport = AITransport.CLI.value
    log_transcript_event(
        provider=provider,
        transport=transport,
        direction=TranscriptDirection.REQUEST,
        payload={"cmd": list(cmd), "cwd": cwd, "timeout": timeout, "stdin": stdin},
    )
    recorded = False

    def record(**payload: Any) -> None:
        """Append the response event for this call.

        Args:
            **payload: Event body fields (e.g. ``returncode``, ``stdout``).
        """
        nonlocal recorded
        recorded = True
        log_transcript_event(
            provider=provider,
            transport=transport,
            direction=TranscriptDirection.RESPONSE,
            payload=payload,
        )

    try:
        yield record
    except BaseException as exc:
        if not recorded:
            record(error=type(exc).__name__, message=str(exc))
        raise
