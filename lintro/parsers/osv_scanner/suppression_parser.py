"""Parser for .osv-scanner.toml vulnerability suppression entries.

Reads [[IgnoredVulns]] entries from the OSV-Scanner configuration file
and classifies them as ACTIVE, STALE, or EXPIRED based on probe scan
results and expiry dates.
"""

from __future__ import annotations

import tomllib
from datetime import date, datetime
from pathlib import Path
from typing import Any

from loguru import logger

from lintro.parsers.osv_scanner.suppression_models import (
    ClassifiedSuppression,
    SuppressionEntry,
)
from lintro.parsers.osv_scanner.suppression_status import SuppressionStatus


def parse_suppressions(toml_path: Path) -> list[SuppressionEntry]:
    """Parse [[IgnoredVulns]] entries from an .osv-scanner.toml file.

    Args:
        toml_path: Path to the .osv-scanner.toml file.

    Returns:
        List of suppression entries. Returns empty list if the file
        doesn't exist, can't be parsed, or has no IgnoredVulns.
    """
    if not toml_path.is_file():
        return []

    try:
        with toml_path.open("rb") as f:
            data: dict[str, Any] = tomllib.load(f)
    except (tomllib.TOMLDecodeError, OSError) as e:
        logger.warning("Failed to parse {}: {}", toml_path, e)
        return []

    ignored_vulns = data.get("IgnoredVulns", [])
    if not isinstance(ignored_vulns, list):
        return []

    entries: list[SuppressionEntry] = []
    for item in ignored_vulns:
        if not isinstance(item, dict):
            continue

        vuln_id = item.get("id")
        if not isinstance(vuln_id, str) or not vuln_id:
            logger.debug("Skipping IgnoredVulns entry with missing id")
            continue

        ignore_until = item.get("ignoreUntil")
        if not isinstance(ignore_until, date) or isinstance(ignore_until, datetime):
            logger.debug(
                "Skipping IgnoredVulns entry '{}': missing or invalid ignoreUntil",
                vuln_id,
            )
            continue

        reason = item.get("reason", "")
        if not isinstance(reason, str):
            reason = ""

        entries.append(
            SuppressionEntry(
                id=vuln_id,
                ignore_until=ignore_until,
                reason=reason,
            ),
        )

    return entries


def classify_suppressions(
    entries: list[SuppressionEntry],
    probe_vuln_ids: set[str],
    today: date | None = None,
) -> list[ClassifiedSuppression]:
    """Classify suppression entries as ACTIVE, STALE, or EXPIRED.

    Compares each suppression against the probe scan results (a scan
    run without suppressions) to determine if the suppressed
    vulnerability is still present in the dependency tree.

    Args:
        entries: Suppression entries parsed from .osv-scanner.toml.
        probe_vuln_ids: Set of vulnerability IDs found by the probe
            scan (run with --config /dev/null to disable suppressions).
        today: Override for the current date (for testing).
            Defaults to date.today().

    Returns:
        List of classified suppressions with their status.
    """
    if today is None:
        today = date.today()

    classified: list[ClassifiedSuppression] = []
    for entry in entries:
        if today > entry.ignore_until:
            status = SuppressionStatus.EXPIRED
        elif entry.id in probe_vuln_ids:
            status = SuppressionStatus.ACTIVE
        else:
            status = SuppressionStatus.STALE

        classified.append(ClassifiedSuppression(entry=entry, status=status))

    return classified
