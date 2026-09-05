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
import shutil
import subprocess  # nosec B404 - probes the tool under test; every call is shell=False
import warnings
from collections.abc import Sequence
from functools import lru_cache
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

#: Sentinel floor accepting any version, for the handful of tools whose
#: ``--version`` output does not describe the tool being gated.
NO_MIN_VERSION = "0"


@lru_cache(maxsize=1)
def _enforced_minimums() -> dict[str, str]:
    """Return the minimum versions lintro's plugins enforce at run time.

    Returns:
        Mapping of tool name (both hyphen and underscore spellings) to the
        lowest version the plugin will run; empty when lintro cannot be
        imported.
    """
    try:
        from lintro.tools.core.version_checking import get_minimum_versions

        return dict(get_minimum_versions())
    except Exception as exc:  # noqa: BLE001 - must not break collection
        # Fail open: with no floors every module keeps its absent-vs-present
        # gate, which is the behaviour that matters inside the tools image.
        # Warn rather than fail silently — losing the floors quietly is the
        # same class of silent pass this gate exists to remove.
        warnings.warn(
            f"could not read lintro's enforced tool minimums ({exc!r}); "
            "integration modules will not apply version floors",
            RuntimeWarning,
            stacklevel=2,
        )
        return {}


#: Env var through which CI tells lintro to keep running binaries that lag
#: the manifest during a digest-pinned image bump (#1582). Same contract as
#: ``lintro.plugins.execution_preparation``.
ALLOW_VERSION_LAG_ENV = "LINTRO_ALLOW_VERSION_LAG"


def version_lag_allowed(name: str) -> bool:
    """Report whether ``name`` may run despite lagging the manifest minimum.

    Mirrors lintro's own ``LINTRO_ALLOW_VERSION_LAG`` contract: a
    comma-separated tool list, or ``*`` for all. When CI has told the plugins
    to keep running a lagged binary, this gate must not skip the module out
    from under them, or the coverage the allowance exists to preserve is lost
    anyway.

    Args:
        name: Tool name to check.

    Returns:
        True when the tool is allow-listed for version lag.
    """
    raw = (os.environ.get(ALLOW_VERSION_LAG_ENV) or "").strip()
    if not raw:
        return False
    if raw == "*":
        return True
    return name.lower() in {
        part.strip().lower() for part in raw.split(",") if part.strip()
    }


def enforced_minimum(name: str) -> str | None:
    """Look up the minimum version lintro enforces for ``name``.

    Integration modules name the *executable* (``dotenv-linter``) while
    lintro names the *tool* (``dotenv_linter``), so the underscore spelling
    is tried as a fallback.

    Args:
        name: Tool or executable name (``shellcheck``, ``golangci-lint``).

    Returns:
        The enforced minimum, or None when lintro pins nothing for the name.
    """
    minimums = _enforced_minimums()
    return minimums.get(name) or minimums.get(name.replace("-", "_"))


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


def tool_runs_for_lintro(
    name: str,
    *,
    version_args: Sequence[str] = ("--version",),
    pin: str | None = None,
) -> bool:
    """Report whether lintro would actually execute ``name`` here.

    True only when the binary resolves *and* clears the same version floor
    :func:`require_tool` applies, so callers reason about "the plugin will
    run this" rather than the weaker "the binary exists".

    Args:
        name: Executable name of the tool.
        version_args: Arguments that make the tool print its version.
        pin: Lintro tool name to look the floor up under, when it differs.

    Returns:
        True when the tool resolves and is not below lintro's minimum.
    """
    resolved = resolve_tool_command(name, version_args=version_args)
    if resolved is None:
        return False
    mark = _version_gate(
        output=resolved[1],
        shown=name,
        tool=pin or name,
        min_version=None,
    )
    return not mark.args[0]


def parse_version(output: str, *, tool: str = "") -> Version | None:
    """Extract a tool's version from its probe output.

    Delegates to lintro's own parsing rather than re-implementing it: a
    private regex here would drift from what ``verify_tool_version`` reads,
    and this gate is only useful if it agrees with the plugin about which
    version is installed.

    Args:
        output: Combined stdout/stderr of a version probe.
        tool: Tool name, so tool-specific parsing rules apply.

    Returns:
        The parsed version, or None when the output carries no version.
    """
    try:
        from lintro.tools.core.version_parsing import (
            extract_version_from_output,
        )
        from lintro.tools.core.version_parsing import (
            parse_version as parse_version_string,
        )

        raw = extract_version_from_output(output, tool or "unknown")
        return parse_version_string(raw) if raw else None
    except Exception:  # noqa: BLE001 - an unknown tool name must not break collection
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


def _version_gate(
    *,
    output: str,
    shown: str,
    tool: str,
    min_version: str | None,
) -> pytest.MarkDecorator:
    """Decide a resolved tool's mark from its version.

    A shortfall is always a skip, never a failure — see :func:`require_tool`.

    Args:
        output: Combined output of the tool's version probe.
        shown: Name to use in the skip reason.
        tool: Tool name used for the floor lookup and version parsing.
        min_version: Explicit floor, or None to use lintro's enforced minimum.

    Returns:
        A mark suitable for assignment to ``pytestmark``.
    """
    floor = min_version if min_version is not None else enforced_minimum(tool)
    if floor is None or floor == NO_MIN_VERSION:
        return gate(available=True, reason=f"{shown} is available")

    installed = parse_version(output, tool=tool)
    if installed is None:
        # Unparsable version: run the module. lintro's own verify_tool_version
        # proceeds in this case rather than skipping, and inventing a skip here
        # would reintroduce the silent pass this gate exists to remove.
        return gate(available=True, reason=f"{shown} is available")
    if installed >= Version(floor):
        return gate(available=True, reason=f"{shown} >= {floor} is available")
    if version_lag_allowed(tool):
        # CI told the plugins to keep running this lagged binary during a
        # digest-pinned image bump (#1582); skipping the module here would
        # throw away exactly the coverage that allowance preserves.
        return gate(
            available=True,
            reason=f"{shown} lags {floor} but is allowed via {ALLOW_VERSION_LAG_ENV}",
        )
    # Skip-only: a version shortfall must never fail the run, even inside the
    # tools image.
    return pytest.mark.skipif(
        True,
        reason=(
            f"{shown} >= {floor} required, and lintro skips the tool below "
            f"that (found: {installed})"
        ),
    )


def require_tool(
    name: str,
    *,
    version_args: Sequence[str] = ("--version",),
    launchers: Sequence[Sequence[str]] = (),
    min_version: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    label: str | None = None,
    pin: str | None = None,
) -> pytest.MarkDecorator:
    """Gate a test module on ``name`` being installed and usable.

    Two distinct conditions, deliberately handled differently:

    * **Absent or unrunnable** — a skip outside the tools image, a hard
      failure inside it. That is the whole point of #465.
    * **Present but older than the minimum lintro's plugins enforce** — a
      skip everywhere, never a failure. Below that floor the plugin returns
      a *skipped* ToolResult with ``issues_count=0``, so a module asserting
      on real findings would fail against a tool that never ran. This is how
      an ambient ``shellcheck`` on the hosted runner broke the suite. Version
      drift inside the image is owned by the manifest gate, which tolerates
      Renovate lag via ``--allow-version-lag`` (#1582); duplicating that
      enforcement here would turn every tool bump into a red required check.

    The floor defaults to whatever lintro itself enforces, so a module never
    has to restate it and cannot drift from the plugin.

    Args:
        name: Executable name of the tool (e.g. ``"shellcheck"``).
        version_args: Arguments that make the tool print its version.
        launchers: Launcher prefixes to try when the bare binary fails.
        min_version: Lowest acceptable version. Defaults to the minimum
            lintro enforces; pass :data:`NO_MIN_VERSION` to accept any.
        timeout: Seconds to wait for the bare binary's version probe.
        label: Name to use in the skip/failure message; defaults to ``name``.
            Set it where the binary and the tool differ (``cargo deny``).
        pin: Lintro tool name to look the floor up under, when it differs
            from the executable name (``cargo`` runs ``cargo_deny``).

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

    return _version_gate(
        output=resolved[1],
        shown=shown,
        tool=pin or name,
        min_version=min_version,
    )


def require_command(
    name: str,
    command: Sequence[str] | None,
    *,
    version_args: Sequence[str] = ("--version",),
    timeout: float = LAUNCHER_TIMEOUT_SECONDS,
    cwd: str | None = None,
    min_version: str | None = None,
    pin: str | None = None,
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
        min_version: Lowest acceptable version. Defaults to the minimum
            lintro enforces; pass :data:`NO_MIN_VERSION` to accept any.
        pin: Lintro tool name to look the floor up under, when it differs
            from ``name``.

    Returns:
        A mark suitable for assignment to ``pytestmark``.
    """
    output = (
        probe_command([*command, *version_args], timeout=timeout, cwd=cwd)
        if command
        else None
    )
    if output is None:
        return gate(
            available=False,
            reason=f"{name} is not installed or not runnable",
        )
    # Same floor as require_tool: a below-minimum binary makes the plugin
    # return a skipped ToolResult, so the module would assert on a tool that
    # never ran (the shellcheck 0.9.0 failure mode, #465).
    return _version_gate(
        output=output,
        shown=name,
        tool=pin or name,
        min_version=min_version,
    )
