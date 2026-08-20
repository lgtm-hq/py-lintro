"""Tests for the release Version-PR artifact orchestrator."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from assertpy import assert_that

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _REPO_ROOT / "scripts" / "release" / "prepare_version_artifacts.py"


def _load_orchestrator() -> ModuleType:
    """Load ``prepare_version_artifacts`` as a module.

    Returns:
        Loaded orchestrator module.
    """
    spec = importlib.util.spec_from_file_location(
        "prepare_version_artifacts",
        _SCRIPT,
    )
    assert_that(spec).is_not_none()
    assert_that(getattr(spec, "loader", None)).is_not_none()
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


class _FakeStep:
    """Record ``main()`` invocations and return a configured exit code."""

    def __init__(self, *, name: str, calls: list[str], rc: int = 0) -> None:
        """Store identity, the shared call log, and the exit code to return.

        Args:
            name: Step name appended to ``calls`` when ``main`` runs.
            calls: Shared ordered log of invoked step names.
            rc: Exit code returned by ``main``.
        """
        self._name = name
        self._calls = calls
        self._rc = rc

    def main(self, argv: list[str]) -> int:
        """Record this step and return the configured exit code.

        Args:
            argv: Unused argv forwarded by the orchestrator.

        Returns:
            Configured exit code.
        """
        del argv
        self._calls.append(self._name)
        return self._rc


def test_prepare_version_artifacts_runs_security_spdx_then_pin_sync(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Version-PR hook stamps CHANGELOG, SECURITY.md, SPDX, then pin sync.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    orchestrator = _load_orchestrator()
    calls: list[str] = []
    fakes = {
        "format_changelog": _FakeStep(name="changelog", calls=calls),
        "update_security_support": _FakeStep(name="security", calls=calls),
        "generate_spdx_data": _FakeStep(name="spdx", calls=calls),
        "sync_pinned_release_image": _FakeStep(name="pin_sync", calls=calls),
    }

    def _load(*, name: str, path: Path) -> ModuleType:
        del path
        return fakes[name]  # type: ignore[return-value]

    monkeypatch.setattr(orchestrator, "_load_module", _load)
    assert_that(orchestrator.main()).is_equal_to(0)
    assert_that(calls).is_equal_to(
        ["changelog", "security", "spdx", "pin_sync"],
    )


def test_prepare_version_artifacts_stops_before_pin_sync_on_fatal_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-zero SPDX refresh must not run the non-fatal pin sync.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    orchestrator = _load_orchestrator()
    calls: list[str] = []
    fakes = {
        "format_changelog": _FakeStep(name="changelog", calls=calls),
        "update_security_support": _FakeStep(name="security", calls=calls),
        "generate_spdx_data": _FakeStep(name="spdx", calls=calls, rc=1),
        "sync_pinned_release_image": _FakeStep(name="pin_sync", calls=calls),
    }

    def _load(*, name: str, path: Path) -> ModuleType:
        del path
        return fakes[name]  # type: ignore[return-value]

    monkeypatch.setattr(orchestrator, "_load_module", _load)
    assert_that(orchestrator.main()).is_equal_to(1)
    assert_that(calls).is_equal_to(["changelog", "security", "spdx"])


def test_prepare_version_artifacts_returns_2_when_a_script_cannot_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing composed script fails the hook with exit 2.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    orchestrator = _load_orchestrator()

    def _load(*, name: str, path: Path) -> ModuleType:
        raise RuntimeError(f"Could not load {path} ({name})")

    monkeypatch.setattr(orchestrator, "_load_module", _load)
    assert_that(orchestrator.main()).is_equal_to(2)


def test_prepare_version_artifacts_composes_expected_scripts() -> None:
    """The hook loads changelog, SECURITY.md, SPDX, and pin-sync scripts."""
    orchestrator = _load_orchestrator()
    source = _SCRIPT.read_text(encoding="utf-8")
    assert_that(orchestrator.REPO_ROOT).is_equal_to(_REPO_ROOT)
    for relative in (
        Path("scripts") / "ci" / "format-changelog.py",
        Path("scripts") / "ci" / "update-security-support.py",
        Path("scripts") / "release" / "generate_spdx_data.py",
        Path("scripts") / "ci" / "sync-pinned-release-image.py",
    ):
        assert_that((_REPO_ROOT / relative).is_file()).is_true()
        assert_that(source).contains(relative.name)
