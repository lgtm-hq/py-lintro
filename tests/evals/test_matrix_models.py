"""Guards that every committed matrix cell names a model its provider accepts.

A cell whose model belongs to another provider would either be rejected at run
time or silently priced through the unknown-model fallback, so the whole point
of a cost projection would be lost.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from assertpy import assert_that
from review_matrix.models.matrix import MatrixConfig
from review_matrix.spec_loader import load_matrix

from lintro.ai.enums import AITransport
from lintro.ai.provider_enum import AIProvider
from lintro.ai.registry import PROVIDERS

HARNESS_ROOT = Path(__file__).resolve().parents[2] / "evals" / "review-efficacy"
MATRIX = load_matrix(HARNESS_ROOT / "matrix.yaml")

#: Model ids the Cursor CLI exposes but the registry does not price. Cursor's
#: registered models are all priced at zero (the subscription is billed
#: elsewhere), so pricing is not what this allowlist gives up; it only lets the
#: matrix name a model the CLI actually accepts. See docs/ai-features.md.
CURSOR_CLI_MODEL_ALLOWLIST = frozenset({"cursor-grok-4.6-high"})

#: Cursor rejects anything but CLI transport
#: (:class:`lintro.ai.providers.cursor.CursorProvider`); the SDK providers
#: support both.
SUPPORTED_TRANSPORTS: dict[AIProvider, frozenset[AITransport]] = {
    AIProvider.ANTHROPIC: frozenset({AITransport.API, AITransport.CLI}),
    AIProvider.OPENAI: frozenset({AITransport.API, AITransport.CLI}),
    AIProvider.CURSOR: frozenset({AITransport.CLI}),
}


@pytest.mark.parametrize(
    "config",
    MATRIX.configs,
    ids=[config.config_id for config in MATRIX.configs],
)
def test_matrix_cell_names_a_registered_provider(config: MatrixConfig) -> None:
    """Every cell's provider is one the registry knows.

    Args:
        config: Matrix cell under test.
    """
    assert_that([provider.value for provider in AIProvider]).contains(config.provider)


@pytest.mark.parametrize(
    "config",
    MATRIX.configs,
    ids=[config.config_id for config in MATRIX.configs],
)
def test_matrix_cell_model_belongs_to_its_provider(config: MatrixConfig) -> None:
    """A cell's model is registered under that cell's own provider.

    Args:
        config: Matrix cell under test.
    """
    provider = AIProvider(config.provider)
    known = set(PROVIDERS.get(provider).models)
    if provider is AIProvider.CURSOR:
        known |= set(CURSOR_CLI_MODEL_ALLOWLIST)

    assert_that(sorted(known)).contains(config.model)


@pytest.mark.parametrize(
    "config",
    MATRIX.configs,
    ids=[config.config_id for config in MATRIX.configs],
)
def test_matrix_cell_transport_is_supported(config: MatrixConfig) -> None:
    """A cell's transport is one its provider actually supports.

    Args:
        config: Matrix cell under test.
    """
    provider = AIProvider(config.provider)
    transport = AITransport(config.transport)

    assert_that(SUPPORTED_TRANSPORTS[provider]).contains(transport)
