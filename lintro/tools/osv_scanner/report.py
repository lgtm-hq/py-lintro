"""Decide whether osv-scanner's standard output is a whole scan report.

The findings parser reads stdout leniently, so that findings in a damaged
report are still kept. Whether the run is a complete verdict is decided
strictly: stdout, without surrounding whitespace, must be exactly one JSON
object that is a report. That object has a ``results`` list and no top-level
key osv-scanner's report does not have (``VulnerabilityResults`` in
``pkg/models/results.go``, the same at every release from 2.4.0 to 2.7.1).
Text before or after the object, a second object, or an ``error`` key beside
``results`` is not a report. A key that is not in :data:`REPORT_KEYS` is named
in the reason: a later osv-scanner release may add one, and until lintro knows
it every scan is incomplete. That is deliberate (fail closed).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from lintro.tools.core.incomplete_run import tool_message_excerpt

#: How many unknown keys the reason names, and how much of each.
_MAX_KEYS_NAMED: int = 5
_MAX_KEY_LENGTH: int = 60

#: Every top-level key osv-scanner's JSON report can have.
REPORT_KEYS: frozenset[str] = frozenset(
    {
        "results",
        "experimental_config",
        "experimental_generic_findings",
        "image_metadata",
        "license_summary",
    },
)


@dataclass(frozen=True)
class ReportCheck:
    """Whether stdout is a whole report, and why not.

    Attributes:
        ok: True when stdout is exactly one report object.
        reason: Why it is not, or an empty string when it is.
    """

    ok: bool
    reason: str = ""


def _parse_whole(stdout: str) -> object | None:
    """Return stdout parsed as one JSON value, or None when it is not one.

    Args:
        stdout: What osv-scanner printed on standard output.

    Returns:
        The parsed value, or None.
    """
    try:
        value: object = json.loads(stdout.strip())
    except ValueError:
        return None
    return value


def _unknown_keys_reason(keys: set[str]) -> str:
    """Name the top-level keys lintro does not know.

    Args:
        keys: The unknown keys.

    Returns:
        The reason, quoting at most ``_MAX_KEYS_NAMED`` keys as JSON strings.
    """
    ordered = sorted(keys)
    named = [
        json.dumps(
            key if len(key) <= _MAX_KEY_LENGTH else f"{key[:_MAX_KEY_LENGTH]}...",
        )
        for key in ordered[:_MAX_KEYS_NAMED]
    ]
    if len(ordered) > _MAX_KEYS_NAMED:
        named.append(f"and {len(ordered) - _MAX_KEYS_NAMED} more")
    return (
        "OSV-Scanner's report could not be read: it has top-level key(s) "
        f"lintro does not know ({', '.join(named)}), so its findings are "
        "incomplete. A newer osv-scanner may have added them; lintro treats "
        "every scan as incomplete until it is updated to know them."
    )


def check_report(stdout: str) -> ReportCheck:
    """Check that stdout is exactly one osv-scanner report.

    Args:
        stdout: What osv-scanner printed on standard output.

    Returns:
        The verdict, with a reason when stdout is not a report.
    """
    if not stdout.strip():
        return ReportCheck(ok=False, reason="OSV-Scanner printed no report")
    value = _parse_whole(stdout)
    if isinstance(value, dict):
        report: dict[str, Any] = value
        error = report.get("error")
        if set(report) == {"error"} and isinstance(error, str) and error.strip():
            return ReportCheck(
                ok=False,
                reason=(
                    "OSV-Scanner reported an error instead of a scan report: "
                    f"{tool_message_excerpt(error)}"
                ),
            )
        if isinstance(report.get("results"), list):
            unknown = set(report) - REPORT_KEYS
            if not unknown:
                return ReportCheck(ok=True)
            return ReportCheck(ok=False, reason=_unknown_keys_reason(unknown))
    return ReportCheck(
        ok=False,
        reason=(
            "OSV-Scanner's report could not be read: its standard output is "
            "not exactly one JSON scan report, so its findings are incomplete."
        ),
    )
