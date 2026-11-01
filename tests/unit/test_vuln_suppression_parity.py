"""Guard: every ``.grype.yaml`` ignore mirrors a live ``.osv-scanner.toml`` entry.

Grype ignore rules have no expiry, so on their own they would silently outlive
their rationale. The time box lives in ``.osv-scanner.toml`` (``ignoreUntil``),
and ``.grype.yaml`` may only mirror entries that are still suppressed there.
These tests fail CI when a grype ignore has no OSV counterpart, when that
counterpart has lapsed, or when the two files disagree on the package.

One exception: rules scoped by ``package.location`` to the lgtm-ci tooling
checkout (``/.lgtm-ci-tooling/**``). The reusable SBOM workflow clones lgtm-ci
into the scanned directory, so grype sees lgtm-ci's own lockfile, which
osv-scanner never reads. An OSV counterpart would wrongly suppress the advisory
in lintro's own lockfiles too, so these rules carry their time box in
``_TOOLING_CHECKOUT_EXPIRY`` below instead, and CI fails once it lapses.
"""

from __future__ import annotations

import datetime
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml
from assertpy import assert_that
from packaging.version import Version

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GRYPE_CONFIG = _REPO_ROOT / ".grype.yaml"
_OSV_CONFIG = _REPO_ROOT / ".osv-scanner.toml"
_TOOLING_CHECKOUT_PREFIX = "/.lgtm-ci-tooling/"

# Time boxes for grype ignores scoped to the lgtm-ci tooling checkout. Each
# entry must match exactly one such rule in .grype.yaml. Re-verify the
# upstream lgtm-ci release before extending a date.
_TOOLING_CHECKOUT_EXPIRY: dict[str, datetime.date] = {}


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


def _is_tooling_checkout_rule(rule: dict[str, Any]) -> bool:
    """Return whether a grype rule is scoped to the lgtm-ci tooling checkout.

    Args:
        rule: One ``ignore`` rule from ``.grype.yaml``.

    Returns:
        True when ``package.location`` lies under the tooling checkout.
    """
    package = rule.get("package") or {}
    location = str(package.get("location") or "")
    return location.startswith(_TOOLING_CHECKOUT_PREFIX)


_GRYPE_RULES = _grype_ignores()
_TOOLING_RULES = [rule for rule in _GRYPE_RULES if _is_tooling_checkout_rule(rule)]
_MIRRORED_RULES = [rule for rule in _GRYPE_RULES if not _is_tooling_checkout_rule(rule)]


def test_grype_ignores_all_name_a_vulnerability_and_package() -> None:
    """Every grype ignore is scoped to one vulnerability id and one package."""
    for rule in _GRYPE_RULES:
        assert_that(rule).contains_key("vulnerability")
        assert_that(rule.get("package")).is_instance_of(dict)
        assert_that(rule["package"].get("name")).is_not_empty()


@pytest.mark.parametrize(
    "rule",
    _MIRRORED_RULES,
    ids=[str(rule.get("vulnerability")) for rule in _MIRRORED_RULES],
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
    _MIRRORED_RULES,
    ids=[str(rule.get("vulnerability")) for rule in _MIRRORED_RULES],
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


def test_semgrep_telemetry_ignore_cannot_hide_vulnerable_application_lock() -> None:
    """An advisory-global tool exception requires a patched application copy."""
    if "GHSA-xcq7-p3pf-p88h" not in _osv_entries():
        return
    lock = tomllib.loads((_REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))
    versions = [
        Version(package["version"])
        for package in lock["package"]
        if package["name"] == "opentelemetry-api"
    ]
    assert_that(versions).is_not_empty()
    for version in versions:
        assert_that(version >= Version("1.45.1")).described_as(
            "The Semgrep-only exception must not hide GHSA-xcq7-p3pf-p88h "
            "in uv.lock; patch application telemetry to at least 1.45.1",
        ).is_true()


def test_tooling_checkout_ignores_are_time_boxed() -> None:
    """Every tooling-checkout grype ignore needs an open time box here.

    A plain loop rather than a parametrized test: with no tooling-checkout
    rules in ``.grype.yaml`` (the normal state) a parametrized test would only
    report an empty-parameter-set skip.
    """
    for rule in _TOOLING_RULES:
        vuln_id = rule["vulnerability"]
        assert_that(_TOOLING_CHECKOUT_EXPIRY).described_as(
            f"{vuln_id} is ignored for the lgtm-ci tooling checkout but has no "
            "_TOOLING_CHECKOUT_EXPIRY entry",
        ).contains_key(vuln_id)
        expiry = _TOOLING_CHECKOUT_EXPIRY[vuln_id]
        assert_that(expiry).described_as(
            f"{vuln_id}: tooling-checkout ignore expired on {expiry}; adopt the "
            "lgtm-ci release that fixes it, or re-verify and renew",
        ).is_greater_than(datetime.date.today())


def test_tooling_checkout_expiry_entries_have_rules() -> None:
    """Every ``_TOOLING_CHECKOUT_EXPIRY`` entry still has a matching rule."""
    rule_ids = sorted(str(rule["vulnerability"]) for rule in _TOOLING_RULES)
    assert_that(rule_ids).is_equal_to(sorted(_TOOLING_CHECKOUT_EXPIRY))
