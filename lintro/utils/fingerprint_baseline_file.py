"""Fail-closed JSON persistence for opt-in fingerprint baselines."""

from __future__ import annotations

import errno
import json
import os
import stat
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from secrets import token_hex
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
def writer_lock(path: Path) -> Iterator[None]:
    """Exclude concurrent management commands with an exclusive sibling lock.

    Args:
        path: Baseline destination.

    Yields:
        None: While this process owns the lock.

    Raises:
        ValueError: If another writer or a stale lock owns the destination.
    """
    lock = path.with_name(path.name + ".lock")
    # Exclusive creation rejects symlinks as well as concurrent writers. A
    # killed writer leaves a visible lock requiring deliberate removal.
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ValueError(
            f"Baseline lock already exists: {lock}. Check for an active baseline "
            f"management command; if none is running, remove {lock} and retry.",
        ) from exc
    try:
        os.close(descriptor)
        yield
    finally:
        lock.unlink(missing_ok=True)


def _fsync_directory(directory: Path) -> None:
    """Flush a directory entry change to disk where the platform allows it.

    Args:
        directory: Directory whose entries were just renamed or linked.
    """
    if os.name != "posix":
        return
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def change_baseline(
    path: Path,
    transform: Callable[[FingerprintBaseline | None], FingerprintBaseline],
    *,
    create: bool = False,
    locked: bool = False,
) -> FingerprintBaseline:
    """Read, transform, and atomically write a baseline under one writer lock.

    Args:
        path: Destination, whose parent must already exist.
        transform: Computes the replacement from the current validated file.
        create: Refuse to replace any existing destination.
        locked: The caller already holds :func:`writer_lock` for ``path``.

    Returns:
        The written baseline.
    """
    if locked:
        return _change_locked(path, transform, create=create)
    with writer_lock(path):
        return _change_locked(path, transform, create=create)


def _change_locked(
    path: Path,
    transform: Callable[[FingerprintBaseline | None], FingerprintBaseline],
    *,
    create: bool,
) -> FingerprintBaseline:
    """Write a baseline while the caller holds the writer lock.

    Args:
        path: Destination, whose parent must already exist.
        transform: Computes the replacement from the current validated file.
        create: Refuse to replace any existing destination.

    Returns:
        The written baseline.

    Raises:
        ValueError: If the destination or replacement is invalid.
        OSError: If reading, writing, or committing the file fails.
    """
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
        candidate = path.parent / f".lintro-baseline-{token_hex(16)}.tmp"
        # Exclusive normal-file creation applies the umask without changing
        # the process-wide mask. Replacements inherit the existing policy
        # before any acknowledgement data is written to the temporary file.
        with candidate.open("xb") as handle:
            temporary = candidate
            if not create:
                temporary.chmod(stat.S_IMODE(path.stat().st_mode))
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if create:
            # An unrelated writer need not honor our sibling lock. Link
            # atomically without overwriting a newly created destination.
            try:
                os.link(temporary, path)
            except OSError as exc:
                if exc.errno not in {
                    errno.ENOSYS,
                    errno.EOPNOTSUPP,
                    errno.EPERM,
                    errno.EXDEV,
                }:
                    raise
                raise ValueError(
                    f"Cannot atomically create baseline {path}: {exc}. "
                    "Choose a --baseline path on a filesystem that supports "
                    "hard links.",
                ) from exc
        else:
            os.replace(temporary, path)
        # The rename is durable only once the directory entry is flushed.
        _fsync_directory(path.parent)
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


def global_baseline_note(explicit: str | None, config: Any) -> str:
    """Name the global config when it alone supplies the ``baseline`` key.

    Args:
        explicit: CLI or API override, which takes precedence over config.
        config: Loaded Lintro configuration.

    Returns:
        A sentence to append to an error, or an empty string.
    """
    if explicit is not None:
        return ""
    if "baseline" not in (getattr(config, "global_contributed_keys", None) or ()):
        return ""
    source = getattr(config, "global_config_path", None) or "the global config"
    return (
        f" The baseline setting comes from the global config {source}; set "
        "baseline in the project config, or remove it from the global config."
    )


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
        require_baseline_root(path, config, explicit=explicit)
        return read_baseline(path)
    except (OSError, ValueError) as exc:
        raise ConfigurationError(
            f"Cannot load fingerprint baseline: {exc}."
            + global_baseline_note(explicit, config),
        ) from exc


def require_baseline_root(
    path: Path,
    config: Any,
    *,
    explicit: str | None,
) -> Path:
    """Pin version-one path identities to a stable root before tools run.

    Version one stores no root. Refuse other invocation directories rather
    than interpreting the same relative entry as a different file.

    Args:
        path: Resolved baseline destination, including explicit CLI paths.
        config: Loaded configuration with an optional project config path.
        explicit: CLI or API override; ``None`` when ``path`` came from config
            or the default filename.

    Returns:
        The project config directory. Without one, the invocation directory
        for a configured path (which resolves there), or else the baseline
        file's directory.

    Raises:
        ValueError: If the invocation directory differs from the expected root.
    """
    config_path = getattr(config, "config_path", None)
    if config_path:
        root = Path(config_path).resolve().parent
    elif explicit is None and getattr(config, "baseline", None):
        # A configured value without a project config can only come from the
        # global config. It resolves at cwd, so cwd is its root, including for
        # values with a directory part such as ``sub/x.json``.
        root = Path.cwd().resolve()
    else:
        root = path.resolve().parent
    if Path.cwd().resolve() != root:
        raise ValueError(
            f"Fingerprint baselines require running from the project root: {root}. "
            "Change to that directory and retry, adjusting path arguments as needed.",
        )
    return root
