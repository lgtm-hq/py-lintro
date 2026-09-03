"""Subprocess invocation of ``lintro review`` for one matrix cell.

The harness never imports the review orchestrator. It drives the installed CLI
exactly as a user would, with configuration supplied only through the
``LINTRO_AI_*`` environment overrides, so a matrix run measures the shipped
product rather than a harness-specific wiring of it.
"""

from __future__ import annotations

import os
import subprocess  # nosec B404 - the harness deliberately drives the real CLI
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from review_matrix.models.corpus import CorpusItem
from review_matrix.models.matrix import MatrixConfig, MatrixSpec

__all__ = [
    "InvocationResult",
    "ReviewInvoker",
    "build_command",
    "build_env",
    "run_review_cli",
]

#: Extra wall-clock slack over ``--timeout`` before the harness kills a run.
#: The CLI owns its own provider timeout; this only catches a wedged process.
_TIMEOUT_GRACE_SECONDS = 120.0


@dataclass(frozen=True, slots=True)
class InvocationResult:
    """Raw result of one ``lintro review`` invocation.

    Attributes:
        exit_code: Process exit code; ``-1`` when the process never completed.
        stdout: Captured standard output, expected to be review JSON.
        stderr: Captured standard error, persisted next to the payload.
        elapsed_seconds: Wall-clock duration of the invocation.
    """

    exit_code: int
    stdout: str
    stderr: str
    elapsed_seconds: float


class ReviewInvoker(Protocol):
    """Callable that performs one review invocation.

    Injecting this protocol is how the runner is tested: the test double
    returns canned payloads, so no test can reach a provider or the network.
    """

    def __call__(
        self,
        *,
        config: MatrixConfig,
        item: CorpusItem,
        spec: MatrixSpec,
    ) -> InvocationResult:
        """Run one review and return its raw result.

        Args:
            config: Matrix cell being exercised.
            item: Corpus item being reviewed.
            spec: Matrix specification supplying shared review knobs.

        Returns:
            The invocation's raw result.
        """
        ...


def build_command(
    *,
    config: MatrixConfig,
    item: CorpusItem,
    spec: MatrixSpec,
) -> tuple[str, ...]:
    """Build the ``lintro review`` argument vector for one run.

    Advisory finder tools are switched off: they are a separate,
    non-diff-based surface and would add findings the matrix is not comparing.

    Args:
        config: Matrix cell being exercised.
        item: Corpus item being reviewed.
        spec: Matrix specification supplying shared review knobs.

    Returns:
        The command as an argument tuple.
    """
    del config
    return (
        "uv",
        "run",
        "lintro",
        "review",
        "--pr",
        str(item.pr),
        "--repo",
        item.repo,
        "--depth",
        str(spec.depth),
        "--timeout",
        f"{spec.timeout_seconds:g}",
        "--output",
        "json",
        "--advisory-tools",
        "none",
    )


def build_env(
    *,
    config: MatrixConfig,
    base_env: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build the environment that pins one matrix config.

    Args:
        config: Matrix cell whose overrides are applied.
        base_env: Environment to overlay; defaults to the current process
            environment.

    Returns:
        A new environment mapping with the ``LINTRO_AI_*`` overlay applied.
    """
    env = dict(os.environ if base_env is None else base_env)
    env.update(config.env_overrides)
    return env


def run_review_cli(
    *,
    config: MatrixConfig,
    item: CorpusItem,
    spec: MatrixSpec,
    cwd: Path | None = None,
) -> InvocationResult:
    """Invoke ``lintro review`` once and capture its output.

    Args:
        config: Matrix cell being exercised.
        item: Corpus item being reviewed.
        spec: Matrix specification supplying shared review knobs.
        cwd: Working directory for the invocation; defaults to the caller's.

    Returns:
        The invocation's raw result. A timeout is reported as exit code ``-1``
        rather than raised, so one hung cell cannot abort the whole matrix.
    """
    command: Sequence[str] = build_command(config=config, item=item, spec=spec)
    env = build_env(config=config)
    started = time.monotonic()
    try:
        completed = subprocess.run(  # nosec B603 - fixed argv, shell=False
            list(command),
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=spec.timeout_seconds + _TIMEOUT_GRACE_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        return InvocationResult(
            exit_code=-1,
            stdout="",
            stderr=f"timed out after {exc.timeout:g}s",
            elapsed_seconds=time.monotonic() - started,
        )
    return InvocationResult(
        exit_code=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
        elapsed_seconds=time.monotonic() - started,
    )
