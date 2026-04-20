"""Tests for the semantic_release_compute_next utility."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that


def _fake_completed(stdout: str = "") -> subprocess.CompletedProcess[str]:
    """Return a fake subprocess.CompletedProcess with the given stdout.

    Args:
        stdout: Standard output string with trailing whitespace stripped.

    Returns:
        subprocess.CompletedProcess: Fake subprocess.CompletedProcess with stdout.
    """
    return subprocess.CompletedProcess(
        args=["git"],
        returncode=0,
        stdout=stdout,
        stderr="",
    )


@pytest.fixture(autouse=True)
def _ensure_repo_root_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure script imports resolve from repository root.

    This avoids issues when tests are invoked from temp directories.

    Args:
        monkeypatch: pytest.MonkeyPatch instance.
    """
    repo_root = Path(__file__).resolve().parents[2]
    monkeypatch.chdir(repo_root)


def test_run_git_describe_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """run_git should allow safe describe invocation.

    Args:
        monkeypatch: pytest.MonkeyPatch instance.
    """
    from scripts.ci.maintenance import semantic_release_compute_next as mod

    monkeypatch.setattr(mod.shutil, "which", lambda *_: "/usr/bin/git")  # type: ignore[attr-defined]
    monkeypatch.setattr(
        mod.subprocess,  # type: ignore[attr-defined]
        "run",
        lambda *_, **__: _fake_completed("v1.2.3\n"),
    )

    out = mod.run_git("describe", "--tags", "--abbrev=0", "--match", "v*")
    assert_that(out).is_equal_to("v1.2.3")


def test_run_git_rev_parse_head_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """run_git should allow 'rev-parse HEAD'.

    Args:
        monkeypatch: pytest.MonkeyPatch instance.
    """
    from scripts.ci.maintenance import semantic_release_compute_next as mod

    monkeypatch.setattr(mod.shutil, "which", lambda *_: "/usr/bin/git")  # type: ignore[attr-defined]
    monkeypatch.setattr(
        mod.subprocess,  # type: ignore[attr-defined]
        "run",
        lambda *_, **__: _fake_completed("abcd123\n"),
    )

    out = mod.run_git("rev-parse", "HEAD")
    assert_that(out).is_equal_to("abcd123")


@pytest.mark.parametrize(
    "args",
    [
        ("log", "HEAD", "--pretty=%s"),
        ("log", "HEAD", "--pretty=%B"),
        ("log", "v1.2.3..HEAD", "--pretty=%s"),
    ],
)
def test_run_git_log_allowed(
    monkeypatch: pytest.MonkeyPatch,
    args: tuple[str, ...],
) -> None:
    """run_git should allow the specific log forms used by the module.

    Args:
        monkeypatch: pytest.MonkeyPatch instance.
        args: Tuple of arguments to pass to run_git.
    """
    from scripts.ci.maintenance import semantic_release_compute_next as mod

    monkeypatch.setattr(mod.shutil, "which", lambda *_: "/usr/bin/git")  # type: ignore[attr-defined]
    monkeypatch.setattr(mod.subprocess, "run", lambda *_, **__: _fake_completed("ok\n"))  # type: ignore[attr-defined]

    out = mod.run_git(*args)
    assert_that(out).is_equal_to("ok")


@pytest.mark.parametrize(
    "args",
    [
        ("status",),
        ("log", "--since=yesterday"),
        ("log", "HEAD; rm -rf /"),
        ("rev-parse", "main"),
    ],
)
def test_run_git_rejects_unsupported_or_unsafe(
    monkeypatch: pytest.MonkeyPatch,
    args: tuple[str, ...],
) -> None:
    """run_git should reject commands/args outside the strict allowlist.

    Args:
        monkeypatch: pytest.MonkeyPatch instance.
        args: Tuple of arguments to pass to run_git.
    """
    from scripts.ci.maintenance import semantic_release_compute_next as mod

    monkeypatch.setattr(mod.shutil, "which", lambda *_: "/usr/bin/git")  # type: ignore[attr-defined]

    # subprocess.run should not be called; keep a guard that would fail if it is
    def _should_not_run(
        *_a: Any,
        **_k: Any,
    ) -> subprocess.CompletedProcess[str]:  # pragma: no cover
        raise AssertionError("subprocess.run must not be invoked for rejected args")

    monkeypatch.setattr(mod.subprocess, "run", _should_not_run)  # type: ignore[attr-defined]

    with pytest.raises(ValueError):
        mod.run_git(*args)


def _write_dockerfile(tmp_path: Path, digest: str | None) -> None:
    """Write a Dockerfile with or without a pinned tools digest.

    Args:
        tmp_path: Directory to write Dockerfile into.
        digest: Full ``sha256:<hex>`` digest to pin, or ``None`` to omit.
    """
    if digest is None:
        (tmp_path / "Dockerfile").write_text("FROM scratch\n")
    else:
        (tmp_path / "Dockerfile").write_text(
            f"FROM scratch\nARG TOOLS_IMAGE=ghcr.io/lgtm-hq/lintro-tools:latest@{digest}\n",
        )


def test_read_pinned_tools_digest_parses_dockerfile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dockerfile ARG is the single source of truth for the pinned digest."""
    from scripts.ci.maintenance import semantic_release_compute_next as mod

    digest = "sha256:" + ("a" * 64)
    _write_dockerfile(tmp_path, digest)
    monkeypatch.chdir(tmp_path)

    assert_that(mod.read_pinned_tools_digest()).is_equal_to(digest)


def test_read_pinned_tools_digest_returns_empty_when_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing Dockerfile line yields empty string, not an exception."""
    from scripts.ci.maintenance import semantic_release_compute_next as mod

    _write_dockerfile(tmp_path, None)
    monkeypatch.chdir(tmp_path)

    assert_that(mod.read_pinned_tools_digest()).is_equal_to("")


def test_detect_digest_drift_true_when_pinned_differs_from_registry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Drift flag fires only when both digests resolve and differ."""
    from scripts.ci.maintenance import semantic_release_compute_next as mod

    pinned = "sha256:" + ("a" * 64)
    registry = "sha256:" + ("b" * 64)
    _write_dockerfile(tmp_path, pinned)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(mod, "fetch_registry_tools_digest", lambda: registry)

    drift, got_pinned, got_registry = mod.detect_digest_drift()

    assert_that(drift).is_true()
    assert_that(got_pinned).is_equal_to(pinned)
    assert_that(got_registry).is_equal_to(registry)


def test_detect_digest_drift_false_when_registry_unknown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Registry unreachable must not block the release pipeline."""
    from scripts.ci.maintenance import semantic_release_compute_next as mod

    _write_dockerfile(tmp_path, "sha256:" + ("a" * 64))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(mod, "fetch_registry_tools_digest", lambda: "")

    drift, _, registry = mod.detect_digest_drift()

    assert_that(drift).is_false()
    assert_that(registry).is_equal_to("")


def test_detect_digest_drift_false_when_digests_match(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Matching digests are a no-op — no spurious patch bumps."""
    from scripts.ci.maintenance import semantic_release_compute_next as mod

    digest = "sha256:" + ("c" * 64)
    _write_dockerfile(tmp_path, digest)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(mod, "fetch_registry_tools_digest", lambda: digest)

    drift, _, _ = mod.detect_digest_drift()

    assert_that(drift).is_false()


def test_compute_forces_patch_bump_on_digest_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Digest drift alone (no commits) must produce a patch release.

    Without this, weekly cron rebuilds and Renovate ``chore(deps)`` bumps
    would silently skip releases — users would never receive the CVE fixes
    baked into the new tools image.
    """
    from scripts.ci.maintenance import semantic_release_compute_next as mod

    monkeypatch.setattr(mod, "read_last_tag", lambda: "v1.2.3")
    monkeypatch.setattr(
        mod,
        "detect_commit_types",
        lambda _base: (False, False, False),
    )
    monkeypatch.setattr(
        mod,
        "detect_digest_drift",
        lambda: (True, "sha256:" + ("a" * 64), "sha256:" + ("b" * 64)),
    )

    result = mod.compute()

    assert_that(result.next_version).is_equal_to("1.2.4")
    assert_that(result.has_digest_drift).is_true()
    assert_that(result.bump_reason).is_equal_to("digest-drift")


def test_compute_keeps_commit_bump_when_drift_also_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A feat commit wins over drift; drift only joins the reason string."""
    from scripts.ci.maintenance import semantic_release_compute_next as mod

    monkeypatch.setattr(mod, "read_last_tag", lambda: "v1.2.3")
    monkeypatch.setattr(
        mod,
        "detect_commit_types",
        lambda _base: (False, True, False),
    )
    monkeypatch.setattr(
        mod,
        "detect_digest_drift",
        lambda: (True, "sha256:" + ("a" * 64), "sha256:" + ("b" * 64)),
    )

    result = mod.compute()

    assert_that(result.next_version).is_equal_to("1.3.0")
    assert_that(result.bump_reason).is_equal_to("feat+digest-drift")


def test_compute_no_drift_no_commits_produces_no_bump(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Idle main: no commits, no drift — no release PR."""
    from scripts.ci.maintenance import semantic_release_compute_next as mod

    monkeypatch.setattr(mod, "read_last_tag", lambda: "v1.2.3")
    monkeypatch.setattr(
        mod,
        "detect_commit_types",
        lambda _base: (False, False, False),
    )
    monkeypatch.setattr(mod, "detect_digest_drift", lambda: (False, "", ""))

    result = mod.compute()

    assert_that(result.next_version).is_equal_to("")
    assert_that(result.bump_reason).is_equal_to("")
