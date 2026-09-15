"""Tests for the npm version-sync script.

Covers version discovery, in-repo internal consistency, and the write/check
round-trip against a temporary ``npm/`` tree.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest
from assertpy import assert_that

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "ci" / "npm" / "sync_npm_version.py"


def _load_module() -> ModuleType:
    """Import sync_npm_version without executing its CLI.

    Returns:
        The loaded module.
    """
    spec = importlib.util.spec_from_file_location("sync_npm_version", _SCRIPT)
    assert_that(spec).is_not_none()
    assert spec is not None  # narrow type for mypy
    assert_that(spec.loader).is_not_none()
    assert spec.loader is not None  # narrow type for mypy
    module = importlib.util.module_from_spec(spec)
    sys.modules["sync_npm_version"] = module
    spec.loader.exec_module(module)
    return module


def _write_manifest(path: Path, data: dict[str, object]) -> None:
    """Write a package.json fixture.

    Args:
        path: Destination manifest path.
        data: JSON-serialisable manifest content.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _make_npm_tree(root: Path, version: str) -> Path:
    """Create a minimal npm/ tree with meta + platform manifests.

    Args:
        root: Temporary base directory.
        version: Version string to seed every manifest with.

    Returns:
        The created ``npm/`` directory.
    """
    npm_dir = root / "npm"
    mod = _load_module()
    _write_manifest(
        npm_dir / mod.META_PACKAGE / "package.json",
        {
            "name": "lintro",
            "version": version,
            "optionalDependencies": {
                f"@lgtm-hq/lintro-{plat}": version for plat in mod.PLATFORM_PACKAGES
            },
        },
    )
    for plat in mod.PLATFORM_PACKAGES:
        _write_manifest(
            npm_dir / plat / "package.json",
            {"name": f"@lgtm-hq/lintro-{plat}", "version": version},
        )
    return npm_dir


# ---------------------------------------------------------------------------
# Version discovery
# ---------------------------------------------------------------------------


def test_read_project_version_matches_init() -> None:
    """read_project_version returns the __version__ from lintro/__init__.py."""
    mod = _load_module()
    init_text = (_REPO_ROOT / "lintro" / "__init__.py").read_text(encoding="utf-8")
    assert_that(init_text).contains(f'__version__ = "{mod.read_project_version()}"')


def test_normalise_version_strips_leading_v() -> None:
    """A leading 'v' is stripped from tag-style versions."""
    mod = _load_module()
    assert_that(mod._normalise_version("v1.2.3")).is_equal_to("1.2.3")
    assert_that(mod._normalise_version("1.2.3")).is_equal_to("1.2.3")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # The checkpoint forms the validation channels can publish (#2633):
        # npm rejects the bare PEP 440 suffix, SemVer wants a dotted one.
        ("v0.160.3rc1", "0.160.3-rc.1"),
        ("0.160.3rc4", "0.160.3-rc.4"),
        ("v1.2.3a1", "1.2.3-alpha.1"),
        ("v1.2.3b12", "1.2.3-beta.12"),
        # Stable stays as is; PEP 440 accepts the SemVer-looking rc spelling
        # and it maps to the same identifier.
        ("v1.2.3", "1.2.3"),
        ("1.2.3-rc.1", "1.2.3-rc.1"),
        # Valid but non-canonical PEP 440 spellings map like their canonical
        # form (Codex delta review on #2672): parsed, never string-matched.
        ("1.2.3RC1", "1.2.3-rc.1"),
        ("1.2.3-rc1", "1.2.3-rc.1"),
        ("1.2.3.RC1", "1.2.3-rc.1"),
        ("v1.2.3.Alpha.2", "1.2.3-alpha.2"),
        ("1.2.3B3", "1.2.3-beta.3"),
        ("1.2.3c1", "1.2.3-rc.1"),
        ("1.2.3pre2", "1.2.3-rc.2"),
        ("1.2.3preview3", "1.2.3-rc.3"),
        ("1.2.3rc", "1.2.3-rc.0"),
        (" v1.2.3rc1 ", "1.2.3-rc.1"),
    ],
)
def test_normalise_version_maps_pep440_prereleases_to_semver(
    raw: str,
    expected: str,
) -> None:
    """A PEP 440 rcN/aN/bN tag becomes the SemVer prerelease npm accepts."""
    mod = _load_module()
    assert_that(mod._normalise_version(raw)).is_equal_to(expected)


@pytest.mark.parametrize(
    "raw",
    [
        "v1.2.3.post1",
        "1.2.3post1",
        "v1.2.3.dev1",
        "1.2.3dev2",
        "v1.2.3rc1.post1",
        "v1.2.3rc1.dev3",
        # Non-canonical spellings reject identically to their canonical form.
        "1.2.3-post1",
        "1.2.3_POST1",
        "1.2.3DEV1",
        "1.2.3-r1",
    ],
)
def test_normalise_version_rejects_post_and_dev_releases(raw: str) -> None:
    """post/dev releases have no npm form and are refused, not passed through."""
    mod = _load_module()
    with pytest.raises(ValueError, match="post- or dev-release") as excinfo:
        mod._normalise_version(raw)
    assert_that(str(excinfo.value)).contains(raw)


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ("1!1.2.3", "local version or epoch"),
        ("1.2.3+local", "local version or epoch"),
        ("1.2.3+build.1", "local version or epoch"),
        ("1.2", "three-part"),
        ("1.2.3.4", "three-part"),
        ("garbage", "non-PEP 440"),
        ("1.2.3-beta.2+x", "local version or epoch"),
    ],
)
def test_normalise_version_rejects_epochs_locals_and_unparseable(
    raw: str,
    reason: str,
) -> None:
    """Epochs, local segments, wrong-width cores and junk are refused."""
    mod = _load_module()
    with pytest.raises(ValueError, match=reason):
        mod._normalise_version(raw)


def test_script_stays_stdlib_only() -> None:
    """The publish-npm stage job runs this script on a bare interpreter."""
    stdlib = set(sys.stdlib_module_names)
    imported = {
        line.split()[1].split(".")[0]
        for line in _SCRIPT.read_text(encoding="utf-8").splitlines()
        if line.startswith(("import ", "from "))
    }
    assert_that(imported - stdlib - {"__future__"}).is_empty()


def test_main_fails_cleanly_on_a_post_release(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The CLI exits 1 with the error on stderr instead of writing manifests."""
    mod = _load_module()
    assert_that(mod.main(["--check", "--version", "v1.2.3.post1"])).is_equal_to(1)
    assert_that(capsys.readouterr().err).contains("post- or dev-release")


# ---------------------------------------------------------------------------
# In-repo manifests
# ---------------------------------------------------------------------------


def test_repo_manifests_are_internally_consistent() -> None:
    """Every shipped npm manifest agrees with the meta-package version."""
    mod = _load_module()
    meta_version = mod.read_meta_version()
    mismatches = mod.check_versions(meta_version)
    assert_that(mismatches).is_empty()


def test_all_manifest_paths_covers_four_packages() -> None:
    """The tree exposes exactly the meta package plus three platforms."""
    mod = _load_module()
    paths = mod.all_manifest_paths()
    assert_that(paths).is_length(4)


# ---------------------------------------------------------------------------
# check / sync round-trip
# ---------------------------------------------------------------------------


def test_check_versions_detects_drift(tmp_path: Path) -> None:
    """A platform manifest that lags the meta version is reported."""
    mod = _load_module()
    npm_dir = _make_npm_tree(tmp_path, "1.0.0")
    drifting = npm_dir / "linux-x64" / "package.json"
    data = json.loads(drifting.read_text(encoding="utf-8"))
    data["version"] = "0.9.0"
    drifting.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    mismatches = mod.check_versions("1.0.0", npm_dir=npm_dir)

    assert_that(mismatches).is_length(1)
    assert_that(mismatches[0].manifest).contains("linux-x64")
    assert_that(mismatches[0].found).is_equal_to("0.9.0")


def test_sync_versions_updates_all_fields(tmp_path: Path) -> None:
    """sync_versions rewrites top-level and optionalDependency versions."""
    mod = _load_module()
    npm_dir = _make_npm_tree(tmp_path, "0.0.0-dev")

    changed = mod.sync_versions("2.3.4", npm_dir=npm_dir)

    assert_that(changed).is_length(4)
    assert_that(mod.check_versions("2.3.4", npm_dir=npm_dir)).is_empty()

    meta = json.loads(
        (npm_dir / "lintro" / "package.json").read_text(encoding="utf-8"),
    )
    assert_that(meta["version"]).is_equal_to("2.3.4")
    for pin in meta["optionalDependencies"].values():
        assert_that(pin).is_equal_to("2.3.4")


def test_sync_versions_is_idempotent(tmp_path: Path) -> None:
    """Re-running sync at the same version reports no further changes."""
    mod = _load_module()
    npm_dir = _make_npm_tree(tmp_path, "0.0.0-dev")

    mod.sync_versions("2.3.4", npm_dir=npm_dir)
    second = mod.sync_versions("2.3.4", npm_dir=npm_dir)

    assert_that(second).is_empty()
