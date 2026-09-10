"""Tests that the typos parser package does not export a findings-only parser."""

from __future__ import annotations

from assertpy import assert_that

import lintro.parsers.typos as typos_pkg
from lintro.parsers.typos.typos_parser import parse_typos_report


def test_package_exports_only_the_combined_parser() -> None:
    """``parse_typos_report`` is the only public parse function.

    Other tools expose ``parse_<tool>_output`` as the plugin entry. That name
    would return an empty findings list for a diagnostic-only stream and look
    like a clean scan, so it is not part of this package's public API.
    """
    assert_that(list(typos_pkg.__all__)).is_equal_to(
        [
            "TyposIssue",
            "TyposReport",
            "parse_typos_report",
        ],
    )
    assert_that(typos_pkg.parse_typos_report).is_same_as(parse_typos_report)
    assert_that(hasattr(typos_pkg, "parse_typos_output")).is_false()
    assert_that(hasattr(typos_pkg, "parse_typos_errors")).is_false()
