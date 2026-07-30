"""Collection-time gating and shared fixtures for the AI CLI contract tiers.

Preconditions and their failure semantics live in :mod:`tests.contract.gating`;
this module only wires them into pytest.
"""

from __future__ import annotations

import pytest

from lintro.ai.provider_enum import AIProvider
from lintro.ai.providers.cli_contract_check import declared_cli_providers
from tests.contract.gating import ENABLE_TIER2_ENV, tier2_enabled


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    """Skip tier-2 tests unless real invocations were explicitly enabled.

    Args:
        config: The active pytest configuration.
        items: Collected test items, mutated in place.
    """
    del config
    if tier2_enabled():
        return
    marker = pytest.mark.skip(
        reason=(
            f"tier-2 real-invocation smoke is opt-in: set {ENABLE_TIER2_ENV}=1 "
            "(spends provider quota)"
        ),
    )
    for item in items:
        if "contract_tier2" in item.keywords:
            item.add_marker(marker)


@pytest.fixture(params=declared_cli_providers(), ids=lambda provider: provider.value)
def cli_provider(request: pytest.FixtureRequest) -> AIProvider:
    """Return each provider that declares a CLI contract.

    Parametrised from the contract registry rather than a hand-written list, so a
    newly declared provider is covered without touching these tests.

    Args:
        request: The pytest fixture request carrying the parametrised provider.

    Returns:
        The provider under test.
    """
    provider: AIProvider = request.param
    return provider
