"""Outcome classification for a single tool install/upgrade action."""

from __future__ import annotations

from enum import StrEnum, auto


class InstallOutcome(StrEnum):
    """Distinct end states of an install or upgrade action.

    A plain success/failure flag cannot express the states that make a retry
    loop converge: a command may succeed while the binary stays undiscoverable,
    a command may succeed while the version stays below ``min_version``, and a
    timeout is retryable where an invalid package name is not.

    Attributes:
        SUCCESS: Command succeeded and the tool is discoverable afterwards.
        NOT_DISCOVERABLE: Command succeeded but the tool is still not
            discoverable (not on PATH, and not a project-local
            ``node_modules/.bin`` binary).
        STILL_OUTDATED: Command succeeded and the tool is discoverable, but
            the probed version is still below ``min_version``.
        FAILED: Command ran and exited non-zero (or could not be launched).
        TIMED_OUT: Command exceeded the install timeout.
        MANUAL_BLOCKED: No executable command exists in this environment.
    """

    SUCCESS = auto()
    NOT_DISCOVERABLE = auto()
    STILL_OUTDATED = auto()
    FAILED = auto()
    TIMED_OUT = auto()
    MANUAL_BLOCKED = auto()

    @property
    def is_success(self) -> bool:
        """Whether the action fully succeeded.

        Returns:
            True only for :attr:`SUCCESS`.
        """
        return self is InstallOutcome.SUCCESS

    @property
    def is_retryable(self) -> bool:
        """Whether re-running the same command could still succeed.

        Only a timeout is worth retrying. A failed command, a blocked manual
        step, an install that left the tool undiscoverable, and an upgrade
        that left the version below ``min_version`` all reproduce identically,
        so none of them may be re-emitted as a quick fix.

        Returns:
            True when retrying the identical command is meaningful.
        """
        return self is InstallOutcome.TIMED_OUT

    @property
    def label(self) -> str:
        """Short, uppercase status label for terminal output.

        Returns:
            Human-facing label such as ``"OK"`` or ``"TIMEOUT"``.
        """
        return _LABELS[self]


_LABELS: dict[InstallOutcome, str] = {
    InstallOutcome.SUCCESS: "OK",
    InstallOutcome.NOT_DISCOVERABLE: "PATH",
    InstallOutcome.STILL_OUTDATED: "STALE",
    InstallOutcome.FAILED: "FAIL",
    InstallOutcome.TIMED_OUT: "TIMEOUT",
    InstallOutcome.MANUAL_BLOCKED: "MANUAL",
}
