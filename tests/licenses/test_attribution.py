"""Tests for the attribution document generator."""

from __future__ import annotations

from assertpy import assert_that

from lintro.licenses.attribution import AttributionGenerator
from lintro.licenses.models import PackageLicense


def _packages() -> list[PackageLicense]:
    """Build a small set of packages spanning several licenses.

    Returns:
        list[PackageLicense]: Sample packages.
    """
    return [
        PackageLicense(name="requests", version="2.31.0", license_id="Apache-2.0"),
        PackageLicense(name="pydantic", version="2.5.0", license_id="MIT"),
        PackageLicense(name="click", version="8.1.7", license_id="BSD-3-Clause"),
        PackageLicense(name="mystery", version="0.1.0", license_id=None),
    ]


def test_group_by_license_buckets_unknown() -> None:
    """Packages without an SPDX id are grouped under Unknown."""
    groups = AttributionGenerator().group_by_license(_packages())
    assert_that(groups).contains_key("MIT", "Apache-2.0", "Unknown")
    assert_that([p.name for p in groups["Unknown"]]).contains("mystery")


def test_generate_markdown_includes_headers_and_packages() -> None:
    """The Markdown document lists license headers and package entries."""
    content = AttributionGenerator().generate_markdown(_packages())
    assert_that(content).starts_with("# Third-Party Licenses")
    assert_that(content).contains("## MIT")
    assert_that(content).contains("## Apache-2.0")
    assert_that(content).contains("**pydantic** (2.5.0)")


def test_generate_markdown_empty_packages() -> None:
    """The generator handles an empty package list gracefully."""
    content = AttributionGenerator().generate_markdown([])
    assert_that(content).contains("# Third-Party Licenses")
