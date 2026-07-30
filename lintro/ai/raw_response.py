"""Durable capture of raw AI responses that failed to parse.

A model response that does not conform to the requested schema is still
evidence: it routinely contains real, actionable findings. Truncating it into
an error message (the historical ``stdout[:500]``) discarded that evidence and
made the failure look like a tooling problem rather than a recoverable parse
miss (#1853).

Every parse failure therefore writes the *complete* response to a file under
``.lintro-cache/ai/raw-responses`` and reports the path, so nothing the model
produced is ever unrecoverable.
"""

from __future__ import annotations

import hashlib
import tempfile
import time
from pathlib import Path

from loguru import logger

__all__ = [
    "CLI_ENVELOPE_STAGE",
    "RAW_RESPONSE_DIR",
    "describe_raw_response",
    "persist_raw_response",
    "recover_prose_envelope",
]

#: Workspace-relative directory holding captured raw responses.
RAW_RESPONSE_DIR = ".lintro-cache/ai/raw-responses"

#: Stage label for a CLI envelope that did not parse as JSON.
CLI_ENVELOPE_STAGE = "cli-envelope"


def _slug(*, label: str) -> str:
    """Return a filesystem-safe slug for *label*.

    Args:
        label: Arbitrary caller-supplied label (provider or stage name).

    Returns:
        A lowercase slug containing only ``[a-z0-9-]``, never empty.
    """
    cleaned = "".join(
        char if char.isalnum() else "-" for char in (label or "").strip().lower()
    ).strip("-")
    return cleaned or "unknown"


def _candidate_dirs(*, workspace_root: Path | None) -> tuple[Path, ...]:
    """Return capture directories to try, in order of preference.

    The workspace cache directory is preferred so the capture sits next to the
    run that produced it; a read-only or missing workspace falls back to the
    system temporary directory rather than losing the evidence.

    Args:
        workspace_root: Workspace root, or ``None`` to use the current
            directory.

    Returns:
        Ordered candidate directories.
    """
    root = workspace_root if workspace_root is not None else Path.cwd()
    return (
        root / RAW_RESPONSE_DIR,
        Path(tempfile.gettempdir()) / "lintro-ai-raw-responses",
    )


def persist_raw_response(
    *,
    provider: str,
    stage: str,
    raw: str,
    workspace_root: Path | None = None,
) -> Path | None:
    """Write the complete raw response to disk and return its path.

    Args:
        provider: Provider or binary identifier, e.g. ``"claude"``.
        stage: Where the parse failed, e.g. ``"cli-envelope"`` or ``"review"``.
        raw: The complete raw response text. Never truncated.
        workspace_root: Workspace root for the capture directory. Defaults to
            the current working directory.

    Returns:
        The path written, or ``None`` when no candidate directory was writable.
        Failing to persist is never fatal: the caller still reports the full
        text inline.
    """
    digest = hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:12]
    name = (
        f"{_slug(label=stage)}-{_slug(label=provider)}-{int(time.time())}-{digest}.txt"
    )
    for directory in _candidate_dirs(workspace_root=workspace_root):
        try:
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / name
            target.write_text(raw, encoding="utf-8")
        except OSError:
            continue
        return target
    return None


def describe_raw_response(
    *,
    provider: str,
    stage: str,
    raw: str,
    workspace_root: Path | None = None,
) -> str:
    """Persist *raw* and return an evidence block carrying it in full.

    The returned block is embedded verbatim in provider error messages, so a
    failure that reaches the user always carries every character the model
    produced plus the path it was saved to.

    Args:
        provider: Provider or binary identifier, e.g. ``"claude"``.
        stage: Where the parse failed, e.g. ``"cli-envelope"``.
        raw: The complete raw response text.
        workspace_root: Workspace root for the capture directory.

    Returns:
        A multi-line evidence block. Never truncates *raw*.
    """
    path = persist_raw_response(
        provider=provider,
        stage=stage,
        raw=raw,
        workspace_root=workspace_root,
    )
    header = f"Full raw output ({len(raw)} chars)"
    if path is not None:
        header = f"{header} saved to {path}"
    return f"{header}:\n----- raw output start -----\n{raw}\n----- raw output end -----"


def recover_prose_envelope(*, provider: str, stdout: str, reason: str) -> str | None:
    """Return a non-JSON CLI envelope as unstructured prose, or ``None``.

    The CLI has already exited zero by the time a caller reaches this: the
    envelope simply is not JSON, which in practice means the agent answered in
    prose. Discarding that answer threw away real findings (#1853), so the
    prose is handed back as unstructured content for the review layer to
    recover, and the complete text is persisted either way.

    Args:
        provider: Provider or binary identifier, e.g. ``"Claude"``.
        stdout: The complete raw stdout that failed to parse as JSON.
        reason: The parse error, used in the warning line.

    Returns:
        The stripped prose, or ``None`` when stdout carries no text at all —
        an empty response is a genuine failure, not a recoverable answer.
    """
    content = (stdout or "").strip()
    if not content:
        return None
    path = persist_raw_response(
        provider=provider,
        stage=CLI_ENVELOPE_STAGE,
        raw=stdout,
    )
    location = f" Full response saved to {path}." if path is not None else ""
    logger.warning(
        f"{provider} CLI returned a non-JSON envelope ({reason}); "
        f"recovering it as unstructured prose.{location}",
    )
    return content
