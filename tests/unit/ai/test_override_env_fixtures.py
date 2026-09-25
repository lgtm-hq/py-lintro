"""The two fixtures that clear ``LINTRO_AI_*`` overrides clear the same set.

``tests/conftest.py`` clears the flat overrides for the whole tree, and
``tests/unit/ai/conftest.py`` clears them (plus the provider-block and bare
mode variables) for the AI suite. A new overlay added to one list and not the
other leaks a developer's export into the tests the other one guards (#2796:
``LINTRO_AI_REVIEW_PR_BUDGET_USD``).
"""

from __future__ import annotations

from assertpy import assert_that

from lintro.ai import config_overrides
from tests.conftest import _AI_OVERRIDE_ENV_VARS
from tests.unit.ai.conftest import FLAT_AI_OVERRIDE_ENV


def test_the_tree_wide_and_ai_suite_lists_agree() -> None:
    """Both fixtures clear exactly the same flat overrides."""
    assert_that(set(_AI_OVERRIDE_ENV_VARS)).is_equal_to(set(FLAT_AI_OVERRIDE_ENV))


def test_both_lists_cover_every_flat_override() -> None:
    """Every flat variable the overlay reads is cleared by both fixtures."""
    read = set(config_overrides._ENV_BY_FIELD.values())

    assert_that(set(_AI_OVERRIDE_ENV_VARS)).is_equal_to(read)
    assert_that(read).contains(config_overrides.ENV_REVIEW_PR_BUDGET_USD)
