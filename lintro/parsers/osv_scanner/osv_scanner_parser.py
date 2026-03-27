"""Parser for OSV-Scanner JSON output."""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from lintro.parsers.base_parser import extract_str_field, validate_str_field
from lintro.parsers.osv_scanner.osv_scanner_issue import OsvScannerIssue

# Severity ranking for selecting the highest severity from a vulnerability's
# database entries.  These raw strings are later normalized to SeverityLevel
# (ERROR/WARNING/INFO) by BaseIssue.get_severity() via _SEVERITY_ALIASES.
# We need the finer-grained ranking here because the canonical enum collapses
# CRITICAL and HIGH into the same ERROR level.
_SEVERITY_RANK: dict[str, int] = {
    "CRITICAL": 4,
    "HIGH": 3,
    "MEDIUM": 2,
    "LOW": 1,
}


def _highest_severity(group: dict[str, Any]) -> str:
    """Extract the severity from a vulnerability group.

    OSV-Scanner v2 groups vulnerabilities and may include CVSS severity
    in the group's max_severity field.

    Args:
        group: A single group dictionary from OSV-Scanner output.

    Returns:
        Severity string from the group, defaults to "MEDIUM".
    """
    max_sev = group.get("max_severity")
    if isinstance(max_sev, str):
        sev_upper = max_sev.upper()
        if sev_upper in _SEVERITY_RANK:
            return sev_upper
    return "MEDIUM"


def _extract_fixed_version(
    vuln_detail: dict[str, Any],
    package_name: str,
    package_ecosystem: str,
) -> str:
    """Extract the fixed version from a vulnerability's affected data.

    Args:
        vuln_detail: The full vulnerability object from OSV database.
        package_name: Package name to match.
        package_ecosystem: Ecosystem to match.

    Returns:
        Fixed version string, or empty string if not found.
    """
    affected = vuln_detail.get("affected", [])
    if not isinstance(affected, list):
        return ""

    for entry in affected:
        if not isinstance(entry, dict):
            continue
        pkg = entry.get("package", {})
        if not isinstance(pkg, dict):
            continue
        if pkg.get("name") != package_name:
            continue
        if pkg.get("ecosystem", "").upper() != package_ecosystem.upper():
            continue
        ranges = entry.get("ranges", [])
        if not isinstance(ranges, list):
            continue
        for r in ranges:
            if not isinstance(r, dict):
                continue
            events = r.get("events", [])
            if not isinstance(events, list):
                continue
            for event in events:
                if isinstance(event, dict) and "fixed" in event:
                    return str(event["fixed"])
    return ""


def _parse_single_result(result: dict[str, Any]) -> list[OsvScannerIssue]:
    """Parse a single OSV-Scanner result into issues.

    Each result corresponds to a package source (lockfile) and may contain
    multiple vulnerability groups, each with multiple vulnerability IDs.

    Args:
        result: Dictionary containing a single OSV-Scanner result.

    Returns:
        List of OsvScannerIssue objects parsed from this result.
    """
    source = result.get("source", {})
    if not isinstance(source, dict):
        return []
    source_path = extract_str_field(
        data=source,
        candidates=["path"],
        default="lockfile",
    )

    packages = result.get("packages", [])
    if not isinstance(packages, list):
        return []

    issues: list[OsvScannerIssue] = []

    for pkg_entry in packages:
        if not isinstance(pkg_entry, dict):
            continue

        package = pkg_entry.get("package", {})
        if not isinstance(package, dict):
            continue

        pkg_name = validate_str_field(
            package.get("name"),
            "package_name",
            log_warning=True,
        )
        if not pkg_name:
            continue

        pkg_version = extract_str_field(
            data=package,
            candidates=["version"],
            default="",
        )
        pkg_ecosystem = extract_str_field(
            data=package,
            candidates=["ecosystem"],
            default="",
        )

        groups = pkg_entry.get("groups", [])
        if not isinstance(groups, list):
            groups = []

        vulnerabilities = pkg_entry.get("vulnerabilities", [])
        if not isinstance(vulnerabilities, list):
            vulnerabilities = []

        # Build a lookup for vulnerability details
        vuln_details: dict[str, dict[str, Any]] = {}
        for v in vulnerabilities:
            if isinstance(v, dict) and "id" in v:
                vuln_details[v["id"]] = v

        # Each group represents a set of related vulnerability IDs
        for group in groups:
            if not isinstance(group, dict):
                continue

            vuln_ids = group.get("ids", [])
            if not isinstance(vuln_ids, list) or not vuln_ids:
                continue

            # Use the first ID as the primary
            primary_id = str(vuln_ids[0])
            severity = _highest_severity(group)

            # Try all IDs in the group to find vulnerability details —
            # the primary ID may not be in the vulnerabilities array
            # (e.g. a CVE alias when only the GHSA entry has details).
            detail: dict[str, Any] = {}
            for vid in vuln_ids:
                detail = vuln_details.get(str(vid), {})
                if detail:
                    break
            fixed = _extract_fixed_version(detail, pkg_name, pkg_ecosystem)

            issues.append(
                OsvScannerIssue(
                    file=source_path,
                    line=0,
                    column=0,
                    message="",  # __post_init__ builds the message
                    vuln_id=primary_id,
                    severity=severity,
                    package_name=pkg_name,
                    package_version=pkg_version,
                    package_ecosystem=pkg_ecosystem,
                    fixed_version=fixed,
                ),
            )

    return issues


def parse_osv_scanner_output(output: str | None) -> list[OsvScannerIssue]:
    """Parse OSV-Scanner JSON output into OsvScannerIssue objects.

    Args:
        output: JSON string from OSV-Scanner output, or None.

    Returns:
        List of parsed vulnerability issues. Returns empty list for
        None, empty string, invalid JSON, or unexpected data structure.
    """
    if output is None or not output.strip():
        return []

    try:
        # Use raw_decode to ignore trailing stderr text that
        # _run_subprocess appends after the JSON stdout.
        decoder = json.JSONDecoder()
        data, _ = decoder.raw_decode(output.lstrip())
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning("Failed to parse OSV-Scanner JSON output: {}", e)
        return []

    if not isinstance(data, dict):
        logger.warning(
            "OSV-Scanner output must be a JSON object, got {}",
            type(data).__name__,
        )
        return []

    results = data.get("results", [])
    if not isinstance(results, list):
        logger.warning(
            "OSV-Scanner results must be a list, got {}",
            type(results).__name__,
        )
        return []

    issues: list[OsvScannerIssue] = []

    for result in results:
        if not isinstance(result, dict):
            logger.debug("Skipping non-dict item in OSV-Scanner results")
            continue

        try:
            result_issues = _parse_single_result(result=result)
            issues.extend(result_issues)
        except (KeyError, TypeError, ValueError) as e:
            logger.warning("Failed to parse OSV-Scanner result: {}", e)
            continue

    return issues
