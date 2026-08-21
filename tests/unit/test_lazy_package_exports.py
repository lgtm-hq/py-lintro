"""Guards that lazy package ``__all__`` stays aligned with ``_LAZY_EXPORTS``."""

from __future__ import annotations

import importlib

import pytest
from assertpy import assert_that

_LAZY_PACKAGES: tuple[str, ...] = (
    "lintro.ai",
    "lintro.ai.review",
    "lintro.config",
    "lintro.plugins",
    "lintro.tools",
    "lintro.cli_utils.commands",
)


@pytest.mark.parametrize("package_name", _LAZY_PACKAGES)
def test_all_matches_lazy_exports(package_name: str) -> None:
    """Public ``__all__`` is exactly the runtime lazy-import map.

    Args:
        package_name: Import path of a PEP 562 lazy package.
    """
    package = importlib.import_module(package_name)
    assert_that(set(package.__all__)).is_equal_to(set(package._LAZY_EXPORTS))


@pytest.mark.parametrize("package_name", _LAZY_PACKAGES)
def test_dir_includes_lazy_export_names(package_name: str) -> None:
    """``dir()`` includes every lazy export without requiring first access.

    Args:
        package_name: Import path of a PEP 562 lazy package.
    """
    package = importlib.import_module(package_name)
    assert_that(set(package.__all__).issubset(set(dir(package)))).is_true()
