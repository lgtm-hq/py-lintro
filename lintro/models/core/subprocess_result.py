"""Native subprocess status and separated output streams."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SubprocessResult:
    """Result of a subprocess execution with separated output streams.

    Historically the subprocess runners concatenated stdout and stderr into a
    single string, which forced every JSON parser to hand-roll recovery from a
    mixed blob (a single stderr warning line could break parsing). This result
    object keeps the streams separated so tool definitions can parse stdout
    only and log stderr independently. See issue #1043.

    Attributes:
        returncode: The subprocess exit code.
        stdout: Captured standard output. For streaming execution (which merges
            the child's stderr into stdout for real-time line handling) this
            holds the combined stream and ``stderr`` is empty.
        stderr: Captured standard error (empty for streaming execution).
        output: Backward-compatible display string. Normally ``stdout`` and
            ``stderr`` concatenated, or a formatted failure message when the
            command failed without emitting any output.
    """

    returncode: int
    stdout: str
    stderr: str
    output: str

    @property
    def success(self) -> bool:
        """Whether the subprocess exited successfully.

        Returns:
            True when the return code is zero.
        """
        return self.returncode == 0

    def as_tuple(self) -> tuple[bool, str]:
        """Return the legacy ``(success, output)`` representation.

        Returns:
            Tuple of the success flag and the combined/display output, matching
            the pre-#1043 return contract used by most tool definitions.
        """
        return self.success, self.output


class SubprocessRunner(Protocol):
    """The native subprocess operation needed by timeout-aware callers."""

    def _run_subprocess_result(
        self,
        cmd: list[str],
        timeout: int | None = None,
        cwd: str | None = None,
    ) -> SubprocessResult:
        """Execute a command while preserving its native verdict.

        Args:
            cmd: Command and arguments.
            timeout: Explicit subprocess deadline or the producer's default.
            cwd: Working directory for execution.

        Returns:
            Native status and separated output streams.
        """
        ...
