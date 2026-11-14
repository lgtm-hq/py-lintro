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
import fnmatch
import os
import re
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml
from assertpy import assert_that
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
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


@pytest.mark.parametrize(
    ("package", "minimum", "advisories"),
    [
        (
            "markdown-it",
            "15.0.3",
            "GHSA-r7fv-28h4-cvq7, GHSA-vfgh-jcmw-g37q and GHSA-gfr2-rv44-r23c",
        ),
        ("re2js", "2.8.7", "GHSA-gg6h-894w-4m9c"),
    ],
    ids=["markdown-it", "re2js"],
)
def test_markdownlint_dependency_lockfiles_remain_patched(
    package: str,
    minimum: str,
    advisories: str,
) -> None:
    """Keep every locked markdownlint dependency copy above its security floor.

    Args:
        package: Dependency whose copies must remain patched.
        minimum: First patched version admitted by this guard.
        advisories: Advisories fixed by the minimum version.
    """
    versions = []
    for lockfile in (_REPO_ROOT / "bun.lock", _REPO_ROOT / "apps/site/bun.lock"):
        for version in re.findall(
            rf'\["{re.escape(package)}@([^"]+)"',
            lockfile.read_text(encoding="utf-8"),
        ):
            versions.append(version)
            assert_that(Version(version) >= Version(minimum)).described_as(
                f"{lockfile.relative_to(_REPO_ROOT)}: {package}@{version} must "
                f"fix {advisories}",
            ).is_true()
    assert_that(versions).is_not_empty()


# Advisories suppressed in .osv-scanner.toml only because Semgrep pins the
# isolated requirements-semgrep.txt lock. OSV ignores are advisory-global, so
# each entry records the package and the affected ranges (alternatives, any
# one matching means affected) that no other Python lockfile may carry.
_SEMGREP_LOCK = Path("requirements-semgrep.txt")
_MCP_AFFECTED = ("<1.30.0", ">=2.0.0a0,<2.2.0")
_SEMGREP_ONLY_ADVISORIES: dict[str, tuple[str, tuple[str, ...]]] = {
    "GHSA-xcq7-p3pf-p88h": ("opentelemetry-api", ("<1.45.1",)),
    "GHSA-mf5v-w3w3-f6cg": ("opentelemetry-api", ("<1.45.2",)),
    "GHSA-84m7-p3x7-pcfv": ("mcp", _MCP_AFFECTED),
    "GHSA-5h93-6whr-6q8j": ("mcp", _MCP_AFFECTED),
    "GHSA-rwrf-2pqf-9j8j": ("mcp", _MCP_AFFECTED),
    "GHSA-w4fh-qvv9-3v23": ("mcp", _MCP_AFFECTED),
}
_LOCKFILE_PATTERNS = ("uv.lock", "requirements*.txt", "pylock*.toml")
_SKIPPED_DIRS = frozenset(
    {".git", ".venv", "venv", "node_modules", "__pycache__", ".tox", ".nox"},
)
_REQUIREMENT_PIN = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?"
    r"\s*===?\s*(?P<version>[^\s;#\\]+)",
    re.MULTILINE,
)


def _semgrep_only_ids() -> list[str]:
    """Return the OSV ignore ids whose reason confines them to the Semgrep lock.

    Returns:
        Sorted ids of ``[[IgnoredVulns]]`` entries naming the Semgrep lock.
    """
    return sorted(
        vuln_id
        for vuln_id, entry in _osv_entries().items()
        if _SEMGREP_LOCK.name in str(entry.get("reason", ""))
    )


def _python_lockfiles(root: Path) -> list[Path]:
    """Return every Python lockfile under *root* except the Semgrep lock.

    Args:
        root: Directory to search.

    Returns:
        Sorted paths matching ``uv.lock``, ``requirements*.txt`` or
        ``pylock*.toml``, skipping virtualenvs and VCS metadata.
    """
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in _SKIPPED_DIRS]
        for filename in filenames:
            if any(fnmatch.fnmatch(filename, pat) for pat in _LOCKFILE_PATTERNS):
                found.append(Path(dirpath) / filename)
    return sorted(path for path in found if path.relative_to(root) != _SEMGREP_LOCK)


def _locked_versions(lockfile: Path, package: str) -> list[Version]:
    """Return the versions of *package* pinned in a Python lockfile.

    Args:
        lockfile: A ``uv.lock``, ``pylock*.toml`` or ``requirements*.txt`` file.
        package: Distribution name to look up.

    Returns:
        Every locked version of the package (empty when it is absent).
    """
    wanted = canonicalize_name(package)
    text = lockfile.read_text(encoding="utf-8")
    if lockfile.suffix == ".txt":
        pins = [(m["name"], m["version"]) for m in _REQUIREMENT_PIN.finditer(text)]
    else:
        data = tomllib.loads(text)
        entries = [*data.get("package", []), *data.get("packages", [])]
        pins = [(entry["name"], entry["version"]) for entry in entries]
    return [
        Version(version) for name, version in pins if canonicalize_name(name) == wanted
    ]


def _affected_pins(root: Path, advisory: str) -> list[str]:
    """Return ``path: package@version`` for each lockfile pin *advisory* affects.

    Args:
        root: Repository root to scan.
        advisory: Key of ``_SEMGREP_ONLY_ADVISORIES``.

    Returns:
        Affected pins outside the Semgrep lock (empty when none).
    """
    package, ranges = _SEMGREP_ONLY_ADVISORIES[advisory]
    specifiers = [SpecifierSet(spec, prereleases=True) for spec in ranges]
    return [
        f"{lockfile.relative_to(root)}: {package}@{version}"
        for lockfile in _python_lockfiles(root)
        for version in _locked_versions(lockfile, package)
        if any(version in spec for spec in specifiers)
    ]


def test_every_semgrep_only_ignore_records_its_affected_range() -> None:
    """A Semgrep-only OSV ignore needs a package and range the guard can check."""
    ids = _semgrep_only_ids()
    assert_that(ids).is_not_empty()
    for vuln_id in ids:
        assert_that(_SEMGREP_ONLY_ADVISORIES).described_as(
            f"{vuln_id} is confined to {_SEMGREP_LOCK} in .osv-scanner.toml; "
            "record its package and affected ranges in _SEMGREP_ONLY_ADVISORIES",
        ).contains_key(vuln_id)
        package = _SEMGREP_ONLY_ADVISORIES[vuln_id][0]
        reason = str(_osv_entries()[vuln_id]["reason"])
        assert_that(reason).contains(package)


def test_repo_python_lockfiles_exclude_only_the_semgrep_lock() -> None:
    """The guard scans uv.lock and never the exempt Semgrep lock."""
    found = [path.relative_to(_REPO_ROOT) for path in _python_lockfiles(_REPO_ROOT)]
    assert_that(found).contains(Path("uv.lock"))
    assert_that(found).does_not_contain(_SEMGREP_LOCK)
    assert_that((_REPO_ROOT / _SEMGREP_LOCK).is_file()).is_true()


def test_any_new_python_lockfile_with_an_affected_pin_is_reported(
    tmp_path: Path,
) -> None:
    """A lockfile added anywhere, in any supported format, is checked.

    Args:
        tmp_path: Scratch repository root.
    """
    (tmp_path / _SEMGREP_LOCK).write_text(
        "mcp==1.29.0 \\\n    --hash=sha256:00\nopentelemetry-api==1.37.0\n",
        encoding="utf-8",
    )
    runtime = tmp_path / "review-runtime"
    runtime.mkdir()
    (runtime / "requirements.txt").write_text(
        "opentelemetry-api==1.45.1 ; python_version >= '3.11'\n",
        encoding="utf-8",
    )
    (tmp_path / "pylock.toml").write_text(
        '[[packages]]\nname = "MCP"\nversion = "2.1.1"\n',
        encoding="utf-8",
    )
    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname = "mcp"\nversion = "2.3.0"\n'
        '[[package]]\nname = "opentelemetry-api"\nversion = "1.45.2"\n',
        encoding="utf-8",
    )
    assert_that(_affected_pins(tmp_path, "GHSA-xcq7-p3pf-p88h")).is_empty()
    assert_that(_affected_pins(tmp_path, "GHSA-mf5v-w3w3-f6cg")).is_equal_to(
        ["review-runtime/requirements.txt: opentelemetry-api@1.45.1"],
    )
    assert_that(_affected_pins(tmp_path, "GHSA-84m7-p3x7-pcfv")).is_equal_to(
        ["pylock.toml: mcp@2.1.1"],
    )


@pytest.mark.parametrize("advisory", _semgrep_only_ids())
def test_semgrep_telemetry_ignore_cannot_hide_vulnerable_application_lock(
    advisory: str,
) -> None:
    """A Semgrep-only OSV ignore must not hide any other Python lockfile.

    OSV ignores apply to every lockfile osv-scanner reads, so each advisory
    confined to ``requirements-semgrep.txt`` (the opentelemetry-api telemetry
    and mcp entries) must not affect a pin in ``uv.lock``, any
    ``requirements*.txt`` or any ``pylock*.toml`` elsewhere in the repository.

    Args:
        advisory: The OSV id of a Semgrep-only exception.
    """
    assert_that(_SEMGREP_ONLY_ADVISORIES).contains_key(advisory)
    package = _SEMGREP_ONLY_ADVISORIES[advisory][0]
    assert_that(_affected_pins(_REPO_ROOT, advisory)).described_as(
        f"The Semgrep-only exception {advisory} must not hide an affected "
        f"{package} outside {_SEMGREP_LOCK}; patch these pins",
    ).is_empty()


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
