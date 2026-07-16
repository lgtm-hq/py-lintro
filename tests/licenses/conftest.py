"""Shared fixtures for license compliance tests."""

from __future__ import annotations

import pytest

from lintro.licenses.models import PackageLicense


@pytest.fixture
def mit_package() -> PackageLicense:
    """Return a permissively-licensed package fixture.

    Returns:
        PackageLicense: A package declaring the MIT license.
    """
    return PackageLicense(
        name="requests",
        version="2.31.0",
        license_id="MIT",
        license_name="MIT",
        ecosystem="python",
    )


@pytest.fixture
def gpl_package() -> PackageLicense:
    """Return a strong-copyleft package fixture.

    Returns:
        PackageLicense: A package declaring GPL-3.0-only.
    """
    return PackageLicense(
        name="some-lib",
        version="1.0.0",
        license_id="GPL-3.0-only",
        license_name="GPL-3.0",
        ecosystem="python",
    )


@pytest.fixture
def unknown_package() -> PackageLicense:
    """Return a package with an undeterminable license.

    Returns:
        PackageLicense: A package with no SPDX identifier.
    """
    return PackageLicense(
        name="mystery-pkg",
        version="0.5.0",
        license_id=None,
        license_name="Custom Proprietary",
        ecosystem="python",
    )


@pytest.fixture
def lgpl_package() -> PackageLicense:
    """Return a weak-copyleft package fixture.

    Returns:
        PackageLicense: A package declaring LGPL-3.0-only.
    """
    return PackageLicense(
        name="weak-lib",
        version="2.0.0",
        license_id="LGPL-3.0-only",
        license_name="LGPL-3.0",
        ecosystem="python",
    )
