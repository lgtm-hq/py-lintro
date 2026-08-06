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


def test_supported_release_line(module: ModuleType) -> None:
    """Supported table rows use major.minor.x labels."""
    assert_that(module.supported_release_line(major=0, minor=78)).is_equal_to("0.78.x")


def test_update_supported_version_table(module: ModuleType) -> None:
    """SECURITY tables get supported and minimum version rows replaced."""
    src = (
        "| Version | Supported |\n"
        "| ------- | --------- |\n"
        "| 0.64.x  | ✅        |\n"
        "| < 0.64  | ❌        |\n"
    )
    result = module.update_supported_version_table(src, major=0, minor=78)

    assert_that(result).contains("| 0.78.x  | ✅        |")
    assert_that(result).contains("| < 0.78  | ❌        |")
    assert_that(result).does_not_contain("0.64.x")


def test_update_pre_commit_rev_pins(module: ModuleType) -> None:
    """Pre-commit examples replace every rev pin with the release tag."""
    src = (
        "repos:\n"
        "  - repo: https://github.com/lgtm-hq/py-lintro\n"
        "    rev: v0.69.0\n"
        "    hooks:\n"
        "      - id: lintro-check\n"
    )
    result = module.update_pre_commit_rev_pins(src, version="0.79.1")

    assert_that(result).contains("rev: v0.79.1")
    assert_that(result).does_not_contain("v0.69.0")


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


def test_sync_release_docs_updates_files(tmp_path: Path, module: ModuleType) -> None:
    """sync_release_docs rewrites SECURITY and pre-commit docs under repo_root."""
    security = tmp_path / "SECURITY.md"
    security.write_text(
        "| 0.64.x  | ✅        |\n| < 0.64  | ❌        |\n",
        encoding="utf-8",
    )
    github_security = tmp_path / ".github" / "SECURITY.md"
    github_security.parent.mkdir(parents=True)
    github_security.write_text(
        "| 0.64.x  | :white_check_mark: |\n| < 0.64  | :x:                |\n",
        encoding="utf-8",
    )
    pre_commit = tmp_path / "docs" / "pre-commit.md"
    pre_commit.parent.mkdir(parents=True)
    pre_commit.write_text("rev: v0.69.0\n", encoding="utf-8")

    changed = module.sync_release_docs(version="0.80.0", repo_root=tmp_path)

    assert_that(changed).is_length(3)
    assert_that(security.read_text(encoding="utf-8")).contains("0.80.x")
    assert_that(github_security.read_text(encoding="utf-8")).contains("0.80.x")
    assert_that(pre_commit.read_text(encoding="utf-8")).contains("rev: v0.80.0")
