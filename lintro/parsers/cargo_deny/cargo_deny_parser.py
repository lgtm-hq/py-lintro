"""Parser for cargo-deny JSON output."""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from lintro.parsers.cargo_deny.cargo_deny_issue import CargoDenyIssue


def _extract_crate_info(labels: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    """Extract crate name and version from diagnostic labels.

    Args:
        labels: List of label objects from the diagnostic.

    Returns:
        Tuple of (crate_name, crate_version).
    """
    for label in labels:
        message = label.get("message", "")
        if isinstance(message, str) and message.startswith("crate "):
            # Format: "crate foo" or "crate foo@1.0.0"
            crate_info = message[6:]  # Remove "crate " prefix
            if "@" in crate_info:
                name, version = crate_info.split("@", 1)
                return name.strip(), version.strip()
            return crate_info.strip(), None
    return None, None


def _parse_diagnostic(item: dict[str, Any]) -> CargoDenyIssue | None:
    """Parse a diagnostic message from cargo-deny output.

    Args:
        item: A diagnostic JSON object.

    Returns:
        CargoDenyIssue or None if parsing fails.
    """
    try:
        fields = item.get("fields", {})
        if not isinstance(fields, dict):
            return None

        severity = fields.get("severity", "")
        code = fields.get("code", "")
        message = fields.get("message", "")
        labels = fields.get("labels", [])

        if not isinstance(severity, str) or not severity:
            return None

        # Normalize severity to lowercase
        severity = severity.lower()

        # Extract crate info from labels
        crate_name, crate_version = None, None
        if isinstance(labels, list):
            crate_name, crate_version = _extract_crate_info(labels)

        return CargoDenyIssue(
            file="Cargo.toml",  # cargo-deny operates at project level
            line=0,  # No line information available
            column=0,
            code=str(code) if code else None,
            severity=severity,
            message=str(message) if message else "",
            crate_name=crate_name,
            crate_version=crate_version,
        )
    except (KeyError, TypeError, ValueError) as e:
        logger.debug(f"Failed to parse cargo-deny diagnostic: {e}")
        return None


def _parse_advisory(item: dict[str, Any]) -> CargoDenyIssue | None:
    """Parse an advisory message from cargo-deny output.

    Args:
        item: An advisory JSON object.

    Returns:
        CargoDenyIssue or None if parsing fails.
    """
    try:
        fields = item.get("fields", {})
        if not isinstance(fields, dict):
            return None

        advisory = fields.get("advisory", {})
        if not isinstance(advisory, dict):
            return None

        advisory_id = advisory.get("id", "")
        advisory_severity = advisory.get("severity", "")

        # Extract patched versions
        versions = fields.get("versions", {})
        patched = None
        if isinstance(versions, dict):
            patched_list = versions.get("patched", [])
            if isinstance(patched_list, list):
                patched = [str(v) for v in patched_list if v]

        # Get crate information from the package field
        package = fields.get("package", {})
        crate_name = None
        crate_version = None
        if isinstance(package, dict):
            crate_name = package.get("name")
            crate_version = package.get("version")

        return CargoDenyIssue(
            file="Cargo.toml",
            line=0,
            column=0,
            code=str(advisory_id) if advisory_id else "ADVISORY",
            severity="error",  # Advisories are treated as errors
            crate_name=str(crate_name) if crate_name else None,
            crate_version=str(crate_version) if crate_version else None,
            advisory_id=str(advisory_id) if advisory_id else None,
            advisory_severity=str(advisory_severity) if advisory_severity else None,
            patched_versions=patched,
        )
    except (KeyError, TypeError, ValueError) as e:
        logger.debug(f"Failed to parse cargo-deny advisory: {e}")
        return None


def parse_cargo_deny_output(output: str) -> list[CargoDenyIssue]:
    """Parse cargo-deny JSON Lines output into CargoDenyIssue objects.

    cargo-deny outputs JSON Lines format (one JSON object per line) when
    using --format json. Each line can be:
    - A diagnostic message with type "diagnostic"
    - An advisory message with type "advisory"

    Args:
        output: Raw stdout emitted by cargo deny check --format json.

    Returns:
        A list of CargoDenyIssue instances. Returns an empty list when
        no issues are found or output cannot be parsed.
    """
    if not output or not output.strip():
        return []

    issues: list[CargoDenyIssue] = []

    for line in output.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue

        try:
            data = json.loads(line)
            if not isinstance(data, dict):
                continue

            item_type = data.get("type", "")

            if item_type == "diagnostic":
                parsed = _parse_diagnostic(data)
                if parsed is not None:
                    issues.append(parsed)
            elif item_type == "advisory":
                parsed = _parse_advisory(data)
                if parsed is not None:
                    issues.append(parsed)
            # Ignore other types like "summary", "build", etc.

        except json.JSONDecodeError:
            continue

    return issues
