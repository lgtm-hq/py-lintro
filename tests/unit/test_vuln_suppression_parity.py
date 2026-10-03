"""Guard: every ``.grype.yaml`` ignore mirrors a live ``.osv-scanner.toml`` entry.

Grype ignore rules have no expiry, so on their own they would silently outlive
their rationale. The time box lives in ``.osv-scanner.toml`` (``ignoreUntil``),
and ``.grype.yaml`` may only mirror entries that are still suppressed there.
These tests fail CI when a grype ignore has no OSV counterpart, when that
counterpart has lapsed, or when the two files disagree on the package.
"""

from __future__ import annotations

import datetime
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml
from assertpy import assert_that

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GRYPE_CONFIG = _REPO_ROOT / ".grype.yaml"
_OSV_CONFIG = _REPO_ROOT / ".osv-scanner.toml"


def _grype_ignores() -> list[dict[str, Any]]:
    """Return the ``ignore`` rules from ``.grype.yaml``.

    Returns:
        The list of grype ignore rules (empty when the file or key is absent).
    """
    if not _GRYPE_CONFIG.is_file():
        return []
    data = yaml.safe_load(_GRYPE_CONFIG.read_text(encoding="utf-8")) or {}
    ignores = data.get("ignore") or []
    assert_that(ignores).is_instance_of(list)
    return list(ignores)


def _osv_entries() -> dict[str, dict[str, Any]]:
    """Return ``.osv-scanner.toml`` ``[[IgnoredVulns]]`` entries keyed by id.

    Returns:
        Mapping of vulnerability id to its OSV suppression entry.
    """
    data = tomllib.loads(_OSV_CONFIG.read_text(encoding="utf-8"))
    return {entry["id"]: entry for entry in data.get("IgnoredVulns", [])}


def _ignore_until(entry: dict[str, Any]) -> datetime.date:
    """Normalise an OSV ``ignoreUntil`` value to a date.

    Args:
        entry: A single ``[[IgnoredVulns]]`` entry.

    Returns:
        The expiry date of the suppression.
    """
    value = entry["ignoreUntil"]
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    return datetime.date.fromisoformat(str(value)[:10])


_GRYPE_RULES = _grype_ignores()


def test_grype_ignores_all_name_a_vulnerability_and_package() -> None:
    """Every grype ignore is scoped to one vulnerability id and one package."""
    for rule in _GRYPE_RULES:
        assert_that(rule).contains_key("vulnerability")
        assert_that(rule.get("package")).is_instance_of(dict)
        assert_that(rule["package"].get("name")).is_not_empty()


@pytest.mark.parametrize(
    "rule",
    _GRYPE_RULES,
    ids=[str(rule.get("vulnerability")) for rule in _GRYPE_RULES],
)
def test_grype_ignore_has_unexpired_osv_counterpart(rule: dict[str, Any]) -> None:
    """A grype ignore needs a matching OSV entry whose time box is still open.

    Args:
        rule: One ``ignore`` rule from ``.grype.yaml``.
    """
    vuln_id = rule["vulnerability"]
    entries = _osv_entries()
    assert_that(entries).described_as(
        f"{vuln_id} is ignored in .grype.yaml but has no [[IgnoredVulns]] "
        "entry in .osv-scanner.toml; remove it from .grype.yaml",
    ).contains_key(vuln_id)
    expiry = _ignore_until(entries[vuln_id])
    assert_that(expiry).described_as(
        f"{vuln_id}: .osv-scanner.toml ignoreUntil {expiry} has passed; "
        "re-verify the advisory and renew or remove both suppressions",
    ).is_greater_than(datetime.date.today())


@pytest.mark.parametrize(
    "rule",
    _GRYPE_RULES,
    ids=[str(rule.get("vulnerability")) for rule in _GRYPE_RULES],
)
def test_grype_ignore_package_matches_osv_reason(rule: dict[str, Any]) -> None:
    """The grype package name matches the package the OSV reason names first.

    OSV ``[[IgnoredVulns]]`` entries carry no package field, so the repo
    convention is a reason that starts with ``<package>@<version>``.

    Args:
        rule: One ``ignore`` rule from ``.grype.yaml``.
    """
    vuln_id = rule["vulnerability"]
    entries = _osv_entries()
    assert_that(entries).contains_key(vuln_id)
    reason = str(entries[vuln_id].get("reason", ""))
    assert_that(reason).described_as(
        f"{vuln_id}: .osv-scanner.toml reason must start with "
        f"'{rule['package']['name']}@' to match .grype.yaml",
    ).starts_with(f"{rule['package']['name']}@")
