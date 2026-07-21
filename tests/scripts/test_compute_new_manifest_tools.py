"""Tests for the newly-added-manifest-tool computation (#1565).

Covers the pure JSON name-diff helper (``compute-new-manifest-tools.py``) and
the git-resolution shell wrapper (``compute-new-manifest-tools.sh``), which
feeds the ``--allow-missing`` allowlist to the manifest-vs-image gate. The
wrapper is exercised against a real temporary git repository so the merge-base
and fail-closed paths are covered end to end.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess  # nosec B404 - drives the scripts under test with shell=False
from pathlib import Path
from types import ModuleType

from assertpy import assert_that

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PY_SCRIPT = (_REPO_ROOT / "scripts/ci/compute-new-manifest-tools.py").resolve()
_SH_SCRIPT = (_REPO_ROOT / "scripts/ci/compute-new-manifest-tools.sh").resolve()


def _load_module() -> ModuleType:
    """Load compute-new-manifest-tools.py as an importable module.

    Returns:
        ModuleType: The loaded module.

    Raises:
        RuntimeError: When the module spec cannot be created.
    """
    spec = importlib.util.spec_from_file_location(
        "compute_new_manifest_tools",
        _PY_SCRIPT,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load compute-new-manifest-tools.py module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _manifest(names: list[str]) -> str:
    """Render a manifest JSON string declaring the given tool names.

    Args:
        names: Tool names to include.

    Returns:
        str: The manifest JSON.
    """
    return json.dumps(
        {"version": 2, "tools": [{"name": n, "version": "1.0.0"} for n in names]},
    )


def test_tool_names_missing_file_is_empty() -> None:
    """A non-existent manifest path yields an empty name set (new-manifest case)."""
    module = _load_module()
    names = module._tool_names(Path("/definitely/not/here.json"))  # noqa: SLF001
    assert_that(names).is_equal_to(set())


def test_tool_names_reads_declared_names(tmp_path: Path) -> None:
    """Declared tool names are extracted, empty/blank names dropped."""
    module = _load_module()
    manifest = tmp_path / "m.json"
    manifest.write_text(
        json.dumps(
            {
                "version": 2,
                "tools": [{"name": "ruff"}, {"name": " "}, {"name": "black"}],
            },
        ),
    )
    names = module._tool_names(manifest)  # noqa: SLF001
    assert_that(names).is_equal_to({"ruff", "black"})


def _run_py(old: Path, new: Path) -> subprocess.CompletedProcess[str]:
    """Run the python diff helper.

    Args:
        old: Old manifest path.
        new: New manifest path.

    Returns:
        subprocess.CompletedProcess[str]: The completed run.
    """
    return subprocess.run(  # nosec B603 B607 - fixed argv, shell=False, controlled test
        ["python3", str(_PY_SCRIPT), "--old", str(old), "--new", str(new)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_py_prints_added_names_sorted(tmp_path: Path) -> None:
    """Added tool names print comma-separated and sorted."""
    old = tmp_path / "old.json"
    new = tmp_path / "new.json"
    old.write_text(_manifest(["ruff"]))
    new.write_text(_manifest(["ruff", "terraform", "ansible"]))
    result = _run_py(old, new)
    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout.strip()).is_equal_to("ansible,terraform")


def test_py_no_additions_prints_empty(tmp_path: Path) -> None:
    """No added tools prints an empty line and exits 0."""
    old = tmp_path / "old.json"
    new = tmp_path / "new.json"
    old.write_text(_manifest(["ruff", "black"]))
    new.write_text(_manifest(["ruff"]))
    result = _run_py(old, new)
    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout.strip()).is_equal_to("")


def test_py_invalid_new_manifest_exits_two(tmp_path: Path) -> None:
    """A malformed current manifest exits 2 (caller fails closed)."""
    old = tmp_path / "old.json"
    new = tmp_path / "new.json"
    old.write_text(_manifest(["ruff"]))
    new.write_text("not json")
    result = _run_py(old, new)
    assert_that(result.returncode).is_equal_to(2)


def _git(repo: Path, *args: str) -> None:
    """Run a git command inside a test repository.

    Args:
        repo: Repository working directory.
        *args: Git arguments (without the leading ``git``).
    """
    subprocess.run(  # nosec B603 B607 - fixed argv against a real binary; shell=False
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
        },
    )


def _write_manifest_file(repo: Path, names: list[str]) -> None:
    """Write the repo manifest declaring the given tool names.

    Args:
        repo: Repository root.
        names: Tool names to declare.
    """
    manifest = repo / "lintro" / "tools" / "manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(_manifest(names))


def _run_sh(
    repo: Path,
    *,
    base_ref: str,
) -> subprocess.CompletedProcess[str]:
    """Run the shell wrapper in a repo with a given BASE_REF.

    Args:
        repo: Repository to run in (cwd).
        base_ref: BASE_REF value.

    Returns:
        subprocess.CompletedProcess[str]: The completed run.
    """
    return subprocess.run(  # nosec B603 - fixed argv against a real binary; shell=False
        [str(_SH_SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
        cwd=repo,
        env={
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", "/tmp"),  # nosec B108 - test fallback
            "BASE_REF": base_ref,
        },
    )


def _init_repo_with_base(tmp_path: Path) -> Path:
    """Create a repo on main with a two-tool manifest committed.

    Args:
        tmp_path: Pytest temporary directory.

    Returns:
        Path: The repository root, checked out on a feature branch.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--initial-branch=main")
    _write_manifest_file(repo, ["ruff", "black"])
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base manifest")
    _git(repo, "checkout", "-b", "feature")
    return repo


def test_sh_empty_base_ref_is_empty(tmp_path: Path) -> None:
    """No BASE_REF (main / nightly) yields an empty allowlist on stdout."""
    repo = _init_repo_with_base(tmp_path)
    result = _run_sh(repo, base_ref="")
    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout.strip()).is_equal_to("")


def test_sh_added_tool_is_reported(tmp_path: Path) -> None:
    """A tool added on the branch is reported as newly-added vs the base."""
    repo = _init_repo_with_base(tmp_path)
    _write_manifest_file(repo, ["ruff", "black", "terraform"])
    _git(repo, "commit", "-am", "add terraform")
    result = _run_sh(repo, base_ref="main")
    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout.strip()).is_equal_to("terraform")


def test_sh_no_manifest_change_is_empty(tmp_path: Path) -> None:
    """A branch that does not touch the manifest reports no added tools."""
    repo = _init_repo_with_base(tmp_path)
    (repo / "README.md").write_text("docs\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "docs only")
    result = _run_sh(repo, base_ref="main")
    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout.strip()).is_equal_to("")


def test_sh_new_manifest_treats_all_as_added(tmp_path: Path) -> None:
    """No manifest at the merge-base reports every current tool as added.

    Regression test for #1566: ``git show`` failing (no manifest blob at the
    merge-base) must make the Python helper see a *non-existent* old-manifest
    path, not an existing-but-empty one -- an empty file fails JSON parsing
    and flips the result to fail-closed (empty allowlist), the opposite of
    the documented "brand-new manifest" intent.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--initial-branch=main")
    (repo / "README.md").write_text("docs\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base without a manifest")
    _git(repo, "checkout", "-b", "feature")
    _write_manifest_file(repo, ["ruff", "terraform"])
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "introduce manifest")
    result = _run_sh(repo, base_ref="main")
    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout.strip()).is_equal_to("ruff,terraform")


def test_sh_unresolvable_base_fails_closed(tmp_path: Path) -> None:
    """An unresolvable base ref fails closed: empty allowlist, exit 0."""
    repo = _init_repo_with_base(tmp_path)
    result = _run_sh(repo, base_ref="does-not-exist")
    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout.strip()).is_equal_to("")


def test_sh_help_exits_zero() -> None:
    """The wrapper prints help and exits 0."""
    result = (
        subprocess.run(  # nosec B603 - fixed argv against a real binary; shell=False
            [str(_SH_SCRIPT), "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
    )
    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("Usage:")
    assert_that(result.stdout).contains("BASE_REF")


def _manifest_versions(entries: list[tuple[str, str]]) -> str:
    """Render a manifest JSON string with explicit name/version pairs.

    Args:
        entries: (name, version) pairs to declare.

    Returns:
        str: The manifest JSON.
    """
    return json.dumps(
        {
            "version": 2,
            "tools": [{"name": n, "version": v} for n, v in entries],
        },
    )


def _run_py_emit(
    old: Path,
    new: Path,
    *,
    emit: str,
) -> subprocess.CompletedProcess[str]:
    """Run the python diff helper with an explicit --emit mode.

    Args:
        old: Old manifest path.
        new: New manifest path.
        emit: ``added`` or ``version-changed``.

    Returns:
        subprocess.CompletedProcess[str]: The completed run.
    """
    return subprocess.run(  # nosec B603 B607 - fixed argv, shell=False, controlled test
        [
            "python3",
            str(_PY_SCRIPT),
            "--old",
            str(old),
            "--new",
            str(new),
            "--emit",
            emit,
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_py_version_changed_reports_bumped_tools(tmp_path: Path) -> None:
    """Version-changed emit lists tools whose version string differs."""
    old = tmp_path / "old.json"
    new = tmp_path / "new.json"
    old.write_text(_manifest_versions([("ruff", "0.9.0"), ("astro_check", "7.0.9")]))
    new.write_text(_manifest_versions([("ruff", "0.9.0"), ("astro_check", "7.1.3")]))
    result = _run_py_emit(old, new, emit="version-changed")
    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout.strip()).is_equal_to("astro_check")


def test_py_version_changed_ignores_newly_added(tmp_path: Path) -> None:
    """Newly-added tools are not reported as version-changed."""
    old = tmp_path / "old.json"
    new = tmp_path / "new.json"
    old.write_text(_manifest_versions([("ruff", "0.9.0")]))
    new.write_text(
        _manifest_versions([("ruff", "0.9.0"), ("terraform", "1.9.0")]),
    )
    result = _run_py_emit(old, new, emit="version-changed")
    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout.strip()).is_equal_to("")


def test_py_version_changed_no_change_is_empty(tmp_path: Path) -> None:
    """Identical versions print an empty version-changed set."""
    old = tmp_path / "old.json"
    new = tmp_path / "new.json"
    payload = _manifest_versions([("ruff", "0.9.0"), ("black", "25.1.0")])
    old.write_text(payload)
    new.write_text(payload)
    result = _run_py_emit(old, new, emit="version-changed")
    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout.strip()).is_equal_to("")


def test_version_tuple_stops_at_prerelease_tag() -> None:
    """A pre-release tag stops parsing so "7.1.0-rc.1" is (7, 1, 0)."""
    module = _load_module()
    version_tuple = module._version_tuple  # noqa: SLF001
    assert_that(version_tuple("7.1.0-rc.1")).is_equal_to((7, 1, 0))
    assert_that(version_tuple("7.1.3")).is_equal_to((7, 1, 3))
    # Pre-release tags collapse to their release base, so ordering is decided
    # by the numeric segments only.
    is_upward = module._is_upward_bump  # noqa: SLF001
    assert_that(is_upward("7.1.0-rc.1", "7.2.0")).is_true()
    assert_that(is_upward("7.2.0", "7.1.0-rc.1")).is_false()


def test_py_version_changed_excludes_downgrades(tmp_path: Path) -> None:
    """A downgrade must not enter the version-lag allowlist (fail closed).

    ``--allow-version-lag`` is for upward bumps only; a downgrade leaves the
    pinned image newer than the manifest, which the gate must hard-fail.
    """
    old = tmp_path / "old.json"
    new = tmp_path / "new.json"
    old.write_text(_manifest_versions([("ruff", "0.9.0"), ("astro_check", "7.1.3")]))
    new.write_text(_manifest_versions([("ruff", "0.9.0"), ("astro_check", "7.0.9")]))
    result = _run_py_emit(old, new, emit="version-changed")
    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout.strip()).is_equal_to("")


def test_py_version_changed_excludes_unparseable_change(tmp_path: Path) -> None:
    """A change to/from an unparseable version fails closed (excluded)."""
    old = tmp_path / "old.json"
    new = tmp_path / "new.json"
    old.write_text(_manifest_versions([("astro_check", "7.0.9")]))
    new.write_text(_manifest_versions([("astro_check", "latest")]))
    result = _run_py_emit(old, new, emit="version-changed")
    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout.strip()).is_equal_to("")


def test_py_version_changed_invalid_exits_two(tmp_path: Path) -> None:
    """Malformed manifests fail closed (exit 2) for version-changed too."""
    old = tmp_path / "old.json"
    new = tmp_path / "new.json"
    old.write_text(_manifest_versions([("ruff", "0.9.0")]))
    new.write_text("not json")
    result = _run_py_emit(old, new, emit="version-changed")
    assert_that(result.returncode).is_equal_to(2)


def _run_sh_emit(
    repo: Path,
    *,
    base_ref: str,
    emit: str,
) -> subprocess.CompletedProcess[str]:
    """Run the shell wrapper with an explicit EMIT mode.

    Args:
        repo: Repository to run in (cwd).
        base_ref: BASE_REF value.
        emit: ``added`` or ``version-changed``.

    Returns:
        subprocess.CompletedProcess[str]: The completed run.
    """
    return subprocess.run(  # nosec B603 - fixed argv against a real binary; shell=False
        [str(_SH_SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
        cwd=repo,
        env={
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", "/tmp"),  # nosec B108 - test fallback
            "BASE_REF": base_ref,
            "EMIT": emit,
        },
    )


def _write_manifest_versions(repo: Path, entries: list[tuple[str, str]]) -> None:
    """Write the repo manifest with explicit name/version pairs.

    Args:
        repo: Repository root.
        entries: (name, version) pairs.
    """
    manifest = repo / "lintro" / "tools" / "manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(_manifest_versions(entries))


def test_sh_version_changed_reports_bump(tmp_path: Path) -> None:
    """A version bump on the branch is reported under EMIT=version-changed."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--initial-branch=main")
    _write_manifest_versions(repo, [("ruff", "0.9.0"), ("astro_check", "7.0.9")])
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    _git(repo, "checkout", "-b", "feature")
    _write_manifest_versions(repo, [("ruff", "0.9.0"), ("astro_check", "7.1.3")])
    _git(repo, "commit", "-am", "bump astro")
    result = _run_sh_emit(repo, base_ref="main", emit="version-changed")
    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout.strip()).is_equal_to("astro_check")


def test_sh_version_changed_unresolvable_fails_closed(tmp_path: Path) -> None:
    """Unresolvable base ref fails closed for version-changed emit too."""
    repo = _init_repo_with_base(tmp_path)
    result = _run_sh_emit(repo, base_ref="does-not-exist", emit="version-changed")
    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout.strip()).is_equal_to("")
