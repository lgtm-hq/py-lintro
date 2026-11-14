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
import json
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
from packaging.version import InvalidVersion, Version

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
# each entry records the package and the affected ranges from the advisory
# (alternatives: any one matching means affected) that no other Python
# lockfile may carry.
_SEMGREP_LOCK = Path("requirements-semgrep.txt")
_MCP_V2_AFFECTED = ">=2.0.0,<2.2.0"
_SEMGREP_ONLY_ADVISORIES: dict[str, tuple[str, tuple[str, ...]]] = {
    "GHSA-xcq7-p3pf-p88h": ("opentelemetry-api", ("<1.45.1",)),
    "GHSA-mf5v-w3w3-f6cg": ("opentelemetry-api", ("<1.45.2",)),
    "GHSA-84m7-p3x7-pcfv": ("mcp", (">=1.8.0,<1.30.0", _MCP_V2_AFFECTED)),
    "GHSA-5h93-6whr-6q8j": ("mcp", (">=1.8.0,<1.30.0", _MCP_V2_AFFECTED)),
    "GHSA-rwrf-2pqf-9j8j": ("mcp", (">=1.10.0,<1.30.0", _MCP_V2_AFFECTED)),
    "GHSA-w4fh-qvv9-3v23": ("mcp", ("<1.30.0", _MCP_V2_AFFECTED)),
}
# Filenames the Python extractors of osv-scanner 2.7.1 (osv-scalibr 0.5.3)
# accept. Its requirements extractor takes any ``.txt`` whose basename
# contains ``requirements``.
_REQUIREMENTS_PATTERN = "*requirements*.txt"
_TOML_LOCK_PATTERNS = (
    "uv.lock",
    "pylock.toml",
    "pylock.*.toml",
    "poetry.lock",
    "pdm.lock",
)
_PIPFILE_LOCK = "Pipfile.lock"
_SKIPPED_DIRS = frozenset(
    {".git", ".venv", "venv", "node_modules", "__pycache__", ".tox", ".nox"},
)
# Requirements-file grammar, mirroring osv-scalibr's parser (which follows
# pip's req_file.py): only exact ``==``/``===`` pins carry a version, and
# osv-scanner 2.7.1 reports nothing for ranges. Includes are followed for
# ``-r``/``--requirement`` and, more strictly than OSV, ``-c``/``--constraint``.
_REQ_COMMENT = re.compile(r"(^|\s+)#.*$")
# Long options take ``=``; short options keep a following ``=`` as part of the
# path, as pip does (``-r=base.txt`` reads the file ``=base.txt``).
_REQ_INCLUDE = re.compile(
    r"^(?:(?:--requirement|--constraint)(?:\s*=\s*|\s+)|(?:-r|-c)\s*)(?P<path>\S+)$",
)
_REQ_OPTIONS = re.compile(r"(?:\s+|^)(?:--hash|--global-option|--config-settings|-C).*")
_REQ_EXTRAS = re.compile(r"\[[^\[\]]*\]")
_REQ_UNSUPPORTED = re.compile(r"\*|<[^=]|,|!=")
_REQ_SEPARATORS = ("===", "==")

_Pin = tuple[Path, str, str]


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


def _is_python_lockfile(filename: str) -> bool:
    """Return whether osv-scanner reads *filename* as a Python lockfile.

    Args:
        filename: Basename of a file.

    Returns:
        True for requirements files and the TOML and Pipfile locks.
    """
    patterns = (_REQUIREMENTS_PATTERN, _PIPFILE_LOCK, *_TOML_LOCK_PATTERNS)
    return any(fnmatch.fnmatchcase(filename, pattern) for pattern in patterns)


def _python_lockfiles(root: Path) -> list[Path]:
    """Return every Python lockfile under *root* except the Semgrep lock.

    Args:
        root: Directory to search.

    Returns:
        Sorted lockfile paths, skipping virtualenvs and VCS metadata.
    """
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in _SKIPPED_DIRS]
        found.extend(
            Path(dirpath) / name for name in filenames if _is_python_lockfile(name)
        )
    return sorted(path for path in found if path.relative_to(root) != _SEMGREP_LOCK)


def _logical_lines(text: str) -> list[str]:
    """Strip ``#`` comments and join backslash continuations.

    Args:
        text: Contents of a requirements file.

    Returns:
        The logical lines, as osv-scalibr reads them.
    """
    lines: list[str] = []
    pending = ""
    for raw in text.splitlines():
        line = _REQ_COMMENT.sub("", raw)
        if line.endswith("\\"):
            pending += line[:-1]
            continue
        lines.append(pending + line)
        pending = ""
    if pending:
        lines.append(pending)
    return lines


def _requirement_pins(path: Path, seen: set[Path]) -> list[_Pin]:
    """Return the pins of a requirements file and of every file it includes.

    Args:
        path: Requirements file to read.
        seen: Files already read, so include cycles terminate.

    Returns:
        ``(file, name, version)`` for each exactly pinned requirement.
    """
    resolved = path.resolve()
    if resolved in seen or not path.is_file():
        return []
    seen.add(resolved)
    pins: list[_Pin] = []
    for raw in _logical_lines(path.read_text(encoding="utf-8")):
        line = _REQ_OPTIONS.sub("", raw).split(";", 1)[0].strip()
        if include := _REQ_INCLUDE.match(line):
            pins.extend(_requirement_pins(path.parent / include["path"], seen))
            continue
        line = _REQ_EXTRAS.sub("", line)
        if not line or line.startswith("-") or _REQ_UNSUPPORTED.search(line):
            continue
        separator = next((sep for sep in _REQ_SEPARATORS if sep in line), None)
        if separator is not None:
            name, version = line.split(separator, 1)
            pins.append((path, name.strip(), version.strip()))
    return pins


def _lockfile_pins(lockfile: Path) -> list[_Pin]:
    """Return every ``(file, name, version)`` pin osv-scanner reads from *lockfile*.

    Args:
        lockfile: A Python lockfile found by ``_python_lockfiles``.

    Returns:
        The pins, including those of included requirements files.
    """
    if lockfile.suffix == ".txt":
        return _requirement_pins(lockfile, set())
    text = lockfile.read_text(encoding="utf-8")
    if lockfile.name == _PIPFILE_LOCK:
        data = json.loads(text)
        return [
            (lockfile, name, str(spec.get("version", "")).removeprefix("=="))
            for section in ("default", "develop")
            for name, spec in data.get(section, {}).items()
        ]
    data = tomllib.loads(text)
    entries = [*data.get("package", []), *data.get("packages", [])]
    # Editable, virtual and path entries can omit ``version``; they carry no
    # registry pin for OSV to report, so skip them instead of failing.
    return [
        (lockfile, entry["name"], str(entry["version"]))
        for entry in entries
        if "name" in entry and entry.get("version")
    ]


def _affected_pins(root: Path, advisory: str) -> list[str]:
    """Return ``path: package@version`` for each lockfile pin *advisory* affects.

    Args:
        root: Repository root to scan.
        advisory: Key of ``_SEMGREP_ONLY_ADVISORIES``.

    Returns:
        Sorted, de-duplicated affected pins outside the Semgrep lock.
    """
    package, ranges = _SEMGREP_ONLY_ADVISORIES[advisory]
    wanted = canonicalize_name(package)
    specifiers = [SpecifierSet(spec, prereleases=True) for spec in ranges]
    affected: set[str] = set()
    for lockfile in _python_lockfiles(root):
        for path, name, raw_version in _lockfile_pins(lockfile):
            if canonicalize_name(name) != wanted:
                continue
            try:
                version = Version(raw_version)
            except InvalidVersion:
                continue
            if any(version in spec for spec in specifiers):
                where = Path(os.path.relpath(path, root)).as_posix()
                affected.add(f"{where}: {package}@{version}")
    return sorted(affected)


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


def _write_files(root: Path, files: dict[str, str]) -> None:
    """Write a scratch repository of relative path to text.

    Args:
        root: Scratch repository root.
        files: Relative file paths mapped to their contents.
    """
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def test_lock_entries_without_a_version_are_skipped(tmp_path: Path) -> None:
    """Editable or virtual lock entries have no version and are not pins.

    Args:
        tmp_path: Scratch repository root.
    """
    _write_files(
        tmp_path,
        {
            "uv.lock": (
                '[[package]]\nname = "my-app"\nsource = { editable = "." }\n\n'
                '[[package]]\nname = "opentelemetry-api"\nversion = "1.45.1"\n'
            ),
        },
    )
    assert_that(_affected_pins(tmp_path, "GHSA-mf5v-w3w3-f6cg")).is_equal_to(
        ["uv.lock: opentelemetry-api@1.45.1"],
    )


def test_any_filename_osv_reads_as_requirements_is_checked(tmp_path: Path) -> None:
    """OSV reads every ``.txt`` whose name contains ``requirements``.

    Args:
        tmp_path: Scratch repository root.
    """
    _write_files(
        tmp_path,
        {
            "review-runtime/runtime.requirements.txt": "opentelemetry-api==1.45.1\n",
            "dev-requirements-lock.txt": "mcp==1.29.0\n",
            "notes.txt": "mcp==1.0.0\n",
        },
    )
    assert_that(_affected_pins(tmp_path, "GHSA-mf5v-w3w3-f6cg")).is_equal_to(
        ["review-runtime/runtime.requirements.txt: opentelemetry-api@1.45.1"],
    )
    assert_that(_affected_pins(tmp_path, "GHSA-w4fh-qvv9-3v23")).is_equal_to(
        ["dev-requirements-lock.txt: mcp@1.29.0"],
    )


def test_requirements_includes_are_followed_without_looping(tmp_path: Path) -> None:
    """Pins reached through ``-r`` and ``-c`` count, even across a cycle.

    Args:
        tmp_path: Scratch repository root.
    """
    _write_files(
        tmp_path,
        {
            "requirements.txt": (
                "-r requirements/production.txt\n" "--constraint=constraints.txt\n"
            ),
            "requirements/production.txt": (
                "-r ../requirements.txt\n--requirement shared/base.txt\n"
            ),
            "requirements/shared/base.txt": "opentelemetry-api==1.45.1\n",
            "constraints.txt": "-c requirements.txt\nmcp==1.29.0\n",
        },
    )
    assert_that(_affected_pins(tmp_path, "GHSA-mf5v-w3w3-f6cg")).is_equal_to(
        ["requirements/shared/base.txt: opentelemetry-api@1.45.1"],
    )
    assert_that(_affected_pins(tmp_path, "GHSA-5h93-6whr-6q8j")).is_equal_to(
        ["constraints.txt: mcp@1.29.0"],
    )


def test_short_include_keeps_an_equals_sign_in_the_path(tmp_path: Path) -> None:
    """``-r=base.txt`` names the file ``=base.txt``, as pip and OSV read it.

    Args:
        tmp_path: Scratch repository root.
    """
    _write_files(
        tmp_path,
        {
            "requirements.txt": "-r=base.txt\n",
            "=base.txt": "opentelemetry-api==1.45.1\n",
            "base.txt": "opentelemetry-api==1.45.2\n",
        },
    )
    assert_that(_affected_pins(tmp_path, "GHSA-mf5v-w3w3-f6cg")).is_equal_to(
        ["=base.txt: opentelemetry-api@1.45.1"],
    )


def test_continued_and_commented_requirement_lines_parse_like_pip(
    tmp_path: Path,
) -> None:
    """Backslash continuations join first; ``#`` comments never count as pins.

    Args:
        tmp_path: Scratch repository root.
    """
    _write_files(
        tmp_path,
        {
            "requirements.txt": (
                "opentelemetry-api\\\n"
                "==1.45.1  # pinned for review\n"
                "# mcp==1.29.0\n"
                "mcp==2.3.0 # mcp==2.1.1\n"
            ),
        },
    )
    assert_that(_affected_pins(tmp_path, "GHSA-mf5v-w3w3-f6cg")).is_equal_to(
        ["requirements.txt: opentelemetry-api@1.45.1"],
    )
    assert_that(_affected_pins(tmp_path, "GHSA-w4fh-qvv9-3v23")).is_empty()


def test_only_exact_requirement_pins_are_checked(tmp_path: Path) -> None:
    """Like osv-scanner 2.7.1, only ``==``/``===`` single pins carry a version.

    Args:
        tmp_path: Scratch repository root.
    """
    _write_files(
        tmp_path,
        {
            "requirements.txt": (
                "opentelemetry-api>=1.37.0\nmcp~=2.1.0\nmcp==1.29.0,!=1.0\n"
            ),
            "tools-requirements.txt": "mcp===1.29.0\n",
        },
    )
    assert_that(_affected_pins(tmp_path, "GHSA-xcq7-p3pf-p88h")).is_empty()
    assert_that(_affected_pins(tmp_path, "GHSA-rwrf-2pqf-9j8j")).is_equal_to(
        ["tools-requirements.txt: mcp@1.29.0"],
    )


def test_poetry_pdm_and_pipfile_locks_are_checked(tmp_path: Path) -> None:
    """The other Python lockfiles OSV reads are scanned as well.

    Args:
        tmp_path: Scratch repository root.
    """
    _write_files(
        tmp_path,
        {
            "poetry/poetry.lock": '[[package]]\nname = "mcp"\nversion = "1.29.0"\n',
            "pdm/pdm.lock": '[[package]]\nname = "mcp"\nversion = "2.0.0"\n',
            "pipenv/Pipfile.lock": (
                '{"default": {"mcp": {"version": "==1.20.0"}}, '
                '"develop": {"opentelemetry-api": {"version": "==1.37.0"}}}'
            ),
        },
    )
    assert_that(_affected_pins(tmp_path, "GHSA-84m7-p3x7-pcfv")).is_equal_to(
        [
            "pdm/pdm.lock: mcp@2.0.0",
            "pipenv/Pipfile.lock: mcp@1.20.0",
            "poetry/poetry.lock: mcp@1.29.0",
        ],
    )
    assert_that(_affected_pins(tmp_path, "GHSA-xcq7-p3pf-p88h")).is_equal_to(
        ["pipenv/Pipfile.lock: opentelemetry-api@1.37.0"],
    )


@pytest.mark.parametrize(
    ("advisory", "version", "affected"),
    [
        ("GHSA-84m7-p3x7-pcfv", "1.7.0", False),
        ("GHSA-84m7-p3x7-pcfv", "1.8.0", True),
        ("GHSA-5h93-6whr-6q8j", "1.29.0", True),
        ("GHSA-rwrf-2pqf-9j8j", "1.9.0", False),
        ("GHSA-rwrf-2pqf-9j8j", "1.10.0", True),
        ("GHSA-w4fh-qvv9-3v23", "1.0.0", True),
        ("GHSA-w4fh-qvv9-3v23", "1.30.0", False),
        ("GHSA-w4fh-qvv9-3v23", "2.0.0", True),
        ("GHSA-rwrf-2pqf-9j8j", "2.2.0", False),
    ],
)
def test_mcp_ranges_follow_the_advisory_data(
    tmp_path: Path,
    advisory: str,
    version: str,
    affected: bool,
) -> None:
    """Each mcp advisory flags only the versions its advisory lists.

    Args:
        tmp_path: Scratch repository root.
        advisory: The OSV id of an mcp exception.
        version: The mcp version pinned in the scratch lockfile.
        affected: Whether the advisory affects that version.
    """
    _write_files(tmp_path, {"requirements.txt": f"mcp=={version}\n"})
    expected = [f"requirements.txt: mcp@{version}"] if affected else []
    assert_that(_affected_pins(tmp_path, advisory)).is_equal_to(expected)


@pytest.mark.parametrize("advisory", _semgrep_only_ids())
def test_semgrep_telemetry_ignore_cannot_hide_vulnerable_application_lock(
    advisory: str,
) -> None:
    """A Semgrep-only OSV ignore must not hide any other Python lockfile.

    OSV ignores apply to every lockfile osv-scanner reads, so each advisory
    confined to ``requirements-semgrep.txt`` (the opentelemetry-api telemetry
    and mcp entries) must not affect a pin in any other Python lockfile
    osv-scanner reads: ``uv.lock``, ``*requirements*.txt`` (and the files
    they include), ``pylock*.toml``, ``poetry.lock``, ``pdm.lock`` or
    ``Pipfile.lock``.

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
