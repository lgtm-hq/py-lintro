"""Tests for scripts/ci/sync-release-docs.py."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from assertpy import assert_that

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "ci" / "sync-release-docs.py"


def _load_module() -> ModuleType:
    """Load sync-release-docs.py as an importable test module.

    Returns:
        ModuleType: The loaded module.

    Raises:
        RuntimeError: If the module spec or loader cannot be resolved.
    """
    spec = importlib.util.spec_from_file_location("sync_release_docs", _SCRIPT_PATH)
    if spec is None or spec.loader is None:
        msg = f"Unable to load module from {_SCRIPT_PATH}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def module() -> ModuleType:
    """Provide the loaded sync-release-docs module.

    Returns:
        ModuleType: The loaded module.
    """
    return _load_module()


def test_update_pre_commit_rev_pins(module: ModuleType) -> None:
    """Pre-commit examples replace every rev pin with the release tag."""
    src = (
        "repos:\n"
        "  - repo: https://github.com/lgtm-hq/py-lintro\n"
        "    rev: v0.69.0\n"
        "    hooks:\n"
        "      - id: lintro-check\n"
        "  - repo: https://github.com/lgtm-hq/py-lintro\n"
        "    rev: v0.70.1 # pin to a released tag\n"
        "    hooks:\n"
        "      - id: lintro-format\n"
    )
    result = module.update_pre_commit_rev_pins(src, version="0.79.1")

    assert_that(result).contains("rev: v0.79.1\n")
    assert_that(result).contains("rev: v0.79.1 # pin to a released tag")
    assert_that(result).does_not_contain("v0.69.0")
    assert_that(result).does_not_contain("v0.70.1")


def test_update_pre_commit_rev_pins_is_idempotent(module: ModuleType) -> None:
    """Re-running against already-synced text leaves it byte-for-byte equal."""
    src = "    rev: v1.2.3\n"

    assert_that(module.update_pre_commit_rev_pins(src, version="1.2.3")).is_equal_to(
        src,
    )


def test_resolve_version_prefers_next_version_env(module: ModuleType) -> None:
    """Release hook reads NEXT_VERSION when present."""
    assert_that(
        module.resolve_version(env={"NEXT_VERSION": "1.2.3"}),
    ).is_equal_to("1.2.3")


def test_resolve_version_strips_leading_v(module: ModuleType) -> None:
    """NEXT_VERSION may include a leading v prefix."""
    assert_that(
        module.resolve_version(env={"NEXT_VERSION": "v2.0.0"}),
    ).is_equal_to("2.0.0")


def test_resolve_version_falls_back_to_pyproject(module: ModuleType) -> None:
    """An empty NEXT_VERSION falls back to the pyproject version."""
    assert_that(module.resolve_version(env={"NEXT_VERSION": "  "})).is_equal_to(
        module._read_pyproject_version(),
    )


def test_sync_release_docs_updates_pre_commit_doc(
    tmp_path: Path,
    module: ModuleType,
) -> None:
    """sync_release_docs rewrites docs/pre-commit.md under repo_root."""
    pre_commit = tmp_path / "docs" / "pre-commit.md"
    pre_commit.parent.mkdir(parents=True)
    pre_commit.write_text("    rev: v0.69.0\n", encoding="utf-8")

    changed = module.sync_release_docs(version="0.80.0", repo_root=tmp_path)

    assert_that(changed).is_length(1)
    assert_that(pre_commit.read_text(encoding="utf-8")).contains("rev: v0.80.0")


def test_sync_release_docs_is_a_noop_when_already_synced(
    tmp_path: Path,
    module: ModuleType,
) -> None:
    """No file is rewritten when the pins already match the release version."""
    pre_commit = tmp_path / "docs" / "pre-commit.md"
    pre_commit.parent.mkdir(parents=True)
    pre_commit.write_text("    rev: v0.80.0\n", encoding="utf-8")

    changed = module.sync_release_docs(version="0.80.0", repo_root=tmp_path)

    assert_that(changed).is_empty()


def test_sync_release_docs_tolerates_missing_doc(
    tmp_path: Path,
    module: ModuleType,
) -> None:
    """A missing target doc warns instead of raising."""
    assert_that(
        module.sync_release_docs(version="0.80.0", repo_root=tmp_path),
    ).is_empty()
