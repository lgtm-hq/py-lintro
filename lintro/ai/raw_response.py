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
import os
import re
import stat
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
    "sanitize_for_display",
]

#: Workspace-relative directory holding captured raw responses.
RAW_RESPONSE_DIR = ".lintro-cache/ai/raw-responses"

#: Stage label for a CLI envelope that did not parse as JSON.
CLI_ENVELOPE_STAGE = "cli-envelope"

#: Directory mode for capture directories (owner-only: captures can embed diff
#: context and other repository content).
_DIR_MODE = 0o700

#: File mode for captured responses.
_FILE_MODE = 0o600

# ANSI/OSC escape sequences and other C0 control characters (tab and newline
# excluded). A model answer is untrusted text that lands in an error message
# printed to a terminal, so escape sequences are neutralised for display. The
# capture file keeps the bytes exactly as received.
_ESCAPE_SEQUENCE_RE = re.compile(
    r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b][^\x07\x1b]*(?:\x07|\x1b\\)",
)
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def sanitize_for_display(*, text: str) -> str:
    """Return *text* with terminal escape sequences neutralised.

    Args:
        text: Untrusted model output destined for a terminal.

    Returns:
        The text with ANSI/OSC sequences and stray control characters replaced
        by visible placeholders. Tabs and newlines are preserved.
    """
    stripped = _ESCAPE_SEQUENCE_RE.sub("", text)
    return _CONTROL_CHAR_RE.sub("?", stripped)


def _slug(*, label: str) -> str:
    """Return a filesystem-safe slug for *label*.

    Args:
        label: Arbitrary caller-supplied label (provider or stage name).

    Returns:
        A lowercase slug containing only ``[a-z0-9-]``, never empty.
    """
    cleaned = "".join(
        char if char.isascii() and char.isalnum() else "-"
        for char in (label or "").strip().lower()
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


def _mkdir_private(*, directory: Path) -> None:
    """Create *directory* and any missing ancestors owner-only.

    ``Path.mkdir(parents=True, mode=...)`` applies the mode only to the leaf,
    leaving newly created ancestors (``.lintro-cache``, ``.lintro-cache/ai``)
    at the process umask. Missing ancestors are created here at ``0700`` and
    chmodded to defeat a restrictive umask; pre-existing directories are left
    untouched.

    Args:
        directory: Capture directory to create.
    """
    missing: list[Path] = []
    current = directory
    while not current.exists():
        missing.append(current)
        parent = current.parent
        if parent == current:
            break
        current = parent
    for path in reversed(missing):
        path.mkdir(mode=_DIR_MODE, exist_ok=True)
        path.chmod(_DIR_MODE)


def _prepare_directory(*, directory: Path) -> bool:
    """Create *directory* and verify it is safe to write captures into.

    The fallback lives at a predictable path in the shared system temp
    directory, so a pre-existing entry there is untrusted: a symlink, another
    user's directory, or one with loose permissions would leak captures that
    can embed diff context. ``lstat`` deliberately does not follow symlinks.
    ``OSError`` from creation or inspection propagates to the caller, which
    treats it as "try the next candidate".

    Args:
        directory: Candidate capture directory.

    Returns:
        True when the directory exists, is a real directory (not a symlink),
        is owned by the current user, and is owner-only.
    """
    _mkdir_private(directory=directory)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode):
        return False
    if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
        return False
    if stat.S_IMODE(info.st_mode) != _DIR_MODE:
        # A pre-existing directory we own but with looser modes is tightened
        # rather than rejected: losing the capture is the worse outcome.
        directory.chmod(_DIR_MODE)
    return True


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
            if not _prepare_directory(directory=directory):
                continue
            target = directory / name
            # Captures can embed diff context, so they are created owner-only
            # rather than at the process umask. O_EXCL + O_NOFOLLOW refuse
            # pre-existing entries and symlinks in the shared temp fallback.
            descriptor = os.open(
                target,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                _FILE_MODE,
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(raw)
        except FileExistsError:
            # Same second, same content (the name embeds a content digest in a
            # directory verified owner-only): the capture already exists.
            return directory / name
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
        A multi-line evidence block. Never truncates *raw*; terminal escape
        sequences are neutralised for display only, and the capture file keeps
        the original bytes.
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
    body = sanitize_for_display(text=raw)
    return (
        f"{header}:\n----- raw output start -----\n{body}\n----- raw output end -----"
    )


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
