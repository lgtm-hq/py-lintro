"""SARIF 2.1.0 ingestion parser (proof of concept for issue #1066).

Decodes a SARIF log into lintro ``SarifIssue`` objects. The goal of this
proof of concept is to validate that the SARIF result model maps cleanly onto
lintro's shared ``BaseIssue`` shape (file/line/column/message/code/severity/
fixable/doc_url) across tools, without committing to wiring SARIF into any
tool plugin.

Mapping summary (SARIF 2.1.0 -> lintro):
    result.locations[0].physicalLocation.artifactLocation.uri -> file
    result.locations[0].physicalLocation.region.startLine      -> line
    result.locations[0].physicalLocation.region.startColumn    -> column
    result.locations[0].physicalLocation.region.endLine/Column -> end_line/col
    result.message.text                                        -> message
    result.ruleId (or rules[ruleIndex].id)                     -> code
    result.level (or rule.defaultConfiguration.level, "warning") -> severity
    bool(result.fixes)                                         -> fixable
    rule.helpUri                                               -> doc_url
    run.tool.driver.name                                       -> tool_name

References:
    OASIS SARIF v2.1.0 specification, sections 3.27 (result),
    3.28 (location), 3.30 (region), 3.49 (reportingDescriptor).
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import unquote, urlparse

from loguru import logger

from lintro.enums.severity_level import (
    SeverityLevel,
    normalize_severity_level,
)
from lintro.parsers.sarif.sarif_issue import SarifIssue

# SARIF ``level`` values that lintro's alias table does not already cover.
_SARIF_LEVEL_OVERRIDES: dict[str, SeverityLevel] = {
    "NONE": SeverityLevel.INFO,
}

_DEFAULT_SARIF_LEVEL = "warning"


def _uri_to_path(uri: str) -> str:
    """Convert a SARIF artifact URI to a filesystem-style path.

    SARIF encodes locations as URIs. Absolute locations use a ``file://``
    scheme; relative locations (paired with a ``uriBaseId``) are plain
    relative paths. Percent-encoding is decoded in both cases.

    Args:
        uri: The ``artifactLocation.uri`` string from a SARIF result.

    Returns:
        A decoded filesystem path. ``file://`` URIs yield their absolute
        path component; relative URIs are returned as-is (decoded).
    """
    if not uri:
        return ""
    if uri.startswith("file:"):
        parsed = urlparse(uri)
        return unquote(parsed.path)
    return unquote(uri)


def _normalize_level(raw: str) -> str:
    """Normalize a SARIF ``level`` into a canonical severity string.

    SARIF defines four levels (``error``, ``warning``, ``note``, ``none``).
    ``error``/``warning``/``note`` are already understood by lintro's shared
    severity alias table; ``none`` is remapped to ``INFO`` here so it does not
    fall through to the default.

    Args:
        raw: The SARIF ``level`` string (any case).

    Returns:
        A canonical ``SeverityLevel`` value as a string.
    """
    upper = raw.upper()
    override = _SARIF_LEVEL_OVERRIDES.get(upper)
    if override is not None:
        return str(override)
    try:
        return str(normalize_severity_level(upper))
    except ValueError:
        return str(SeverityLevel.WARNING)


def _build_rule_index(driver: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Index a run's reporting descriptors (rules) by their ``id``.

    Results reference rules by ``ruleId`` (and optionally ``ruleIndex``).
    Indexing by ``id`` lets the parser resolve ``helpUri`` and the rule's
    ``defaultConfiguration.level`` for results that omit an inline level.

    Args:
        driver: The ``run.tool.driver`` object.

    Returns:
        Mapping of rule id to the rule (reportingDescriptor) object.
    """
    index: dict[str, dict[str, Any]] = {}
    for rule in driver.get("rules", []) or []:
        if isinstance(rule, dict):
            rule_id = rule.get("id")
            if isinstance(rule_id, str):
                index[rule_id] = rule
    return index


def _resolve_rule(
    result: dict[str, Any],
    rules_by_id: dict[str, dict[str, Any]],
    rules_list: list[Any],
) -> dict[str, Any]:
    """Resolve the rule object a result refers to.

    Prefers ``ruleId`` lookup, falling back to positional ``ruleIndex`` into
    the driver's rules array (both are permitted by the spec).

    Args:
        result: A single SARIF ``result`` object.
        rules_by_id: Rules indexed by id.
        rules_list: The raw ordered rules array (for ``ruleIndex`` lookup).

    Returns:
        The matching rule object, or an empty dict when none is found.
    """
    rule_id = result.get("ruleId")
    if isinstance(rule_id, str) and rule_id in rules_by_id:
        return rules_by_id[rule_id]
    idx = result.get("ruleIndex")
    if isinstance(idx, int) and 0 <= idx < len(rules_list):
        candidate = rules_list[idx]
        if isinstance(candidate, dict):
            return candidate
    return {}


def _parse_result(
    result: dict[str, Any],
    rules_by_id: dict[str, dict[str, Any]],
    rules_list: list[Any],
    tool_name: str,
) -> SarifIssue | None:
    """Convert a single SARIF ``result`` into a ``SarifIssue``.

    Args:
        result: A single SARIF ``result`` object.
        rules_by_id: Rules indexed by id for the owning run.
        rules_list: The raw ordered rules array for the owning run.
        tool_name: The driver name of the owning run.

    Returns:
        A populated ``SarifIssue``, or None when the result has no usable
        physical location (SARIF permits location-free results, which do not
        map onto lintro's file/line issue model).
    """
    rule = _resolve_rule(result, rules_by_id, rules_list)

    locations = result.get("locations") or []
    physical: dict[str, Any] = {}
    if locations and isinstance(locations[0], dict):
        physical = locations[0].get("physicalLocation") or {}
    if not physical:
        logger.debug("Skipping SARIF result without a physical location")
        return None

    # PoC limitation: ``artifactLocation.uriBaseId`` is not resolved against
    # ``run.originalUriBaseIds``, so a relative ``uri`` is taken as-is. Tools
    # lintro runs from the project root emit workspace-relative URIs, which
    # matches lintro's file/path model; a production parser must resolve
    # ``uriBaseId`` first (tracked in the evaluation doc's risk table).
    artifact = physical.get("artifactLocation") or {}
    file_path = _uri_to_path(str(artifact.get("uri", "")))

    region = physical.get("region") or {}
    line = region.get("startLine")
    column = region.get("startColumn")
    end_line = region.get("endLine")
    end_column = region.get("endColumn")

    message = ""
    msg_obj = result.get("message")
    if isinstance(msg_obj, dict):
        message = str(msg_obj.get("text", ""))

    rule_id = result.get("ruleId")
    code = rule_id if isinstance(rule_id, str) else str(rule.get("id", ""))

    raw_level = result.get("level")
    if not isinstance(raw_level, str):
        default_config = rule.get("defaultConfiguration") or {}
        raw_level = default_config.get("level", _DEFAULT_SARIF_LEVEL)
    level = _normalize_level(str(raw_level))

    fixes = result.get("fixes")
    fixable = bool(fixes)

    doc_url = str(rule.get("helpUri", "")) if rule else ""

    return SarifIssue(
        file=file_path,
        line=int(line) if isinstance(line, int) else 0,
        column=int(column) if isinstance(column, int) else 0,
        message=message,
        doc_url=doc_url,
        code=code,
        level=level,
        fixable=fixable,
        end_line=int(end_line) if isinstance(end_line, int) else None,
        end_column=int(end_column) if isinstance(end_column, int) else None,
        tool_name=tool_name,
    )


def parse_sarif_output(output: str | None) -> list[SarifIssue]:
    """Parse a SARIF 2.1.0 log into a flat list of ``SarifIssue`` objects.

    All runs in the log are flattened into a single list; each result records
    its originating ``tool_name`` so multi-run logs remain attributable. The
    parser is defensive: malformed JSON, a non-object root, or a missing
    ``runs`` array yield an empty list rather than raising.

    Args:
        output: The SARIF log as a JSON string, or None.

    Returns:
        List of parsed issues. Empty for None, blank, invalid JSON, or a
        structurally unexpected document.
    """
    if output is None or not output.strip():
        return []

    try:
        data = json.loads(output)
    except json.JSONDecodeError as exc:
        logger.warning(f"Failed to parse SARIF JSON output: {exc}")
        return []

    if not isinstance(data, dict):
        logger.warning(
            "SARIF output must be a JSON object, got %s",
            type(data).__name__,
        )
        return []

    runs = data.get("runs")
    if not isinstance(runs, list):
        logger.warning("SARIF log has no 'runs' array")
        return []

    issues: list[SarifIssue] = []
    for run in runs:
        if not isinstance(run, dict):
            continue
        driver = (run.get("tool") or {}).get("driver") or {}
        tool_name = str(driver.get("name", ""))
        rules_by_id = _build_rule_index(driver)
        rules_list = driver.get("rules", []) or []

        for result in run.get("results", []) or []:
            if not isinstance(result, dict):
                continue
            try:
                issue = _parse_result(result, rules_by_id, rules_list, tool_name)
            except (KeyError, TypeError, ValueError) as exc:
                logger.debug(f"Failed to parse SARIF result: {exc}")
                continue
            if issue is not None:
                issues.append(issue)

    return issues
