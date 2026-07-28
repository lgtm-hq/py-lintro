"""Tests for the agent-CLI contract verifier that backs the tier-1 gate (#1614).

The verifier decides what counts as drift worth failing a build over. Two
distinctions carry the design and are pinned here: a *required* flag going missing
is fatal (lintro always sends it and cannot degrade), while an *optional* flag
going missing is not (the guard drops it before sending). And an unreadable help
surface yields no findings at all — absence of evidence must never be reported as
evidence of absence, or a CLI whose help momentarily fails would fail the gate for
every flag at once.
"""

from __future__ import annotations

import stat
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.ai.provider_enum import AIProvider
from lintro.ai.providers.cli_contract_check import (
    CliSurfaceReport,
    declared_cli_providers,
    probe_cli_surface,
)
from lintro.ai.providers.cli_contracts import CliContract, cli_contract_for

_CONTRACT = CliContract(
    binary="fake-agent",
    display_name="Fake",
    upgrade_hint="Upgrade the fake CLI.",
    version_floor=(2, 0, 0),
    required_flags=("--always", "--print"),
    optional_flags=cli_contract_for(AIProvider.ANTHROPIC).optional_flags,
)


def _report(**overrides: object) -> CliSurfaceReport:
    """Build a surface report with sensible conforming defaults.

    Args:
        **overrides: Fields to override on the conforming baseline.

    Returns:
        The constructed report.
    """
    fields: dict[str, object] = {
        "provider": AIProvider.ANTHROPIC,
        "contract": _CONTRACT,
        "binary_path": "/usr/local/bin/fake-agent",
        "version": (2, 5, 0),
        "help_readable": True,
    }
    fields.update(overrides)
    return CliSurfaceReport(**fields)  # type: ignore[arg-type]


# --- verdict semantics -------------------------------------------------------


def test_conforming_cli_has_no_violations() -> None:
    """A present, current, fully advertised CLI passes cleanly."""
    report = _report()

    assert_that(report.binary_present).is_true()
    assert_that(report.meets_version_floor).is_true()
    assert_that(list(report.violations)).is_empty()
    assert_that(report.describe()).contains("contract satisfied")


def test_absent_binary_is_the_only_reported_violation() -> None:
    """A missing binary short-circuits: later checks would be meaningless."""
    report = _report(binary_path=None, version=None, help_readable=False)

    assert_that(report.binary_present).is_false()
    assert_that(report.violations).is_length(1)
    assert_that(report.violations[0]).contains("not found on PATH")
    assert_that(report.violations[0]).contains("Upgrade the fake CLI.")


def test_below_floor_version_is_a_violation() -> None:
    """A binary predating the flag surface lintro drives fails the gate."""
    report = _report(version=(1, 9, 9))

    assert_that(report.meets_version_floor).is_false()
    assert_that(" ".join(report.violations)).contains("below the declared floor")


def test_unknown_version_is_not_a_violation() -> None:
    """An unreadable version can have causes unrelated to compatibility."""
    report = _report(version=None)

    assert_that(report.meets_version_floor).is_true()
    assert_that(list(report.violations)).is_empty()


def test_missing_required_flag_is_a_violation() -> None:
    """Required flags are never gated at runtime, so their absence is fatal."""
    report = _report(missing_required_flags=("--always",))

    assert_that(" ".join(report.violations)).contains("--always")


def test_unadvertised_optional_flag_is_not_a_violation() -> None:
    """The guard drops optional flags before sending them, so drift degrades."""
    report = _report(unadvertised_optional_flags=("--json-schema-name",))

    assert_that(list(report.violations)).is_empty()


def test_describe_flags_an_unreadable_help_surface() -> None:
    """A verdict reached without help output says so, so it is not over-trusted."""
    report = _report(help_readable=False)

    assert_that(report.describe()).contains("help unreadable")


# --- probing a real binary ---------------------------------------------------


def _install_fake_binary(
    *,
    directory: Path,
    name: str,
    version_output: str,
    help_output: str,
    help_exit: int = 0,
) -> None:
    """Write an executable stub that answers ``--version`` and ``--help``.

    Args:
        directory: Directory to place the stub in.
        name: Executable name.
        version_output: Text printed for ``--version``.
        help_output: Text printed for the help probe.
        help_exit: Exit status the help probe reports.
    """
    script = directory / name
    script.write_text(
        "#!/bin/sh\n"
        'case "$1" in\n'
        f'  --version) printf "%s\\n" "{version_output}" ;;\n'
        f'  *) printf "%s\\n" "{help_output}"; exit {help_exit} ;;\n'
        "esac\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


async def test_probe_reports_absent_binary_without_raising(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An uninstalled CLI yields a report, so callers choose skip or fail.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Empty directory used as the whole PATH.
    """
    monkeypatch.setenv("PATH", str(tmp_path))

    report = await probe_cli_surface(provider=AIProvider.ANTHROPIC)

    assert_that(report.binary_present).is_false()
    assert_that(report.help_readable).is_false()
    assert_that(report.describe()).contains("not installed")


async def test_probe_reads_version_and_flag_surface(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A conforming stub is read as conforming, end to end.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Directory holding the stub binary.
    """
    contract = cli_contract_for(AIProvider.ANTHROPIC)
    help_output = " ".join(contract.required_flags)
    _install_fake_binary(
        directory=tmp_path,
        name=contract.binary,
        version_output="9.9.9 (Claude Code)",
        help_output=help_output,
    )
    monkeypatch.setenv("PATH", str(tmp_path))

    report = await probe_cli_surface(provider=AIProvider.ANTHROPIC)

    assert_that(report.binary_present).is_true()
    assert_that(report.version).is_equal_to((9, 9, 9))
    assert_that(report.help_readable).is_true()
    assert_that(report.missing_required_flags).is_empty()
    assert_that(list(report.violations)).is_empty()


async def test_probe_detects_a_removed_required_flag(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The #1611 failure mode is caught: a flag lintro sends has vanished.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Directory holding the stub binary.
    """
    contract = cli_contract_for(AIProvider.ANTHROPIC)
    dropped, *kept = contract.required_flags
    _install_fake_binary(
        directory=tmp_path,
        name=contract.binary,
        version_output="9.9.9",
        help_output=" ".join(kept),
    )
    monkeypatch.setenv("PATH", str(tmp_path))

    report = await probe_cli_surface(provider=AIProvider.ANTHROPIC)

    assert_that(report.missing_required_flags).contains(dropped)
    assert_that(" ".join(report.violations)).contains(dropped)


async def test_probe_distrusts_a_non_zero_help_probe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A failed help probe reports nothing rather than everything as missing.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Directory holding the stub binary.
    """
    contract = cli_contract_for(AIProvider.ANTHROPIC)
    _install_fake_binary(
        directory=tmp_path,
        name=contract.binary,
        version_output="9.9.9",
        help_output="unexpected internal error",
        help_exit=1,
    )
    monkeypatch.setenv("PATH", str(tmp_path))

    report = await probe_cli_surface(provider=AIProvider.ANTHROPIC)

    assert_that(report.help_readable).is_false()
    assert_that(report.missing_required_flags).is_empty()
    assert_that(list(report.violations)).is_empty()


# --- registry coverage -------------------------------------------------------


def test_declared_providers_cover_every_contract() -> None:
    """The tier-1 suite parametrises from here, so it must not under-report."""
    providers = declared_cli_providers()

    # Every provider, not just a count: a provider added to the enum without a
    # declared contract would silently drop out of the tier-1 gate.
    assert_that(set(providers)).is_equal_to(set(AIProvider))
    for provider in providers:
        assert_that(cli_contract_for(provider).binary).is_not_empty()
