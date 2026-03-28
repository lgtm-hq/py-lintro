"""Unit tests for OSV-Scanner suppression parser."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.parsers.osv_scanner.suppression_models import SuppressionEntry
from lintro.parsers.osv_scanner.suppression_parser import (
    classify_suppressions,
    parse_suppressions,
)
from lintro.parsers.osv_scanner.suppression_status import SuppressionStatus

# =============================================================================
# Tests for parse_suppressions
# =============================================================================


def test_parse_well_formed_toml(tmp_path: Path) -> None:
    """Parse a valid .osv-scanner.toml with multiple entries."""
    toml_file = tmp_path / ".osv-scanner.toml"
    toml_file.write_text(
        "[[IgnoredVulns]]\n"
        'id = "GHSA-1111-aaaa-bbbb"\n'
        "ignoreUntil = 2026-12-31\n"
        'reason = "Low risk transitive dep"\n'
        "\n"
        "[[IgnoredVulns]]\n"
        'id = "CVE-2024-99999"\n'
        "ignoreUntil = 2026-06-15\n"
        'reason = "No fix available yet"\n',
    )

    entries = parse_suppressions(toml_file)

    assert_that(entries).is_length(2)
    assert_that(entries[0].id).is_equal_to("GHSA-1111-aaaa-bbbb")
    assert_that(entries[0].ignore_until).is_equal_to(date(2026, 12, 31))
    assert_that(entries[0].reason).is_equal_to("Low risk transitive dep")
    assert_that(entries[1].id).is_equal_to("CVE-2024-99999")
    assert_that(entries[1].ignore_until).is_equal_to(date(2026, 6, 15))


def test_parse_missing_file(tmp_path: Path) -> None:
    """Return empty list for nonexistent file."""
    entries = parse_suppressions(tmp_path / "nonexistent.toml")
    assert_that(entries).is_equal_to([])


def test_parse_empty_file(tmp_path: Path) -> None:
    """Return empty list for empty TOML file."""
    toml_file = tmp_path / ".osv-scanner.toml"
    toml_file.write_text("")

    entries = parse_suppressions(toml_file)
    assert_that(entries).is_equal_to([])


def test_parse_no_ignored_vulns(tmp_path: Path) -> None:
    """Return empty list when TOML has no IgnoredVulns key."""
    toml_file = tmp_path / ".osv-scanner.toml"
    toml_file.write_text('[SomeOtherSection]\nkey = "value"\n')

    entries = parse_suppressions(toml_file)
    assert_that(entries).is_equal_to([])


def test_parse_skips_entry_without_id(tmp_path: Path) -> None:
    """Skip entries that have no id field."""
    toml_file = tmp_path / ".osv-scanner.toml"
    toml_file.write_text(
        "[[IgnoredVulns]]\n" "ignoreUntil = 2026-12-31\n" 'reason = "Missing id"\n',
    )

    entries = parse_suppressions(toml_file)
    assert_that(entries).is_equal_to([])


def test_parse_skips_entry_without_ignore_until(tmp_path: Path) -> None:
    """Skip entries that have no ignoreUntil field."""
    toml_file = tmp_path / ".osv-scanner.toml"
    toml_file.write_text(
        "[[IgnoredVulns]]\n" 'id = "GHSA-1111-aaaa-bbbb"\n' 'reason = "Missing date"\n',
    )

    entries = parse_suppressions(toml_file)
    assert_that(entries).is_equal_to([])


def test_parse_missing_reason_defaults_empty(tmp_path: Path) -> None:
    """Missing reason field defaults to empty string."""
    toml_file = tmp_path / ".osv-scanner.toml"
    toml_file.write_text(
        "[[IgnoredVulns]]\n"
        'id = "GHSA-1111-aaaa-bbbb"\n'
        "ignoreUntil = 2026-12-31\n",
    )

    entries = parse_suppressions(toml_file)
    assert_that(entries).is_length(1)
    assert_that(entries[0].reason).is_equal_to("")


def test_parse_invalid_toml(tmp_path: Path) -> None:
    """Return empty list for malformed TOML."""
    toml_file = tmp_path / ".osv-scanner.toml"
    toml_file.write_text("this is not valid toml [[[")

    entries = parse_suppressions(toml_file)
    assert_that(entries).is_equal_to([])


# =============================================================================
# Tests for classify_suppressions
# =============================================================================


def test_classify_expired() -> None:
    """Entry past ignoreUntil is EXPIRED."""
    entry = SuppressionEntry(
        id="GHSA-expired",
        ignore_until=date(2025, 1, 1),
        reason="Old suppression",
    )

    classified = classify_suppressions(
        [entry],
        probe_vuln_ids={"GHSA-expired"},
        today=date(2026, 3, 26),
    )

    assert_that(classified).is_length(1)
    assert_that(classified[0].status).is_equal_to(SuppressionStatus.EXPIRED)


def test_classify_active() -> None:
    """Entry still within date and found in probe is ACTIVE."""
    entry = SuppressionEntry(
        id="GHSA-active",
        ignore_until=date(2027, 12, 31),
        reason="Still present",
    )

    classified = classify_suppressions(
        [entry],
        probe_vuln_ids={"GHSA-active", "GHSA-other"},
        today=date(2026, 3, 26),
    )

    assert_that(classified).is_length(1)
    assert_that(classified[0].status).is_equal_to(SuppressionStatus.ACTIVE)


def test_classify_stale() -> None:
    """Entry within date but NOT found in probe is STALE."""
    entry = SuppressionEntry(
        id="GHSA-stale",
        ignore_until=date(2027, 12, 31),
        reason="Fixed upstream",
    )

    classified = classify_suppressions(
        [entry],
        probe_vuln_ids={"GHSA-other"},
        today=date(2026, 3, 26),
    )

    assert_that(classified).is_length(1)
    assert_that(classified[0].status).is_equal_to(SuppressionStatus.STALE)


def test_classify_boundary_today_equals_ignore_until() -> None:
    """Entry with ignoreUntil == today is NOT expired (still active/stale)."""
    entry = SuppressionEntry(
        id="GHSA-boundary",
        ignore_until=date(2026, 3, 26),
        reason="Boundary case",
    )

    # In probe (active, not expired)
    classified = classify_suppressions(
        [entry],
        probe_vuln_ids={"GHSA-boundary"},
        today=date(2026, 3, 26),
    )

    assert_that(classified[0].status).is_equal_to(SuppressionStatus.ACTIVE)


def test_classify_empty_entries() -> None:
    """Empty entries list returns empty classified list."""
    classified = classify_suppressions(
        [],
        probe_vuln_ids={"GHSA-something"},
        today=date(2026, 3, 26),
    )

    assert_that(classified).is_equal_to([])


def test_classify_multiple_entries() -> None:
    """Classify a mix of active, stale, and expired entries."""
    entries = [
        SuppressionEntry(
            id="GHSA-active",
            ignore_until=date(2027, 1, 1),
            reason="Still present",
        ),
        SuppressionEntry(
            id="GHSA-stale",
            ignore_until=date(2027, 1, 1),
            reason="Fixed upstream",
        ),
        SuppressionEntry(
            id="GHSA-expired",
            ignore_until=date(2025, 1, 1),
            reason="Past date",
        ),
    ]

    classified = classify_suppressions(
        entries,
        probe_vuln_ids={"GHSA-active"},
        today=date(2026, 3, 26),
    )

    assert_that(classified).is_length(3)
    assert_that(classified[0].status).is_equal_to(SuppressionStatus.ACTIVE)
    assert_that(classified[1].status).is_equal_to(SuppressionStatus.STALE)
    assert_that(classified[2].status).is_equal_to(SuppressionStatus.EXPIRED)


@pytest.mark.parametrize(
    ("ignore_until", "today", "in_probe", "expected"),
    [
        pytest.param(
            date(2025, 1, 1),
            date(2026, 1, 1),
            True,
            SuppressionStatus.EXPIRED,
            id="expired_even_if_in_probe",
        ),
        pytest.param(
            date(2025, 1, 1),
            date(2026, 1, 1),
            False,
            SuppressionStatus.EXPIRED,
            id="expired_and_not_in_probe",
        ),
        pytest.param(
            date(2027, 1, 1),
            date(2026, 1, 1),
            True,
            SuppressionStatus.ACTIVE,
            id="future_and_in_probe",
        ),
        pytest.param(
            date(2027, 1, 1),
            date(2026, 1, 1),
            False,
            SuppressionStatus.STALE,
            id="future_and_not_in_probe",
        ),
    ],
)
def test_classify_parametrized(
    ignore_until: date,
    today: date,
    in_probe: bool,
    expected: SuppressionStatus,
) -> None:
    """Parametrized classification covering all state combinations."""
    entry = SuppressionEntry(id="GHSA-test", ignore_until=ignore_until, reason="test")
    probe_ids = {"GHSA-test"} if in_probe else set()

    classified = classify_suppressions([entry], probe_ids, today=today)

    assert_that(classified[0].status).is_equal_to(expected)
