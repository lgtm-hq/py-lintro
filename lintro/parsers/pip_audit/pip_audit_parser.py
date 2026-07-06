"""Parser for pip-audit JSON output.

pip-audit (``--format json``) emits a single top-level JSON object::

    {
      "dependencies": [
        {"name": "jinja2", "version": "2.4.1", "vulns": [
            {"id": "PYSEC-2019-217", "fix_versions": ["2.10.1"],
             "aliases": ["CVE-2019-10906"], "description": "..."}
        ]},
        {"name": "somepkg", "skip_reason": "..."}
      ],
      "fixes": []
    }

The JSON payload has no severity field, so severity is reported as
``UNKNOWN``.
"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from lintro.parsers.base_parser import validate_str_field
from lintro.parsers.pip_audit.pip_audit_issue import PipAuditIssue


def extract_pip_audit_payload(output: str | None) -> dict[str, Any] | None:
    """Extract the parsed JSON object from pip-audit output.

    Args:
        output: Raw stdout from ``pip-audit --format json``.

    Returns:
        The parsed JSON object, or ``None`` when the output is empty or cannot
        be parsed as a JSON object. A ``None`` result on non-empty output
        indicates a parse failure that security callers must treat as a
        non-clean scan (see #1044).
    """
    if not output or not output.strip():
        return None

    try:
        data = json.loads(output.strip())
    except json.JSONDecodeError as exc:
        logger.warning(f"Failed to parse pip-audit JSON output: {exc}")
        return None

    if not isinstance(data, dict):
        logger.warning("pip-audit output is not a JSON object")
        return None

    return data


def _coerce_str_list(value: object) -> list[str]:
    """Coerce a JSON value into a list of strings.

    Args:
        value: Raw value from the pip-audit payload (expected list).

    Returns:
        List of string entries; non-list or non-string members are dropped.
    """
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def parse_pip_audit_output(
    output: str | None,
    source: str = "",
    data: dict[str, Any] | None = None,
) -> list[PipAuditIssue]:
    """Parse pip-audit JSON output into ``PipAuditIssue`` objects.

    Args:
        output: Raw JSON output from ``pip-audit --format json``.
        source: File or project path the audit was run against; used as the
            ``file`` shown for each issue.
        data: Pre-parsed JSON payload when already extracted from ``output``.

    Returns:
        List of parsed dependency vulnerability issues. One issue per
        (dependency, vulnerability) pair.
    """
    if data is None:
        data = extract_pip_audit_payload(output)
    if data is None:
        return []

    dependencies = data.get("dependencies", [])
    if not isinstance(dependencies, list):
        return []

    issues: list[PipAuditIssue] = []

    for dep in dependencies:
        if not isinstance(dep, dict):
            continue

        # Skipped dependencies carry a ``skip_reason`` and no ``vulns``.
        vulns = dep.get("vulns")
        if not isinstance(vulns, list) or not vulns:
            continue

        package_name = validate_str_field(dep.get("name"), "package_name")
        package_version = validate_str_field(dep.get("version"), "package_version")

        for vuln in vulns:
            if not isinstance(vuln, dict):
                continue

            vuln_id = validate_str_field(vuln.get("id"), "vuln_id", log_warning=True)
            if not vuln_id:
                logger.warning("Skipping pip-audit vulnerability with empty id")
                continue

            issues.append(
                PipAuditIssue(
                    file=source or package_name,
                    line=0,  # pip-audit does not provide line numbers
                    column=0,
                    vuln_id=vuln_id,
                    package_name=package_name,
                    package_version=package_version,
                    severity="UNKNOWN",
                    fix_versions=_coerce_str_list(vuln.get("fix_versions")),
                    aliases=_coerce_str_list(vuln.get("aliases")),
                    description=validate_str_field(
                        vuln.get("description"),
                        "description",
                    ),
                ),
            )

    return issues
