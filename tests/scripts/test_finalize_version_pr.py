# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""Regressions for stale release candidates after an earlier gate passed (#3054)."""

from __future__ import annotations

import importlib.util
import subprocess  # nosec B404 - exceptions for simulated read-only git failures
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
LIVE_MAIN_SHA = "627cb4c06dc22f97643aaf871559bf8b49b65464"


@pytest.fixture
def module() -> ModuleType:
    """Load the tokenless version hook without running it.

    Returns:
        The imported hook module.
    """
    spec = importlib.util.spec_from_file_location(
        "finalize_version_pr",
        ROOT / "scripts/ci/finalize-version-pr.py",
    )
    assert spec is not None and spec.loader is not None
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def _live_git(
    monkeypatch: pytest.MonkeyPatch,
    module: ModuleType,
    *,
    version: str = "0.173.4",
    tagged: bool = False,
) -> None:
    """Model live main independently of the triggering checkout.

    Args:
        monkeypatch: Fixture for replacing Git reads.
        module: Loaded release hook.
        version: Project version already on live main.
        tagged: Whether the candidate tag already exists at origin.
    """

    def read(*, repo_root: Path, args: list[str]) -> str:
        assert repo_root == ROOT
        if args == [
            "ls-remote",
            "origin",
            "refs/heads/main",
            "refs/tags/v0.173.4",
        ]:
            refs = f"{LIVE_MAIN_SHA}\trefs/heads/main\n"
            if tagged:
                refs += f"{'9' * 40}\trefs/tags/v0.173.4\n"
            return refs
        assert args == ["show", f"{LIVE_MAIN_SHA}:pyproject.toml"]
        return f'[project]\nversion = "{version}"\n'

    monkeypatch.setenv("NEXT_VERSION", "0.173.4")
    monkeypatch.setattr(module, "_git_read", read)


@pytest.mark.parametrize(
    ("current", "tagged", "reason"),
    [
        ("0.173.4", False, "does not advance live main 0.173.4"),
        ("0.174.0", False, "does not advance live main 0.174.0"),
        ("0.173.3", True, "Tag v0.173.4 already exists"),
    ],
)
def test_stale_candidate_stops_before_finalization(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    current: str,
    tagged: bool,
    reason: str,
) -> None:
    """A previously green gate cannot authorize a consumed version or tag."""
    _live_git(monkeypatch, module, version=current, tagged=tagged)

    def reject_load(*args: object) -> None:
        pytest.fail("Unsafe candidate reached the release finalizers")

    monkeypatch.setattr(module, "_load", reject_load)

    assert module.main() == 1
    output = capsys.readouterr().out
    assert "::error title=Release candidate rejected::" in output
    assert reason in output


@pytest.mark.parametrize("current", ["0.173.3", "0.173.4a1", "0.173.4rc1"])
def test_unconsumed_candidate_runs_all_finalizers(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    current: str,
) -> None:
    """A fresh version still finalizes changelog, support table and image pin."""
    _live_git(monkeypatch, module, version=current)
    finalized = tmp_path / "finalized"

    def load(name: str, filename: str) -> SimpleNamespace:
        def finalize(argv: list[str]) -> int:
            with finalized.open("a", encoding="utf-8") as handle:
                handle.write(f"{filename}\n")
            return 0

        return SimpleNamespace(main=finalize)

    monkeypatch.setattr(module, "_load", load)

    assert module.main() == 0
    assert finalized.read_text(encoding="utf-8").splitlines() == [
        "format-changelog.py",
        "update-security-support.py",
        "sync-pinned-release-image.py",
    ]


@pytest.mark.parametrize(
    "error",
    [
        OSError("origin unreachable"),
        subprocess.TimeoutExpired("git ls-remote", 30),
        subprocess.CalledProcessError(128, "git show live-main:pyproject.toml"),
    ],
)
def test_unreadable_live_state_fails_closed(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: Exception,
) -> None:
    """A read failure must never turn stale generation back on."""
    monkeypatch.setenv("NEXT_VERSION", "0.173.4")

    def fail_read(**kwargs: object) -> str:
        raise error

    monkeypatch.setattr(module, "_git_read", fail_read)

    assert module.main() == 1
    assert "Release candidate rejected" in capsys.readouterr().out


@pytest.mark.parametrize("refs", ["", "not-a-sha\trefs/heads/main\n"])
def test_unresolved_main_cannot_authorize_generation(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    refs: str,
) -> None:
    """A successful Git command without a usable main ref is still unsafe."""
    monkeypatch.setenv("NEXT_VERSION", "0.173.4")
    monkeypatch.setattr(module, "_git_read", lambda **kwargs: refs)

    assert module.main() == 1
    assert "Cannot resolve live origin/main" in capsys.readouterr().out


@pytest.mark.parametrize("version", ["", "v0.173.4", "0.173.4;echo unsafe"])
def test_invalid_candidate_is_rejected_without_git_reads(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    version: str,
) -> None:
    """NEXT_VERSION must be a stable version before it enters a Git argument."""
    monkeypatch.setenv("NEXT_VERSION", version)

    def reject_read(**kwargs: object) -> str:
        pytest.fail("Invalid NEXT_VERSION reached Git")

    monkeypatch.setattr(module, "_git_read", reject_read)

    assert module.main() == 1
    assert "Expected a stable X.Y.Z" in capsys.readouterr().out


def test_version_ordering_is_numeric(module: Any) -> None:
    """A legitimate minor bump must not sort behind a two-digit patch."""
    assert module._stable_version("0.174.0") > module._stable_version("0.173.99")
