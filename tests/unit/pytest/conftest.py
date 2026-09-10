"""Pytest configuration for pytest-specific unit tests.

Tests in this directory focus on pytest command-line interface functionality
and pytest formatter behavior.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest


@dataclass
class PipelineRecorder:
    """Records the pipeline invocations a CLI command produced.

    ``lintro test`` normalises its flags into pipeline keyword arguments and
    then propagates the pipeline's exit code. Recording those arguments in a
    plain list keeps the assertions on real captured values rather than on
    mock call bookkeeping (#2315).

    Attributes:
        runs: One mapping of keyword arguments per pipeline invocation.
        exit_code: Exit code the fake pipeline returns to the command.
    """

    runs: list[dict[str, Any]] = field(default_factory=list)
    exit_code: int = 0

    @property
    def only_run(self) -> dict[str, Any]:
        """Return the keyword arguments of the single recorded invocation.

        Returns:
            The recorded keyword arguments.

        Raises:
            AssertionError: If the command did not invoke the pipeline exactly
                once, which means the assertions that follow would be reading
                the wrong run.
        """
        if len(self.runs) != 1:
            raise AssertionError(
                f"expected exactly one pipeline run, recorded {len(self.runs)}",
            )
        return self.runs[0]


@pytest.fixture
def recorded_pipeline(monkeypatch: pytest.MonkeyPatch) -> PipelineRecorder:
    """Replace the pipeline behind ``lintro test`` with a recording stand-in.

    Args:
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        The recorder holding every pipeline invocation the command made.
    """
    recorder = PipelineRecorder()

    def _record_run(**kwargs: Any) -> int:
        recorder.runs.append(kwargs)
        return recorder.exit_code

    monkeypatch.setattr(
        "lintro.cli_utils.commands.test.run_lint_with_ai",
        _record_run,
    )
    return recorder
