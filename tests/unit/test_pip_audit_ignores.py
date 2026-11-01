# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""Every pip-audit ignore is a time-boxed osv-scanner suppression too.

pip-audit has no suppression file of its own, so ``[tool.lintro.pip_audit]
ignore_vulns`` would otherwise be an ignore with no reason and no expiry.
Each id there must match an ``[[IgnoredVulns]]`` entry in ``.osv-scanner.toml``
by id, or appear in that entry's reason as an alias, and the entry must not
have expired.
"""

from __future__ import annotations

import datetime as dt
import re
import tomllib
from pathlib import Path

from assertpy import assert_that
from packaging.requirements import Requirement
from packaging.version import Version

_ROOT = Path(__file__).resolve().parents[2]


def _ignored_by_pip_audit() -> list[str]:
    data = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    section = data.get("tool", {}).get("lintro", {}).get("pip_audit", {})
    value = section.get("ignore_vulns", [])
    assert isinstance(value, list)
    return [str(entry) for entry in value]


def _osv_suppressions() -> list[dict[str, object]]:
    data = tomllib.loads((_ROOT / ".osv-scanner.toml").read_text(encoding="utf-8"))
    entries = data.get("IgnoredVulns", [])
    assert isinstance(entries, list)
    return [entry for entry in entries if isinstance(entry, dict)]


def _names(entry: dict[str, object], vuln_id: str) -> bool:
    """Return whether *entry* names *vuln_id* by id or as a whole-word alias."""
    if entry.get("id") == vuln_id:
        return True
    reason = str(entry.get("reason", ""))
    return re.search(rf"(?<![\w-]){re.escape(vuln_id)}(?![\w-])", reason) is not None


def test_every_pip_audit_ignore_is_a_live_osv_suppression() -> None:
    """An ignore with no matching, unexpired, reasoned osv entry is a silent exemption."""
    suppressions = _osv_suppressions()
    today = dt.date.today()
    for vuln_id in _ignored_by_pip_audit():
        matching = [entry for entry in suppressions if _names(entry, vuln_id)]
        assert_that(matching).described_as(
            f"{vuln_id}: no [[IgnoredVulns]] entry names it by id or as an alias",
        ).is_not_empty()
        for entry in matching:
            assert_that(str(entry.get("reason", "")).strip()).described_as(
                f"{vuln_id}: the osv entry carries no reason",
            ).is_not_empty()
            until = entry.get("ignoreUntil")
            assert_that(until).described_as(f"{vuln_id}: ignoreUntil").is_instance_of(
                dt.date,
            )
            assert isinstance(until, dt.date)
            assert_that(until >= today).described_as(
                f"{vuln_id}: the osv entry expired on {until}; drop both ignores or "
                "renew them with a fresh reason",
            ).is_true()


def test_semgrep_telemetry_exception_is_paired_with_pip_audit() -> None:
    """Both scanners cover the same remaining, time-boxed Semgrep blocker."""
    if not any(
        entry.get("id") == "GHSA-xcq7-p3pf-p88h" for entry in _osv_suppressions()
    ):
        return
    assert_that(_ignored_by_pip_audit()).contains(
        "CVE-2026-114603",
        "GHSA-xcq7-p3pf-p88h",
    )


def test_the_alias_match_is_whole_id_only() -> None:
    """A prefix of an id, or a bare year, must not satisfy the pairing."""
    entry: dict[str, object] = {
        "id": "GHSA-84m7-p3x7-pcfv",
        "reason": "mcp@1.29.0 (alias CVE-2026-59951): not reachable",
    }
    assert_that(_names(entry, "CVE-2026-59951")).is_true()
    assert_that(_names(entry, "GHSA-84m7-p3x7-pcfv")).is_true()
    assert_that(_names(entry, "CVE-2026-5995")).is_false()
    assert_that(_names(entry, "CVE-2026")).is_false()
    assert_that(_names(entry, "GHSA-84m7-p3x7")).is_false()


def test_mcp_exceptions_do_not_cover_a_vulnerable_lintro_runtime() -> None:
    """The scan-only exceptions must never hide an affected runtime SDK."""
    project = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = [
        *project["project"]["optional-dependencies"]["mcp"],
        *project["dependency-groups"]["dev"],
    ]
    mcp_requirements = [
        req for item in dependencies if (req := Requirement(item)).name == "mcp"
    ]
    assert mcp_requirements
    for requirement in mcp_requirements:
        for vulnerable in ("1.29.0", "2.1.1"):
            assert Version(vulnerable) not in requirement.specifier
    lock = tomllib.loads((_ROOT / "uv.lock").read_text(encoding="utf-8"))
    versions = [entry["version"] for entry in lock["package"] if entry["name"] == "mcp"]
    assert versions
    assert all(Version(version) >= Version("2.2.0") for version in versions)
