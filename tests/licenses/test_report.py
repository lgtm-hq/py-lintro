"""Tests for license result serialization helpers."""

from __future__ import annotations

import json

from assertpy import assert_that

from lintro.licenses.models import LicenseResult, LicenseStatus, PackageLicense
from lintro.licenses.report import to_csv, to_json, to_spdx


def _results() -> list[LicenseResult]:
    """Build a small set of evaluated results.

    Returns:
        list[LicenseResult]: Sample results.
    """
    return [
        LicenseResult(
            package=PackageLicense(
                name="requests",
                version="2.31.0",
                license_id="Apache-2.0",
            ),
            status=LicenseStatus.ALLOWED,
            reason="ok",
        ),
        LicenseResult(
            package=PackageLicense(
                name="some-lib",
                version="1.0.0",
                license_id="GPL-3.0-only",
            ),
            status=LicenseStatus.DENIED,
            reason="denied",
        ),
    ]


def test_to_json_is_valid_and_complete() -> None:
    """JSON output parses and preserves status and license fields."""
    payload = json.loads(to_json(_results()))
    assert_that(payload).is_length(2)
    assert_that(payload[0]["name"]).is_equal_to("requests")
    assert_that(payload[1]["status"]).is_equal_to("denied")


def test_to_csv_has_header_and_rows() -> None:
    """CSV output includes a header and one row per result."""
    lines = to_csv(_results()).strip().splitlines()
    assert_that(lines[0]).contains("name", "license_id", "status")
    assert_that(lines).is_length(3)


def test_to_spdx_emits_document_header_and_packages() -> None:
    """SPDX output includes the document header and package entries."""
    content = to_spdx(_results())
    assert_that(content).contains("SPDXVersion: SPDX-2.3")
    assert_that(content).contains("PackageName: requests")
    assert_that(content).contains("PackageLicenseConcluded: GPL-3.0-only")
