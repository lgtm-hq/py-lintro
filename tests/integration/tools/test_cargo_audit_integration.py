"""Integration tests for cargo-audit across several lockfiles (#3549).

These run the real ``cargo audit`` and need its advisory database, which
cargo-audit fetches on first use. They reproduce the issue: two separate
crates, where only the second pins a crate with an advisory.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.tools.cargo_audit.definition import CargoAuditPlugin
from tests.integration._tools import require_tool

pytestmark = require_tool(
    "cargo",
    version_args=("audit", "--version"),
    label="cargo-audit",
    pin="cargo_audit",
)

# smallvec 1.6.0 carries RUSTSEC-2021-0003 (fixed in 1.6.1).
VULNERABLE_LOCK = """version = 3

[[package]]
name = "smallvec"
version = "1.6.0"
source = "registry+https://github.com/rust-lang/crates.io-index"

[[package]]
name = "{name}"
version = "0.1.0"
dependencies = [
 "smallvec",
]
"""
CLEAN_LOCK = """version = 3

[[package]]
name = "{name}"
version = "0.1.0"
"""


def _crate(root: Path, rel: str, *, vulnerable: bool) -> Path:
    """Write a crate with a Cargo.toml next to its Cargo.lock.

    Args:
        root: Repository root.
        rel: Crate directory relative to ``root``.
        vulnerable: Whether the lockfile pins smallvec 1.6.0.

    Returns:
        The resolved Cargo.lock path.
    """
    crate = root / rel
    crate.mkdir(parents=True)
    name = crate.name
    (crate / "Cargo.toml").write_text(
        f'[package]\nname = "{name}"\nversion = "0.1.0"\nedition = "2021"\n',
    )
    lock = crate / "Cargo.lock"
    lock.write_text((VULNERABLE_LOCK if vulnerable else CLEAN_LOCK).format(name=name))
    return lock.resolve()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a repository root and make it the process directory.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Used to change the working directory.

    Returns:
        The repository root, resolved.
    """
    root = (tmp_path / "repo").resolve()
    (root / ".git").mkdir(parents=True)
    monkeypatch.chdir(root)
    return root


def test_second_crate_advisory_is_reported_against_its_lockfile(repo: Path) -> None:
    """The advisory in the second crate is found and names that lockfile.

    Args:
        repo: Repository root.
    """
    _crate(repo, "first", vulnerable=False)
    second = _crate(repo, "second", vulnerable=True)

    result = CargoAuditPlugin().check(["."], {})

    assert_that(result.execution_failed).described_as(result.output).is_false()
    assert_that(result.success).is_false()
    assert_that([(i.file, i.get_code()) for i in result.issues or []]).is_equal_to(
        [(str(second), "RUSTSEC-2021-0003")],
    )


def test_excluded_vendored_lockfile_is_named(repo: Path) -> None:
    """A vendored copy left out by an exclude pattern is named, not audited.

    Args:
        repo: Repository root.
    """
    _crate(repo, "app", vulnerable=False)
    _crate(repo, "vendor/dep", vulnerable=True)
    plugin = CargoAuditPlugin()
    plugin.set_options(exclude_patterns=["vendor/"])

    result = plugin.check(["."], {})

    assert_that(result.execution_failed).described_as(result.output).is_false()
    assert_that(result.success).is_true()
    assert_that(result.issues or []).is_empty()
    assert_that(result.output).contains(
        "excluded by lintro's exclude patterns (1): vendor/dep/Cargo.lock.",
    )


def test_empty_lockfile_is_not_audited(repo: Path) -> None:
    """cargo-audit passes a zero-byte lockfile as 0 dependencies; lintro fails it.

    Args:
        repo: Repository root.
    """
    _crate(repo, "app", vulnerable=False)
    (repo / "app" / "Cargo.lock").write_text("")

    result = CargoAuditPlugin().check(["."], {})

    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.output).contains("app/Cargo.lock (lockfile lists no packages)")


def test_denied_warning_fails_an_audit_that_ran(repo: Path) -> None:
    """``[output] deny`` makes cargo-audit exit 1 with no vulnerabilities.

    ansi_term 0.12.1 is unmaintained (RUSTSEC-2021-0139). The audit ran, so
    the lockfile fails as denied, not as a missing report.

    Args:
        repo: Repository root.
    """
    crate = repo / "app"
    crate.mkdir()
    (crate / "Cargo.toml").write_text('[package]\nname = "app"\nversion = "0.1.0"\n')
    (crate / "Cargo.lock").write_text(
        'version = 3\n\n[[package]]\nname = "ansi_term"\nversion = "0.12.1"\n'
        'source = "registry+https://github.com/rust-lang/crates.io-index"\n\n'
        '[[package]]\nname = "app"\nversion = "0.1.0"\ndependencies = ["ansi_term"]\n',
    )
    (crate / ".cargo").mkdir()
    (crate / ".cargo" / "audit.toml").write_text(
        '[output]\ndeny = ["unmaintained"]\nquiet = false\n',
    )

    result = CargoAuditPlugin().check(["."], {})

    assert_that(result.success).described_as(result.output).is_false()
    assert_that(result.execution_failed).is_false()
    assert_that(result.inspected_files).is_true()
    assert_that(result.output).contains(
        "app/Cargo.lock (unmaintained ansi_term (RUSTSEC-2021-0139))",
    )


@pytest.mark.parametrize("include", [False, True], ids=["default", "included"])
def test_vendored_copy_is_skipped_unless_included(repo: Path, include: bool) -> None:
    """A vulnerable vendored lockfile is named, not audited, unless included.

    Args:
        repo: Repository root.
        include: Whether include_vendored is set.
    """
    _crate(repo, "app", vulnerable=False)
    dep = _crate(repo, "vendor/dep", vulnerable=True)
    plugin = CargoAuditPlugin()
    plugin.set_options(include_vendored=include)

    result = plugin.check(["."], {})

    assert_that(result.execution_failed).described_as(result.output).is_false()
    assert_that([i.file for i in result.issues or []]).is_equal_to(
        [str(dep)] if include else [],
    )
    assert_that(result.success).is_equal_to(not include)
    if not include:
        assert_that(result.output).contains(
            "copies under a vendor/ or target/ directory (1): vendor/dep/Cargo.lock;",
        )
