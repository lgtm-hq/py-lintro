"""Tool-availability gate for the integration suite.

The integration suite is a required check that runs inside the lintro tools
image, where every wrapped tool is baked in. A tool missing there is a real
regression (a broken image, a dropped install step, a renamed binary), so it
must fail the build instead of silently skipping the tests that would have
caught it (#465).

Outside the image — a contributor laptop, the toolless hosted runner — the
same gate degrades to a skip so the suite stays runnable without installing
the full tool stack.

The switch is the ``LINTRO_TOOLS_IMAGE`` environment variable, set to ``1``
by ``docker/tools.Dockerfile`` and by the ``test-integration`` service in
``docker-compose.yml``.

Usage::

    from tests.integration._tools import require_tool

    pytestmark = require_tool("shellcheck")

Modules needing several binaries pass a list::

    pytestmark = [require_tool("golangci-lint"), require_tool("go", ...)]
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess  # nosec B404 - probes the tool under test; every call is shell=False
from collections.abc import Sequence
from typing import NoReturn

import pytest
from packaging.version import Version

#: Environment variable that flips the gate from "skip" to "fail". The name is
#: repeated as a plain string in ``docker/tools.Dockerfile`` and in the
#: ``test-integration`` service of ``docker-compose.yml``, which cannot import
#: it; ``tests/unit/test_workflow_wiring.py`` pins all three together.
TOOLS_IMAGE_ENV = "LINTRO_TOOLS_IMAGE"

#: Default timeout, in seconds, for a tool's version probe.
DEFAULT_TIMEOUT_SECONDS = 10.0

#: Timeout for probes that go through a package-runner launcher (bunx, npx),
#: which may resolve and download the package before answering.
LAUNCHER_TIMEOUT_SECONDS = 60.0

_VERSION_PATTERN = re.compile(r"(\d+\.\d+\.\d+)")


class MissingToolError(RuntimeError):
    """Raised when a required tool is unusable inside the tools image."""


def in_tools_image() -> bool:
    """Report whether the suite is running inside the lintro tools image.

    Returns:
        True when ``LINTRO_TOOLS_IMAGE`` is set to ``1``, False otherwise.
    """
    return os.environ.get(TOOLS_IMAGE_ENV, "") == "1"


def _which(name: str) -> str | None:
    """Resolve ``name`` on PATH.

    Indirection so tests can replace PATH resolution without mutating the
    real :mod:`shutil`.

    Args:
        name: Executable name to resolve.

    Returns:
        The resolved path, or None when the name is not on PATH.
    """
    return shutil.which(name)


def _run(
    command: Sequence[str],
    *,
    timeout: float,
    cwd: str | None,
) -> subprocess.CompletedProcess[str]:
    """Run a version probe.

    Indirection so tests can replace process execution without mutating the
    real :mod:`subprocess`.

    Args:
        command: Full argv of the probe.
        timeout: Seconds to wait before giving up.
        cwd: Directory to run the probe from; None uses the current directory.

    Returns:
        The completed process.
    """
    return subprocess.run(  # nosec B603 - fixed argv probe of a tool binary; shell=False, no user input
        list(command),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        cwd=cwd,
    )


def probe_command(
    command: Sequence[str],
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    cwd: str | None = None,
) -> str | None:
    """Run a version command and return its output when it succeeds.

    Args:
        command: Full argv of the version probe (e.g. ``["ruff", "--version"]``).
        timeout: Seconds to wait before giving up on the probe.
        cwd: Directory to run the probe from; None uses the current directory.

    Returns:
        The probe's combined stdout/stderr when it exits 0, otherwise None.
    """
    if _which(command[0]) is None:
        return None
    try:
        result = _run(command, timeout=timeout, cwd=cwd)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return f"{result.stdout}{result.stderr}"


def resolve_tool_command(
    name: str,
    *,
    version_args: Sequence[str] = ("--version",),
    launchers: Sequence[Sequence[str]] = (),
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> tuple[list[str], str] | None:
    """Resolve the first invocation of ``name`` whose version probe succeeds.

    The bare binary is tried first; each launcher prefix (``bunx``, ``npx``,
    …) is then tried in order, so a tool installed directly always wins over
    a package-runner fallback.

    Args:
        name: Executable name of the tool (e.g. ``"svelte-check"``).
        version_args: Arguments that make the tool print its version.
        launchers: Launcher prefixes to try when the bare binary fails.
        timeout: Seconds to wait for the bare binary's probe; launcher probes
            get :data:`LAUNCHER_TIMEOUT_SECONDS`.

    Returns:
        A ``(command_prefix, version_output)`` pair, or None when no
        invocation works.
    """
    candidates: list[tuple[list[str], float]] = [([name], timeout)]
    candidates.extend(
        ([*launcher, name], LAUNCHER_TIMEOUT_SECONDS) for launcher in launchers
    )
    for prefix, probe_timeout in candidates:
        output = probe_command([*prefix, *version_args], timeout=probe_timeout)
        if output is not None:
            return prefix, output
    return None


def tool_is_available(
    name: str,
    *,
    version_args: Sequence[str] = ("--version",),
    launchers: Sequence[Sequence[str]] = (),
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> bool:
    """Report whether ``name`` is installed and answers its version command.

    Args:
        name: Executable name of the tool.
        version_args: Arguments that make the tool print its version.
        launchers: Launcher prefixes to try when the bare binary fails.
        timeout: Seconds to wait for the bare binary's probe.

    Returns:
        True when some invocation of the tool exits 0, False otherwise.
    """
    resolved = resolve_tool_command(
        name,
        version_args=version_args,
        launchers=launchers,
        timeout=timeout,
    )
    return resolved is not None


def parse_version(output: str) -> Version | None:
    """Extract the first ``MAJOR.MINOR.PATCH`` version from probe output.

    Args:
        output: Combined stdout/stderr of a version probe.

    Returns:
        The parsed version, or None when the output carries no version.
    """
    match = _VERSION_PATTERN.search(output)
    if match is None:
        return None
    try:
        return Version(match.group(1))
    except ValueError:  # pragma: no cover - regex already constrains the shape
        return None


def unavailable(reason: str) -> NoReturn:
    """Fail inside the tools image, skip everywhere else.

    Use this for runtime prerequisites that :func:`require_tool` cannot
    express as a module-level mark (a fixture's missing input, a tool probed
    through a plugin's own resolution).

    Args:
        reason: Human-readable description of what is missing.

    Raises:
        MissingToolError: When running inside the tools image.
    """
    if in_tools_image():
        raise MissingToolError(f"{reason} (running with {TOOLS_IMAGE_ENV}=1)")
    pytest.skip(reason)


def gate(
    *,
    available: bool,
    reason: str,
) -> pytest.MarkDecorator:
    """Build the module-level mark for an already-resolved availability check.

    Args:
        available: Whether the prerequisite is usable in this environment.
        reason: Human-readable description of what is missing.

    Returns:
        A ``skipif`` mark that never triggers when the prerequisite is usable.

    Raises:
        MissingToolError: When the prerequisite is missing inside the tools
            image, so collection of the module fails the run.
    """
    if not available and in_tools_image():
        raise MissingToolError(f"{reason} (running with {TOOLS_IMAGE_ENV}=1)")
    return pytest.mark.skipif(not available, reason=reason)


def require_tool(
    name: str,
    *,
    version_args: Sequence[str] = ("--version",),
    launchers: Sequence[Sequence[str]] = (),
    min_version: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    label: str | None = None,
) -> pytest.MarkDecorator:
    """Gate a test module on ``name`` being installed and runnable.

    Args:
        name: Executable name of the tool (e.g. ``"shellcheck"``).
        version_args: Arguments that make the tool print its version.
        launchers: Launcher prefixes to try when the bare binary fails.
        min_version: Lowest acceptable version, as a string; when given, the
            probe output must carry a version at least this high.
        timeout: Seconds to wait for the bare binary's version probe.
        label: Name to use in the skip/failure message; defaults to ``name``.
            Set it where the binary and the tool differ (``cargo deny``).

    Returns:
        A mark suitable for assignment to ``pytestmark``.
    """
    shown = label or name
    resolved = resolve_tool_command(
        name,
        version_args=version_args,
        launchers=launchers,
        timeout=timeout,
    )
    if resolved is None:
        return gate(available=False, reason=f"{shown} is not installed or not runnable")
    if min_version is None:
        return gate(available=True, reason=f"{shown} is available")
    installed = parse_version(resolved[1])
    if installed is None or installed < Version(min_version):
        return gate(
            available=False,
            reason=(
                f"{shown} >= {min_version} required (found: {installed or 'unknown'})"
            ),
        )
    return gate(available=True, reason=f"{shown} >= {min_version} is available")


def require_command(
    name: str,
    command: Sequence[str] | None,
    *,
    version_args: Sequence[str] = ("--version",),
    timeout: float = LAUNCHER_TIMEOUT_SECONDS,
    cwd: str | None = None,
) -> pytest.MarkDecorator:
    """Gate a test module on an explicitly resolved command prefix.

    Used where the tool's invocation comes from the plugin's own resolution
    logic, so the probe cannot drift from production behaviour.

    Args:
        name: Human-readable tool name, used in the skip/failure message.
        command: Command prefix to probe, or None when resolution failed.
        version_args: Arguments that make the tool print its version.
        timeout: Seconds to wait for the version probe.
        cwd: Directory to run the probe from; None uses the current directory.

    Returns:
        A mark suitable for assignment to ``pytestmark``.
    """
    available = False
    if command:
        available = (
            probe_command(
                [*command, *version_args],
                timeout=timeout,
                cwd=cwd,
            )
            is not None
        )
    return gate(
        available=available,
        reason=f"{name} is not installed or not runnable",
    )
