"""Tier 1: assert each installed agent CLI still offers the flags lintro sends.

Free by construction — only ``--version`` and ``--help`` are run, so this tier
costs no quota and needs no credential. That is what makes it viable as a required
gate, and a required gate is what was missing when
``@anthropic-ai/claude-code`` 2.1.218 removed ``--json-schema-name``: lintro kept
sending it, every CLI review broke, and CI stayed green (#1611).

Assertions run against :mod:`lintro.ai.providers.cli_contracts`, the same
single source of truth the runtime guard reads, so a contract edit and this gate
can never disagree.
"""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.ai.provider_enum import AIProvider
from lintro.ai.providers.cli_contract_check import (
    CliSurfaceReport,
    declared_cli_providers,
    probe_cli_surface_sync,
)
from lintro.ai.providers.cli_contracts import cli_contract_for
from tests.contract.gating import unmet_precondition

pytestmark = pytest.mark.contract_tier1


def _require_surface(provider: AIProvider) -> CliSurfaceReport:
    """Probe a provider's CLI, refusing to pass when the probe is meaningless.

    Args:
        provider: Provider whose CLI contract is under test.

    Returns:
        A report whose binary was found and whose help text was readable.
    """
    report = probe_cli_surface_sync(provider=provider)
    if not report.binary_present:
        unmet_precondition(
            f"{report.contract.binary} is not on PATH "
            f"({report.contract.upgrade_hint})",
        )
    if not report.help_readable:
        unmet_precondition(
            f"{report.contract.binary} "
            f"{' '.join(report.contract.help_args)} did not exit cleanly, so its "
            "flag surface cannot be read",
        )
    return report


def test_declared_contracts_are_not_empty() -> None:
    """Every declared contract must name a binary and at least one flag."""
    providers = declared_cli_providers()
    assert_that(providers).is_not_empty()
    for provider in providers:
        contract = cli_contract_for(provider)
        assert_that(contract.binary).is_not_empty()
        assert_that(contract.required_flags).is_not_empty()


def test_installed_cli_advertises_every_required_flag(
    cli_provider: AIProvider,
) -> None:
    """Required flags are never gated at runtime, so they must still exist.

    Args:
        cli_provider: Provider whose CLI contract is under test.
    """
    report = _require_surface(cli_provider)
    assert_that(report.missing_required_flags).described_as(
        f"{report.contract.binary} dropped required flag(s) lintro always sends; "
        f"update lintro/ai/providers/cli_contracts.py and the provider argv. "
        f"{report.describe()}",
    ).is_empty()


def test_installed_cli_meets_declared_version_floor(
    cli_provider: AIProvider,
) -> None:
    """The installed binary must not predate the flag surface lintro drives.

    Args:
        cli_provider: Provider whose CLI contract is under test.
    """
    report = _require_surface(cli_provider)
    assert_that(report.meets_version_floor).described_as(
        f"installed binary is below the declared floor. {report.describe()}",
    ).is_true()


def test_installed_cli_has_no_contract_violations(
    cli_provider: AIProvider,
) -> None:
    """The aggregate verdict must be clean, so a new violation class cannot hide.

    Args:
        cli_provider: Provider whose CLI contract is under test.
    """
    report = _require_surface(cli_provider)
    assert_that(list(report.violations)).described_as(report.describe()).is_empty()


def test_unadvertised_optional_flags_are_reported_not_fatal(
    cli_provider: AIProvider,
) -> None:
    """Optional-flag drift is observable but must never fail the gate.

    The guard drops an unadvertised optional flag before sending it, so its
    absence degrades a call rather than breaking one. Asserting the *shape* of the
    finding keeps the distinction from required flags honest without coupling the
    gate to whichever optional flags a given release happens to ship.

    Args:
        cli_provider: Provider whose CLI contract is under test.
    """
    report = _require_surface(cli_provider)
    declared = set(report.contract.optional_flag_names)
    # Asserted over the *declared* set, not over the findings: iterating the
    # findings makes both assertions vacuous on a fully conforming CLI, which is
    # the common case and exactly when the test still has to mean something.
    assert_that(set(report.unadvertised_optional_flags)).described_as(
        "only declared optional flags may be reported as unadvertised",
    ).is_subset_of(declared)
    violation_text = " ".join(report.violations)
    for flag in declared:
        assert_that(violation_text).described_as(
            f"optional flag {flag} must not be treated as a contract violation",
        ).does_not_contain(flag)
