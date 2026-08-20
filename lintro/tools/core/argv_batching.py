"""ARG_MAX-safe batching of file paths for tools that take explicit argv.

Several tools scan a catch-all file set (``file_patterns=["*"]``) and receive
the resolved paths as command-line arguments. A large repository can hold tens
of thousands of files, and expanding them all into a single invocation would
exceed the OS ``ARG_MAX`` limit, making ``execve`` fail with ``E2BIG`` (surfaced
as an ``OSError`` that fails the whole run). The helpers here derive a safe byte
budget and split a path list into batches that fit inside it.

The budget is a best effort, not a proof: it tracks the bytes left after the
environment block, but :func:`chunk_paths` still emits an individually
oversized path in a batch of its own rather than dropping it, and the budget
floors at one byte when the environment has consumed the entire limit. In
those two cases the OS remains the authority on whether the argv is
executable.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence

# Reserved bytes for argv/environment accounting slack. Scaled down on hosts
# with a small ``ARG_MAX`` (see :func:`_headroom_for`) so the margin can never
# swallow the whole limit.
ARGV_SAFETY_HEADROOM_BYTES: int = 4096
# ``_POSIX_ARG_MAX`` — the smallest ``ARG_MAX`` POSIX permits. Used only when
# ``SC_ARG_MAX`` cannot be queried, where guessing low is the safe direction:
# too small only costs extra batches, while too large costs an ``E2BIG``.
ARGV_FALLBACK_LIMIT_BYTES: int = 4096
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


def argv_cost(args: Sequence[str]) -> int:
    """Return the bytes ``execve`` charges for ``args``.

    Callers must size ``fixed_arg_bytes`` with this function so the command
    prefix is measured by the same rules as the paths — encoded byte length,
    the NUL terminator, and one pointer slot per entry. Sizing it with
    ``len(s)`` undercounts non-ASCII flags and ignores the pointer slots.

    Args:
        args: Command-line arguments (executable, subcommands, flags, paths).

    Returns:
        Total bytes those arguments occupy against the OS limit.
    """
    return sum(len(_fsbytes(arg)) + 1 + ARGV_POINTER_BYTES for arg in args)


def _headroom_for(arg_max: int) -> int:
    """Scale the safety margin to the limit it is carved out of.

    A flat 4 KiB margin is sensible against a megabyte-scale ``ARG_MAX`` but
    consumes the entire limit on a host at the POSIX floor, so cap it at a
    quarter of what is available.

    Args:
        arg_max: The OS argument-size limit in bytes.

    Returns:
        Bytes to hold back from the usable budget.
    """
    return min(ARGV_SAFETY_HEADROOM_BYTES, max(arg_max // 4, 1))


def argv_byte_budget() -> int:
    """Return a safe byte budget for path arguments on one command line.

    The budget is derived from the OS ``ARG_MAX`` limit, reserving room for the
    current environment block (``execve`` counts it against the same limit) and
    a safety margin. Falls back to the POSIX minimum when ``ARG_MAX`` cannot be
    queried.

    The result tracks the bytes actually left after the environment block
    rather than a fixed floor. The one exception is a 1-byte lower bound when
    nothing is left at all: that is not a claim of capacity, only enough for
    :func:`chunk_paths` to terminate with one path per batch and let the OS be
    the authority — the same contract as an individually oversized path.

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
    remaining = arg_max - env_bytes
    budget = remaining - _headroom_for(arg_max)
    if budget > 0:
        return budget
    if remaining > 0:
        # Positive but tighter than the safety margin. Deliberately hand back
        # the true remainder with no slack rather than a comfortable-looking
        # floor: the margin is insurance against our own accounting drift, and
        # spending bytes we do not have is exactly the ``E2BIG`` this module
        # exists to prevent.
        return remaining
    # Nothing is left at all. Returning 1 does exceed what remains; it is not a
    # capacity claim, only the smallest value that keeps :func:`chunk_paths`
    # terminating (one path per batch). Whether that argv executes is then the
    # OS's call, the same contract as an individually oversized path.
    return 1


def chunk_paths(
    paths: list[str],
    *,
    fixed_arg_bytes: int,
    budget: int | None = None,
) -> list[list[str]]:
    """Split resolved file paths into ARG_MAX-safe batches.

    Batches preserve input order so tool output is deterministic.

    The guarantee is bounded, deliberately: every batch of two or more paths
    fits the budget, but a single path that alone exceeds it is still emitted
    in a batch of its own, and when the environment has consumed the whole
    limit the budget floors at one byte. Both cases hand the decision to the
    OS rather than silently dropping a file, so callers must be prepared for
    ``execve`` to reject a batch instead of assuming it cannot happen.

    Args:
        paths: File paths to pass as arguments, in a stable order.
        fixed_arg_bytes: Byte length of the non-path portion of the command
            (executable, subcommand, flags), which counts against the same
            OS limit.
        budget: Total argument-byte budget. Defaults to
            :func:`argv_byte_budget`.

    Returns:
        A list of path batches. Every batch holding more than one path fits
        the budget; a single oversized path is returned as its own batch.
    """
    total_budget = argv_byte_budget() if budget is None else budget
    remaining = max(total_budget - fixed_arg_bytes, 1)

    batches: list[list[str]] = []
    current: list[str] = []
    current_bytes = 0
    for path in paths:
        # +1 for the argv NUL terminator and one pointer slot, both of which
        # the kernel accounts per argument.
        path_bytes = argv_cost([path])
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
    "argv_cost",
    "chunk_paths",
]
