"""Trivy output parser for dependency vulnerabilities."""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from lintro.parsers.base_parser import validate_str_field
from lintro.parsers.trivy.trivy_issue import TrivyIssue


def parse_trivy_output(output: str | None) -> list[TrivyIssue]:
    """Parse Trivy ``fs --format json`` output into ``TrivyIssue`` objects.

    Only the vulnerability stream is surfaced (the plugin runs Trivy with
    ``--scanners vuln``). The parser is defensive against malformed JSON and
    unexpected structures, returning an empty list rather than raising. A clean
    scan omits the ``Results`` key entirely, which yields an empty list.

    Args:
        output: Raw JSON string from ``trivy fs --format json``. May be
            ``None`` or empty.

    Returns:
        list[TrivyIssue]: Parsed vulnerabilities. Empty when there are no
            findings or the input cannot be parsed.
    """
    if not output or not output.strip():
        return []

    try:
        data = json.loads(output)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("Failed to parse trivy JSON output: {}", exc)
        return []

    if not isinstance(data, dict):
        return []

    results = data.get("Results")
    if not isinstance(results, list):
        return []

    issues: list[TrivyIssue] = []
    for result in results:
        if not isinstance(result, dict):
            continue
        target = result.get("Target")
        target_str = target if isinstance(target, str) else ""

        vulnerabilities = result.get("Vulnerabilities")
        if not isinstance(vulnerabilities, list):
            continue

        for vuln in vulnerabilities:
            if not isinstance(vuln, dict):
                continue
            issue = _parse_vulnerability(vuln, target_str)
            if issue is not None:
                issues.append(issue)

    return issues


def _parse_vulnerability(vuln: dict[str, Any], target: str) -> TrivyIssue | None:
    """Convert a single Trivy vulnerability record into a ``TrivyIssue``.

    Args:
        vuln: One entry from ``Results[].Vulnerabilities``.
        target: The scanned lockfile / manifest for this result block.

    Returns:
        TrivyIssue | None: The parsed issue, or ``None`` when the required
            identifier is missing or malformed.
    """
    try:
        vuln_id = validate_str_field(vuln.get("VulnerabilityID"), "VulnerabilityID")
        if not vuln_id:
            logger.warning("Skipping trivy vulnerability missing VulnerabilityID")
            return None

        pkg_name = validate_str_field(vuln.get("PkgName"), "PkgName")
        installed_version = validate_str_field(
            vuln.get("InstalledVersion"),
            "InstalledVersion",
        )

        fixed_raw = vuln.get("FixedVersion")
        fixed_version = fixed_raw if isinstance(fixed_raw, str) and fixed_raw else None

        severity_raw = vuln.get("Severity")
        severity = severity_raw if isinstance(severity_raw, str) else None

        title = validate_str_field(vuln.get("Title"), "Title")

        primary_url_raw = vuln.get("PrimaryURL")
        doc_url = primary_url_raw if isinstance(primary_url_raw, str) else ""

        file_path = target or pkg_name

        return TrivyIssue(
            file=file_path,
            line=0,
            doc_url=doc_url,
            vuln_id=vuln_id,
            pkg_name=pkg_name,
            installed_version=installed_version,
            fixed_version=fixed_version,
            severity=severity,
            title=title,
            target=target,
        )
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning("Failed to parse trivy vulnerability: {}", exc)
        return None
