"""Fail-closed JSON persistence for opt-in fingerprint baselines."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING, Any, Literal

from lintro.exceptions.errors import ConfigurationError
from lintro.models.core.fingerprint_baseline import FingerprintBaseline

if TYPE_CHECKING:
    from collections.abc import Iterator

MAX_BASELINE_BYTES = 10 * 1024 * 1024
DEFAULT_BASELINE = ".lintro-baseline.json"


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build a JSON object while rejecting duplicate keys."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate baseline JSON key: {key}")
        result[key] = value
    return result


def read_baseline(path: Path) -> FingerprintBaseline:
    """Load an explicitly requested baseline, failing on any invalid input.

    Args:
        path: Baseline JSON file.

    Returns:
        Validated version-one baseline.

    Raises:
        ValueError: If the file is too large or invalid.
    """
    if not path.is_file():
        raise ValueError(f"baseline is not a regular file: {path}")
    with path.open("rb") as handle:
        raw = handle.read(MAX_BASELINE_BYTES + 1)
    if len(raw) > MAX_BASELINE_BYTES:
        raise ValueError("baseline exceeds 10 MiB")
    try:
        data = json.loads(raw, object_pairs_hook=_unique_object)
        return FingerprintBaseline.model_validate(data)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ValueError(f"Invalid baseline {path}: {exc}") from exc


@contextmanager
def _writer_lock(path: Path) -> Iterator[None]:
    """Exclude concurrent management commands with an exclusive sibling lock.

    Args:
        path: Baseline destination.

    Yields:
        None: While this process owns the lock.
    """
    lock = path.with_name(path.name + ".lock")
    # Exclusive creation rejects symlinks as well as concurrent writers. A
    # killed writer leaves a visible lock requiring deliberate removal.
    descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.close(descriptor)
        yield
    finally:
        lock.unlink(missing_ok=True)


def change_baseline(
    path: Path,
    transform: Callable[[FingerprintBaseline | None], FingerprintBaseline],
    *,
    create: bool = False,
) -> FingerprintBaseline:
    """Read, transform, and atomically write a baseline under one writer lock.

    Args:
        path: Destination, whose parent must already exist.
        transform: Computes the replacement from the current validated file.
        create: Refuse to replace any existing destination.

    Returns:
        The written baseline.

    Raises:
        ValueError: If the destination or replacement is invalid.
    """
    with _writer_lock(path):
        if path.is_symlink():
            raise ValueError("refusing to replace a symlink baseline")
        if create and path.exists():
            raise ValueError(f"baseline already exists: {path}")
        previous = None if create else read_baseline(path)
        updated = FingerprintBaseline.model_validate(transform(previous).model_dump())
        data = updated.model_dump()
        data["entries"] = sorted(
            data["entries"],
            key=lambda entry: (
                entry["tool"],
                entry["file"],
                entry["code"],
                entry["message_hash"],
            ),
        )
        payload = (json.dumps(data, indent=2, ensure_ascii=True) + "\n").encode()
        if len(payload) > MAX_BASELINE_BYTES:
            raise ValueError("baseline exceeds 10 MiB")
        temporary: Path | None = None
        try:
            with NamedTemporaryFile(dir=path.parent, delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            if create:
                # An unrelated writer need not honor our sibling lock. Link
                # atomically without overwriting a newly created destination.
                os.link(temporary, path)
            else:
                os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return updated


def resolve_baseline_path(
    explicit: str | None,
    config: Any,
    *,
    default: bool = False,
) -> Path | None:
    """Resolve CLI paths at cwd and configured paths at the config directory.

    Args:
        explicit: CLI override.
        config: Loaded Lintro configuration.
        default: Use the standard filename when neither source opts in.

    Returns:
        The chosen path, or None when baselining is disabled.
    """
    if explicit is not None:
        return Path(explicit).absolute()
    configured = getattr(config, "baseline", None)
    if configured:
        config_path = getattr(config, "config_path", None)
        root = Path(config_path).parent if config_path else Path.cwd()
        return (root / str(configured)).absolute()
    return Path(DEFAULT_BASELINE).absolute() if default else None


def load_run_baseline(
    explicit: str | Literal[False] | None,
    config: Any,
) -> FingerprintBaseline | None:
    """Validate an opted-in baseline before any tool or AI execution.

    Args:
        explicit: Override path, ``None`` to use config, or ``False`` to disable.
        config: Loaded project configuration.

    Returns:
        The baseline, or None when the feature is disabled.

    Raises:
        ConfigurationError: If an opted-in baseline cannot be loaded.
    """
    if explicit is False:
        return None
    path = resolve_baseline_path(explicit, config)
    if path is None:
        return None
    try:
        return read_baseline(path)
    except (OSError, ValueError) as exc:
        raise ConfigurationError(f"Cannot load fingerprint baseline: {exc}") from exc
