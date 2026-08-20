"""ARG_MAX-safe batching of file paths for tools that take explicit argv.

Several tools scan a catch-all file set (``file_patterns=["*"]``) and receive
the resolved paths as command-line arguments. A large repository can hold tens
of thousands of files, and expanding them all into a single invocation would
exceed the OS ``ARG_MAX`` limit, making ``execve`` fail with ``E2BIG`` (surfaced
as an ``OSError`` that fails the whole run). The helpers here derive a safe byte
budget and split a path list into batches that fit inside it.
"""

from __future__ import annotations

import os
import sys

# Reserved bytes for argv/environment accounting slack.
ARGV_SAFETY_HEADROOM_BYTES: int = 4096
# POSIX-guaranteed ARG_MAX minimum, used when the real limit is unavailable.
ARGV_FALLBACK_LIMIT_BYTES: int = 131072
# Linux charges one pointer slot per argv/envp entry on top of the string
# bytes. Assume 64-bit pointers, which is the conservative choice.
ARGV_POINTER_BYTES: int = 8


def _fsbytes(value: str) -> bytes:
    """Encode a string the way the OS will when it reaches ``execve``.

    Args:
        value: Text destined for argv or the environment block.

    Returns:
        The encoded bytes, using the filesystem encoding with surrogate
        round-tripping so undecodable names are measured, not rejected.
    """
    return value.encode(sys.getfilesystemencoding(), "surrogateescape")


def argv_byte_budget() -> int:
    """Return a safe byte budget for path arguments on one command line.

    The budget is derived from the OS ``ARG_MAX`` limit, reserving room for the
    current environment block (``execve`` counts it against the same limit) and
    a fixed safety margin. Falls back to the POSIX-guaranteed minimum when
    ``ARG_MAX`` cannot be queried, and is clamped to ``ARG_MAX`` when the
    environment has consumed the entire limit.

    Returns:
        The maximum number of argument-data bytes to place on one command line.
    """
    try:
        arg_max = os.sysconf("SC_ARG_MAX")
    except (ValueError, OSError, AttributeError):
        arg_max = ARGV_FALLBACK_LIMIT_BYTES
    if not isinstance(arg_max, int) or arg_max <= 0:
        arg_max = ARGV_FALLBACK_LIMIT_BYTES

    # ``execve`` counts bytes, not characters, so measure the environment the
    # same way ``chunk_paths`` measures paths: encoded, plus the "=" and NUL
    # the kernel stores per entry, plus one pointer slot per envp entry.
    env_bytes = sum(
        len(_fsbytes(key)) + len(_fsbytes(value)) + 2 + ARGV_POINTER_BYTES
        for key, value in os.environ.items()
    )
    budget = arg_max - env_bytes - ARGV_SAFETY_HEADROOM_BYTES
    if budget > 0:
        return budget
    # The environment has eaten the whole limit. Return something usable, but
    # never more than ``ARG_MAX`` itself: raising the floor above the real
    # kernel limit would hand back a budget that cannot be executed, which is
    # exactly the ``E2BIG`` this module exists to prevent.
    return max(min(ARGV_SAFETY_HEADROOM_BYTES, arg_max), 1)


def chunk_paths(
    paths: list[str],
    *,
    fixed_arg_bytes: int,
    budget: int | None = None,
) -> list[list[str]]:
    """Split resolved file paths into ARG_MAX-safe batches.

    Batches preserve input order so tool output is deterministic. A single path
    that alone exceeds the budget is still placed in its own batch (the OS, not
    lintro, then decides whether it is too long); this keeps the function total
    and never silently drops a file from the run.

    Args:
        paths: File paths to pass as arguments, in a stable order.
        fixed_arg_bytes: Byte length of the non-path portion of the command
            (executable, subcommand, flags), which counts against the same
            OS limit.
        budget: Total argument-byte budget. Defaults to
            :func:`argv_byte_budget`.

    Returns:
        A list of path batches, each safe to place on one command line.
    """
    total_budget = argv_byte_budget() if budget is None else budget
    remaining = max(total_budget - fixed_arg_bytes, 1)

    batches: list[list[str]] = []
    current: list[str] = []
    current_bytes = 0
    for path in paths:
        # +1 for the argv NUL terminator and one pointer slot, both of which
        # the kernel accounts per argument.
        path_bytes = len(_fsbytes(path)) + 1 + ARGV_POINTER_BYTES
        if current and current_bytes + path_bytes > remaining:
            batches.append(current)
            current = []
            current_bytes = 0
        current.append(path)
        current_bytes += path_bytes
    if current:
        batches.append(current)
    return batches


__all__ = [
    "ARGV_FALLBACK_LIMIT_BYTES",
    "ARGV_POINTER_BYTES",
    "ARGV_SAFETY_HEADROOM_BYTES",
    "argv_byte_budget",
    "chunk_paths",
]
