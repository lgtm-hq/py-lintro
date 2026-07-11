"""Availability detection for competitor meta-linters.

The harness must degrade gracefully when a competitor tool is not installed in
the current environment. This module centralizes the "is this tool available"
checks so the runner definitions and the CLI can share a single source of truth.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from enum import StrEnum, auto


class CompetitorTool(StrEnum):
    """Meta-linters that lintro is benchmarked against.

    Values are lower-case identifiers used in report keys and CLI selectors.
    """

    LINTRO = auto()
    SEQUENTIAL = auto()
    PRE_COMMIT = auto()
    MEGALINTER = auto()


def which(executable: str) -> str | None:
    """Resolve an executable on ``PATH``.

    Args:
        executable: Name of the executable to look up.

    Returns:
        str | None: Absolute path to the executable, or None if not found.
    """
    return shutil.which(executable)


@dataclass(frozen=True, slots=True)
class Availability:
    """Availability status of a competitor tool.

    Attributes:
        tool: The competitor tool being described.
        available: Whether the tool can be executed in this environment.
        reason: Human-readable explanation when unavailable.
    """

    tool: CompetitorTool
    available: bool
    reason: str


def _pre_commit_available() -> Availability:
    """Determine whether pre-commit can be invoked.

    Returns:
        Availability: Status for the pre-commit competitor.
    """
    if which("pre-commit") is not None or which("prek") is not None:
        return Availability(
            tool=CompetitorTool.PRE_COMMIT,
            available=True,
            reason="found on PATH",
        )
    return Availability(
        tool=CompetitorTool.PRE_COMMIT,
        available=False,
        reason="neither 'pre-commit' nor 'prek' found on PATH",
    )


def _megalinter_available() -> Availability:
    """Determine whether MegaLinter can be invoked.

    MegaLinter is distributed as a Docker image, driven either directly via the
    ``docker`` CLI or through the ``mega-linter-runner`` npm wrapper.

    Returns:
        Availability: Status for the MegaLinter competitor.
    """
    if which("mega-linter-runner") is not None:
        return Availability(
            tool=CompetitorTool.MEGALINTER,
            available=True,
            reason="mega-linter-runner on PATH",
        )
    if which("docker") is not None:
        return Availability(
            tool=CompetitorTool.MEGALINTER,
            available=True,
            reason="docker available (image pulled on first run)",
        )
    return Availability(
        tool=CompetitorTool.MEGALINTER,
        available=False,
        reason="neither 'mega-linter-runner' nor 'docker' found on PATH",
    )


def detect_runners() -> dict[CompetitorTool, Availability]:
    """Detect which competitor tools are available in this environment.

    Lintro and the sequential-native scenario are always considered available
    because the harness runs lintro via ``uv run`` and the sequential scenario
    only invokes tools lintro already manages.

    Returns:
        dict[CompetitorTool, Availability]: Availability keyed by tool.
    """
    return {
        CompetitorTool.LINTRO: Availability(
            tool=CompetitorTool.LINTRO,
            available=True,
            reason="invoked via 'uv run lintro'",
        ),
        CompetitorTool.SEQUENTIAL: Availability(
            tool=CompetitorTool.SEQUENTIAL,
            available=True,
            reason="invoked via 'uv run' against lintro-managed tools",
        ),
        CompetitorTool.PRE_COMMIT: _pre_commit_available(),
        CompetitorTool.MEGALINTER: _megalinter_available(),
    }
