"""Unit tests for the integration suite's tool-availability gate (#465).

``tests/integration/_tools.py`` decides whether a missing wrapped tool skips a
module or fails the run. These tests pin that decision without touching the
real PATH: the helper's own ``_which`` and ``_run`` seams are monkeypatched —
deliberately, so the stdlib :mod:`shutil` and :mod:`subprocess` are left alone
— and the ``LINTRO_TOOLS_IMAGE`` switch is set through ``monkeypatch.setenv``.
"""

from __future__ import annotations

import subprocess  # nosec B404 - only referenced to build fake CompletedProcess objects
from collections.abc import Callable, Sequence
from typing import Any

import pytest
from assertpy import assert_that

from tests.integration import _tools
from tests.integration._tools import (
    LAUNCHER_TIMEOUT_SECONDS,
    NO_MIN_VERSION,
    MissingToolError,
    enforced_minimum,
    in_tools_image,
    parse_version,
    require_command,
    require_tool,
    tool_is_available,
    unavailable,
)

_WHICH_TARGET = "tests.integration._tools._which"
_RUN_TARGET = "tests.integration._tools._run"


def _fake_which(*, present: Sequence[str]) -> Callable[[str], str | None]:
    """Build a PATH-resolution replacement that resolves only ``present``.

    Args:
        present: Executable names that should resolve.

    Returns:
        A callable with :func:`tests.integration._tools._which`'s signature.
    """

    def _resolve(name: str) -> str | None:
        """Resolve ``name`` only when it is in the allow-list.

        Args:
            name: Executable name being resolved.

        Returns:
            A fake absolute path, or None when ``name`` is not present.
        """
        return f"/usr/bin/{name}" if name in present else None

    return _resolve


def _fake_run(
    *,
    returncode: int,
    stdout: str = "",
    exc: BaseException | None = None,
    calls: list[tuple[list[str], dict[str, Any]]] | None = None,
) -> Callable[..., subprocess.CompletedProcess[str]]:
    """Build a process-execution replacement with a fixed outcome.

    Args:
        returncode: Exit code the fake probe reports.
        stdout: Text the fake probe writes to stdout.
        exc: Exception to raise instead of returning, when given.
        calls: List that each invocation's ``(argv, kwargs)`` is appended to,
            so tests can assert the probe wiring and not just its verdict.

    Returns:
        A callable with :func:`tests.integration._tools._run`'s signature.
    """
    if calls is None:
        calls = []

    def _execute(
        command: Sequence[str],
        **kwargs: Any,
    ) -> subprocess.CompletedProcess[str]:
        """Record the invocation and return a canned completed process.

        Args:
            command: Command argv.
            **kwargs: Probe keyword arguments (``timeout``, ``cwd``).

        Returns:
            A CompletedProcess carrying the configured outcome.

        Raises:
            exc: The configured exception, when one was given.
        """
        calls.append((list(command), kwargs))
        if exc is not None:
            raise exc
        return subprocess.CompletedProcess(
            args=list(command),
            returncode=returncode,
            stdout=stdout,
            stderr="",
        )

    return _execute


@pytest.fixture
def outside_tools_image(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear the tools-image switch so the gate skips rather than fails.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.delenv(_tools.TOOLS_IMAGE_ENV, raising=False)


@pytest.fixture
def inside_tools_image(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set the tools-image switch so the gate fails rather than skips.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setenv(_tools.TOOLS_IMAGE_ENV, "1")


@pytest.mark.parametrize(
    ("value", "expected"),
    [("1", True), ("0", False), ("true", False), ("", False)],
    ids=["one", "zero", "true", "empty"],
)
def test_in_tools_image_only_accepts_one(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
    expected: bool,
) -> None:
    """Only the exact string ``1`` flips the gate to fail-on-missing.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        value: Value assigned to the switch variable.
        expected: Expected verdict.
    """
    monkeypatch.setenv(_tools.TOOLS_IMAGE_ENV, value)
    assert_that(in_tools_image()).is_equal_to(expected)


def test_tool_is_available_reports_true_for_a_working_binary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A resolvable binary whose version command exits 0 is available.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setattr(_WHICH_TARGET, _fake_which(present=("ruff",)))
    monkeypatch.setattr(
        _RUN_TARGET,
        _fake_run(returncode=0, stdout="ruff 0.15.9\n"),
    )
    assert_that(tool_is_available("ruff")).is_true()


def test_tool_is_available_rejects_a_binary_whose_version_command_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A wrapper script that exists but cannot run counts as unavailable.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setattr(_WHICH_TARGET, _fake_which(present=("oxlint",)))
    monkeypatch.setattr(_RUN_TARGET, _fake_run(returncode=127))
    assert_that(tool_is_available("oxlint")).is_false()


def test_tool_is_available_rejects_a_binary_missing_from_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A name that does not resolve is unavailable without running anything.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setattr(_WHICH_TARGET, _fake_which(present=()))
    monkeypatch.setattr(
        _RUN_TARGET,
        _fake_run(returncode=0, exc=AssertionError("probe must not run")),
    )
    assert_that(tool_is_available("shellcheck")).is_false()


@pytest.mark.parametrize(
    "exc",
    [
        subprocess.TimeoutExpired(cmd="tsc", timeout=10.0),
        OSError("exec format error"),
    ],
    ids=["timeout", "oserror"],
)
def test_tool_is_available_swallows_probe_failures(
    monkeypatch: pytest.MonkeyPatch,
    exc: Exception,
) -> None:
    """A hung or unexecutable probe resolves to unavailable, not an error.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        exc: Exception the probe raises.
    """
    monkeypatch.setattr(_WHICH_TARGET, _fake_which(present=("tsc",)))
    monkeypatch.setattr(_RUN_TARGET, _fake_run(returncode=0, exc=exc))
    assert_that(tool_is_available("tsc")).is_false()


def test_resolve_tool_command_falls_back_to_a_launcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tool reachable only through ``bunx`` resolves to the launcher argv.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    monkeypatch.setattr(_WHICH_TARGET, _fake_which(present=("bunx",)))
    monkeypatch.setattr(
        _RUN_TARGET,
        _fake_run(returncode=0, stdout="svelte-check 4.0.0\n"),
    )
    resolved = _tools.resolve_tool_command(
        "svelte-check",
        launchers=(("bunx",), ("npx",)),
    )
    assert_that(resolved).is_not_none()
    assert resolved is not None
    assert_that(resolved[0]).is_equal_to(["bunx", "svelte-check"])


@pytest.mark.parametrize(
    ("output", "tool", "expected"),
    [
        ("rustfmt 1.8.0-stable (abc1234 2026-01-01)", "rustfmt", "1.8.0"),
        ("cargo-deny 0.14.3", "cargo_deny", "0.14.3"),
        # Two-part versions are real (taplo prints "taplo 25.1"); a private
        # MAJOR.MINOR.PATCH regex here would read them as unparsable and
        # silently drop the floor.
        ("taplo 25.1", "taplo", "25.1"),
        ("no version here", "ruff", None),
    ],
    ids=["rustfmt", "cargo-deny", "two-part", "unparsable"],
)
def test_parse_version_matches_lintros_own_parsing(
    output: str,
    tool: str,
    expected: str | None,
) -> None:
    """Version parsing is delegated to lintro so the gate cannot drift.

    Args:
        output: Combined probe output.
        tool: Tool name, so tool-specific parsing rules apply.
        expected: Expected version string, or None.
    """
    parsed = parse_version(output, tool=tool)
    assert_that(None if parsed is None else str(parsed)).is_equal_to(expected)


def test_require_tool_skips_outside_the_tools_image(
    monkeypatch: pytest.MonkeyPatch,
    outside_tools_image: None,
) -> None:
    """A missing tool yields a triggered skipif mark on a developer machine.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        outside_tools_image: Fixture clearing the tools-image switch.
    """
    monkeypatch.setattr(_WHICH_TARGET, _fake_which(present=()))
    mark = require_tool("shellcheck")
    assert_that(mark.args[0]).is_true()
    assert_that(mark.kwargs["reason"]).contains("shellcheck")


def test_require_tool_does_not_skip_when_the_tool_works(
    monkeypatch: pytest.MonkeyPatch,
    outside_tools_image: None,
) -> None:
    """A working, new-enough tool yields an inert mark so the module runs.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        outside_tools_image: Fixture clearing the tools-image switch.
    """
    monkeypatch.setattr(_WHICH_TARGET, _fake_which(present=("shellcheck",)))
    monkeypatch.setattr(
        _RUN_TARGET,
        _fake_run(returncode=0, stdout="ShellCheck\nversion: 99.0.0\n"),
    )
    mark = require_tool("shellcheck")
    assert_that(mark.args[0]).is_false()


def test_require_tool_runs_when_the_version_cannot_be_parsed(
    monkeypatch: pytest.MonkeyPatch,
    outside_tools_image: None,
) -> None:
    """An unparsable version runs the module rather than skipping it.

    lintro's own verify_tool_version proceeds when it cannot parse a version,
    and inventing a skip here would reintroduce the silent pass this gate
    exists to remove.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        outside_tools_image: Fixture clearing the tools-image switch.
    """
    monkeypatch.setattr(_WHICH_TARGET, _fake_which(present=("shellcheck",)))
    monkeypatch.setattr(
        _RUN_TARGET,
        _fake_run(returncode=0, stdout="ShellCheck - shell script analysis tool\n"),
    )

    mark = require_tool("shellcheck")

    assert_that(mark.args[0]).is_false()


def test_require_tool_fails_inside_the_tools_image(
    monkeypatch: pytest.MonkeyPatch,
    inside_tools_image: None,
) -> None:
    """A missing tool inside the image raises instead of skipping.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        inside_tools_image: Fixture setting the tools-image switch.
    """
    monkeypatch.setattr(_WHICH_TARGET, _fake_which(present=()))
    with pytest.raises(MissingToolError):
        require_tool("shellcheck")


def test_require_tool_enforces_a_minimum_version(
    monkeypatch: pytest.MonkeyPatch,
    outside_tools_image: None,
) -> None:
    """A too-old tool is gated off.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        outside_tools_image: Fixture clearing the tools-image switch.
    """
    monkeypatch.setattr(_WHICH_TARGET, _fake_which(present=("rustfmt",)))
    monkeypatch.setattr(
        _RUN_TARGET,
        _fake_run(returncode=0, stdout="rustfmt 1.7.0-stable\n"),
    )
    mark = require_tool("rustfmt", min_version="1.8.0")
    assert_that(mark.args[0]).is_true()
    assert_that(mark.kwargs["reason"]).contains("1.8.0", "1.7.0")


def test_require_tool_defaults_the_floor_to_what_lintro_enforces(
    monkeypatch: pytest.MonkeyPatch,
    outside_tools_image: None,
) -> None:
    """An ambient tool below lintro's own minimum skips without being told.

    Below that floor the plugin returns a skipped ToolResult with zero
    issues, so a module asserting on findings would fail against a tool that
    never ran — the hosted-runner shellcheck regression (#465).

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        outside_tools_image: Fixture clearing the tools-image switch.
    """
    floor = enforced_minimum("shellcheck")
    assert_that(floor).is_not_none()
    monkeypatch.setattr(_WHICH_TARGET, _fake_which(present=("shellcheck",)))
    monkeypatch.setattr(
        _RUN_TARGET,
        _fake_run(returncode=0, stdout="ShellCheck\nversion: 0.0.1\n"),
    )

    mark = require_tool("shellcheck")

    assert_that(mark.args[0]).is_true()
    assert_that(mark.kwargs["reason"]).contains("shellcheck", str(floor), "0.0.1")


def test_require_tool_never_fails_the_image_on_a_version_shortfall(
    monkeypatch: pytest.MonkeyPatch,
    inside_tools_image: None,
) -> None:
    """A too-old-but-present tool skips inside the image instead of raising.

    Version drift inside the image is owned by the manifest gate, which
    tolerates Renovate lag; failing here too would turn every tool bump into
    a red required check.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        inside_tools_image: Fixture setting the tools-image switch.
    """
    monkeypatch.setattr(_WHICH_TARGET, _fake_which(present=("shellcheck",)))
    monkeypatch.setattr(
        _RUN_TARGET,
        _fake_run(returncode=0, stdout="ShellCheck\nversion: 0.0.1\n"),
    )

    mark = require_tool("shellcheck")

    assert_that(mark.args[0]).is_true()


def test_require_tool_accepts_any_version_under_the_sentinel(
    monkeypatch: pytest.MonkeyPatch,
    outside_tools_image: None,
) -> None:
    """NO_MIN_VERSION opts out where the probe describes a different tool.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        outside_tools_image: Fixture clearing the tools-image switch.
    """
    monkeypatch.setattr(_WHICH_TARGET, _fake_which(present=("vue-tsc",)))
    monkeypatch.setattr(
        _RUN_TARGET,
        _fake_run(returncode=0, stdout="Version 0.0.1\n"),
    )

    mark = require_tool("vue-tsc", min_version=NO_MIN_VERSION)

    assert_that(mark.args[0]).is_false()


@pytest.mark.parametrize(
    ("name", "pinned"),
    [("shellcheck", True), ("golangci-lint", True), ("git", False)],
    ids=["underscore-free", "hyphenated", "unpinned"],
)
def test_enforced_minimum_resolves_executable_spellings(
    name: str,
    pinned: bool,
) -> None:
    """Executable names resolve to lintro's tool-name pins where they exist.

    Args:
        name: Executable name as an integration module spells it.
        pinned: Whether lintro pins a minimum for that tool.
    """
    resolved = enforced_minimum(name)
    if pinned:
        assert_that(resolved).is_not_none()
    else:
        assert_that(resolved).is_none()


def test_require_tool_accepts_a_new_enough_version(
    monkeypatch: pytest.MonkeyPatch,
    outside_tools_image: None,
) -> None:
    """A tool at or above the floor runs, and the label names the tool.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        outside_tools_image: Fixture clearing the tools-image switch.
    """
    monkeypatch.setattr(_WHICH_TARGET, _fake_which(present=("cargo",)))
    monkeypatch.setattr(
        _RUN_TARGET,
        _fake_run(returncode=0, stdout="cargo-deny 0.20.1\n"),
    )
    mark = require_tool(
        "cargo",
        version_args=("deny", "--version"),
        min_version="0.14.0",
        label="cargo-deny",
    )
    assert_that(mark.args[0]).is_false()
    assert_that(mark.kwargs["reason"]).contains("cargo-deny")


def test_require_command_treats_unresolved_commands_as_missing(
    outside_tools_image: None,
) -> None:
    """A plugin that could not resolve its CLI gates the module off.

    Args:
        outside_tools_image: Fixture clearing the tools-image switch.
    """
    mark = require_command("spectral", None)
    assert_that(mark.args[0]).is_true()
    assert_that(mark.kwargs["reason"]).contains("spectral")


def test_unavailable_skips_outside_the_tools_image(
    outside_tools_image: None,
) -> None:
    """The runtime guard raises pytest's Skipped outside the image.

    Args:
        outside_tools_image: Fixture clearing the tools-image switch.
    """
    with pytest.raises(BaseException) as excinfo:
        unavailable("node_modules not found")
    assert_that(excinfo.type.__name__).is_equal_to("Skipped")


def test_unavailable_fails_inside_the_tools_image(
    inside_tools_image: None,
) -> None:
    """The runtime guard raises MissingToolError inside the image.

    Args:
        inside_tools_image: Fixture setting the tools-image switch.
    """
    with pytest.raises(MissingToolError):
        unavailable("node_modules not found")


def test_probe_passes_version_args_and_launcher_timeout(
    monkeypatch: pytest.MonkeyPatch,
    outside_tools_image: None,
) -> None:
    """The probe runs the requested argv, with the launcher timeout on fallback.

    Guards the wiring itself: dropping ``version_args`` or the longer
    launcher timeout would otherwise leave every assertion in this file
    passing.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        outside_tools_image: Fixture clearing the tools-image switch.
    """
    calls: list[tuple[list[str], dict[str, Any]]] = []
    monkeypatch.setattr(_WHICH_TARGET, _fake_which(present=("bunx",)))
    monkeypatch.setattr(
        _RUN_TARGET,
        _fake_run(returncode=0, stdout="svelte-check 9.9.9\n", calls=calls),
    )

    require_tool(
        "svelte-check",
        version_args=("--version", "--json"),
        launchers=(("bunx",),),
    )

    assert_that(calls).is_length(1)
    argv, kwargs = calls[0]
    assert_that(argv).is_equal_to(["bunx", "svelte-check", "--version", "--json"])
    assert_that(kwargs["timeout"]).is_equal_to(LAUNCHER_TIMEOUT_SECONDS)


def test_require_command_applies_the_same_version_floor(
    monkeypatch: pytest.MonkeyPatch,
    outside_tools_image: None,
) -> None:
    """A plugin-resolved CLI below lintro's floor skips like require_tool.

    stylelint, html-validate, markdownlint-cli2 and spectral are gated
    through require_command; without the floor they would replay the
    shellcheck 0.9.0 failure mode on the hosted matrix.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        outside_tools_image: Fixture clearing the tools-image switch.
    """
    monkeypatch.setattr(_WHICH_TARGET, _fake_which(present=("stylelint",)))
    monkeypatch.setattr(
        _RUN_TARGET,
        _fake_run(returncode=0, stdout="0.0.1\n"),
    )

    mark = require_command("stylelint", ["stylelint"])

    assert_that(mark.args[0]).is_true()
    assert_that(mark.kwargs["reason"]).contains("stylelint")


def test_require_command_fails_inside_the_image_when_unresolved(
    inside_tools_image: None,
) -> None:
    """An unresolved plugin CLI is a hard failure inside the tools image.

    Args:
        inside_tools_image: Fixture setting the tools-image switch.
    """
    with pytest.raises(MissingToolError):
        require_command("spectral", None)


def test_version_lag_allowance_keeps_a_lagged_tool_running(
    monkeypatch: pytest.MonkeyPatch,
    outside_tools_image: None,
) -> None:
    """LINTRO_ALLOW_VERSION_LAG keeps a below-floor module collecting.

    CI sets it during a digest-pinned image bump so the plugins keep running
    lagged binaries (#1582); skipping the module would discard exactly the
    coverage the allowance preserves.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        outside_tools_image: Fixture clearing the tools-image switch.
    """
    monkeypatch.setattr(_WHICH_TARGET, _fake_which(present=("shellcheck",)))
    monkeypatch.setattr(
        _RUN_TARGET,
        _fake_run(returncode=0, stdout="ShellCheck\nversion: 0.0.1\n"),
    )

    monkeypatch.delenv(_tools.ALLOW_VERSION_LAG_ENV, raising=False)
    assert_that(require_tool("shellcheck").args[0]).is_true()

    monkeypatch.setenv(_tools.ALLOW_VERSION_LAG_ENV, "shellcheck,ruff")
    assert_that(require_tool("shellcheck").args[0]).is_false()

    monkeypatch.setenv(_tools.ALLOW_VERSION_LAG_ENV, "*")
    assert_that(require_tool("shellcheck").args[0]).is_false()


def test_missing_minimums_warn_instead_of_silently_dropping_floors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broken minimums lookup warns rather than quietly disabling floors.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """

    def _boom() -> dict[str, str]:
        """Stand in for a lookup that raises.

        Returns:
            Never returns.

        Raises:
            RuntimeError: Always.
        """
        raise RuntimeError("manifest unreadable")

    monkeypatch.setattr(
        "lintro.tools.core.version_checking.get_minimum_versions",
        _boom,
    )
    _tools._enforced_minimums.cache_clear()
    try:
        with pytest.warns(RuntimeWarning, match="enforced tool minimums"):
            assert_that(_tools.enforced_minimum("shellcheck")).is_none()
    finally:
        _tools._enforced_minimums.cache_clear()


def test_require_tool_does_not_skip_inside_the_image_when_the_tool_works(
    monkeypatch: pytest.MonkeyPatch,
    inside_tools_image: None,
) -> None:
    """A working, new-enough tool collects normally inside the tools image.

    The other inside-image cases all assert a skip or a raise, so a gate that
    skipped unconditionally under ``LINTRO_TOOLS_IMAGE=1`` would satisfy them
    and turn the required Docker run into hundreds of silent skips. This pins
    the happy path so that mutation fails.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        inside_tools_image: Fixture setting the tools-image switch.
    """
    monkeypatch.setattr(_WHICH_TARGET, _fake_which(present=("shellcheck",)))
    monkeypatch.setattr(
        _RUN_TARGET,
        _fake_run(returncode=0, stdout="ShellCheck\nversion: 99.0.0\n"),
    )

    assert_that(require_tool("shellcheck").args[0]).is_false()
    assert_that(require_command("shellcheck", ["shellcheck"]).args[0]).is_false()


def test_version_lag_allowance_matches_hyphenated_executable_names(
    monkeypatch: pytest.MonkeyPatch,
    outside_tools_image: None,
) -> None:
    """The lag allow-list folds ``-`` and ``_`` on both sides.

    CI fills ``LINTRO_ALLOW_VERSION_LAG`` from the manifest names
    (``html_validate``, ``golangci_lint``) while integration modules gate on
    the executable spelling (``html-validate``). Without the fold, an
    allow-listed lagging tool would still skip its module and discard exactly
    the coverage the allowance preserves (#1582).

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        outside_tools_image: Fixture clearing the tools-image switch.
    """
    monkeypatch.setenv(_tools.ALLOW_VERSION_LAG_ENV, "html_validate,golangci_lint")

    assert_that(_tools.version_lag_allowed("html-validate")).is_true()
    assert_that(_tools.version_lag_allowed("golangci-lint")).is_true()
    assert_that(_tools.version_lag_allowed("html_validate")).is_true()
    assert_that(_tools.version_lag_allowed("shellcheck")).is_false()

    monkeypatch.setenv(_tools.ALLOW_VERSION_LAG_ENV, "html-validate")
    assert_that(_tools.version_lag_allowed("html_validate")).is_true()


def test_require_command_pin_applies_the_floor_to_a_renamed_executable(
    monkeypatch: pytest.MonkeyPatch,
    outside_tools_image: None,
) -> None:
    """``pin`` makes the floor reachable when the binary is not the tool name.

    ``markdownlint-cli2`` is the npm package; lintro registers the tool as
    ``markdownlint``. Version parsing is keyed on lintro's tool name, so
    without the pin the parse returns None, the floor is silently dropped and
    an old CLI runs the module against a plugin that skipped the tool (#465).

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        outside_tools_image: Fixture clearing the tools-image switch.
    """
    monkeypatch.setattr(_WHICH_TARGET, _fake_which(present=("markdownlint-cli2",)))
    monkeypatch.setattr(
        _RUN_TARGET,
        _fake_run(
            returncode=0,
            stdout="markdownlint-cli2 v0.0.1 (markdownlint v0.0.1)",
        ),
    )

    assert_that(
        parse_version("markdownlint-cli2 v0.0.1", tool="markdownlint-cli2"),
    ).is_none()
    assert_that(
        require_command("markdownlint-cli2", ["markdownlint-cli2"]).args[0],
    ).is_false()

    mark = require_command(
        "markdownlint-cli2",
        ["markdownlint-cli2"],
        pin="markdownlint",
    )

    assert_that(mark.args[0]).is_true()
    assert_that(mark.kwargs["reason"]).contains("markdownlint-cli2", "0.0.1")
