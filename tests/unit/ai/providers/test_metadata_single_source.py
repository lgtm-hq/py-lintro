"""Guard: plugin metadata is the only declaration of provider facts (#2308).

The migration in #2308 folded four parallel tables — ``registry.PROVIDERS``,
``availability._CLI_BINARIES``, ``cli_contracts.CLI_CONTRACTS`` and doctor's
``if provider == ...`` ladders — into each plugin's
:class:`~lintro.ai.providers.protocol.ProviderMetadata`. The equality test that
proved the move was correct is gone with the tables it compared against; this
module is what stops them growing back.

The source scan is the point of the file: a reviewer can convince themselves a
consumer reads the facade, but only a scan can prove nobody re-declared the
table next door.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.ai.enums import AITransport
from lintro.ai.provider_enum import AIProvider
from lintro.ai.providers.cli_auth_probe import CliAuthProbe
from lintro.ai.providers.cli_contracts import cli_contract_for
from lintro.ai.registry import all_metadata, metadata_for, model_pricing

#: Root of the package the scan walks.
_PACKAGE_ROOT = Path(__file__).resolve().parents[4] / "lintro"

#: Assignments that would re-create a provider table outside plugin metadata.
_BANNED_ASSIGNMENTS = (
    re.compile(r"^\s*PROVIDERS\b\s*[:=]", re.MULTILINE),
    re.compile(r"^\s*CLI_CONTRACTS\b\s*[:=]", re.MULTILINE),
    re.compile(r"^\s*_CLI_BINARIES\b\s*[:=]", re.MULTILINE),
)


def _auth_probe(provider: AIProvider) -> CliAuthProbe:
    """Return the declared CLI auth probe for *provider*.

    Args:
        provider: The provider to look up.

    Returns:
        The provider's auth probe.

    Raises:
        AssertionError: If the provider declares none. Every provider lintro
            ships serves a CLI transport, so a missing probe is a regression.
    """
    probe = metadata_for(provider).cli_auth_probe
    if probe is None:
        raise AssertionError(f"{provider.value} declares no CLI auth probe")
    return probe


def test_no_parallel_provider_table_exists_in_the_package() -> None:
    """No module re-declares a provider table the metadata already owns."""
    offenders: list[str] = []
    for path in sorted(_PACKAGE_ROOT.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for pattern in _BANNED_ASSIGNMENTS:
            if pattern.search(text):
                offenders.append(
                    f"{path.relative_to(_PACKAGE_ROOT)}: {pattern.pattern}",
                )
    assert_that(offenders).is_empty()


@pytest.mark.parametrize("provider", list(AIProvider))
def test_every_provider_declares_a_complete_metadata_record(
    provider: AIProvider,
) -> None:
    """Each plugin states every fact its consumers read, with nothing empty.

    Args:
        provider: The provider under test.
    """
    metadata = metadata_for(provider)
    assert_that(metadata.display_name).is_not_empty()
    assert_that(metadata.default_model).is_not_empty()
    assert_that(metadata.default_api_key_env).is_not_empty()
    assert_that(metadata.supported_transports).is_not_empty()
    assert_that(metadata.supported_transports).contains(metadata.default_transport)
    assert_that(dict(metadata.pricing)).contains_key(metadata.default_model)


@pytest.mark.parametrize("provider", list(AIProvider))
def test_cli_facts_agree_within_one_record(provider: AIProvider) -> None:
    """A provider's CLI binary, contract and probe describe the same binary.

    These three used to live in three modules, where nothing stopped them
    drifting apart. Declared together, they can still be mistyped — so assert
    they agree. Every provider lintro ships serves the CLI transport, and
    ``tests/unit/ai/providers/test_cli_capability_guard.py`` asserts a contract
    exists for every enum member, so this asserts the same rule rather than
    branching on a case that cannot occur. ``ProviderMetadata`` still allows the
    CLI fields to be ``None`` for a future API-only vendor; that vendor would
    relax both tests together.

    Args:
        provider: The provider under test.
    """
    metadata = metadata_for(provider)
    assert_that(metadata.supports(AITransport.CLI)).is_true()
    assert_that(metadata.cli_binary).is_not_none()
    assert_that(metadata.cli_contract_id).is_equal_to(provider.value)
    assert_that(cli_contract_for(provider).binary).is_equal_to(metadata.cli_binary)
    assert_that(metadata.cli_install_hint).is_not_empty()
    assert_that(_auth_probe(provider).unverified_message).is_not_empty()


@pytest.mark.parametrize("provider", list(AIProvider))
def test_api_transport_implies_an_sdk_package(provider: AIProvider) -> None:
    """A provider serves the API transport only if it names an SDK to install.

    Args:
        provider: The provider under test.
    """
    metadata = metadata_for(provider)
    if metadata.supports(AITransport.API):
        assert_that(metadata.sdk_package).is_not_none()
    else:
        assert_that(metadata.sdk_package).is_none()


def test_model_identifiers_are_unique_across_providers() -> None:
    """No two providers claim the same model, so the merged pricing is lossless."""
    declared = [model for record in all_metadata().values() for model in record.pricing]
    assert_that(len(declared)).is_equal_to(len(set(declared)))
    assert_that(sorted(model_pricing())).is_equal_to(sorted(declared))
