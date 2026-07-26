# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""Tests for the pinned release-image sync (#1590)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that

ROOT = Path(__file__).resolve().parents[3]
SYNC_SCRIPT = ROOT / "scripts" / "ci" / "sync-pinned-release-image.py"

DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64


def _load_module() -> Any:
    """Load the hyphenated sync script as an importable module.

    Returns:
        The loaded module.
    """
    spec = importlib.util.spec_from_file_location(
        "sync_pinned_release_image",
        SYNC_SCRIPT,
    )
    assert spec is not None and spec.loader is not None  # narrow for mypy
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def module() -> Any:
    """Provide the loaded sync module.

    Returns:
        The loaded module.
    """
    return _load_module()


def _version_record(*, digest: str, tags: list[str]) -> dict[str, Any]:
    """Build a GitHub Packages API version record.

    Args:
        digest: Version digest, used as the record ``name``.
        tags: Container tags attached to the version.

    Returns:
        A record shaped like the packages API response.
    """
    return {"name": digest, "metadata": {"container": {"tags": tags}}}


def test_picks_the_newest_release_tag(module: Any) -> None:
    """The newest ``major.minor.patch`` tag wins.

    Args:
        module: The loaded sync module.
    """
    resolved = module.latest_published_release(
        [
            _version_record(digest=DIGEST_A, tags=["0.91.9"]),
            _version_record(digest=DIGEST_B, tags=["0.91.49"]),
        ],
    )

    # 0.91.49 > 0.91.9 numerically; a string comparison would pick the wrong one.
    assert_that(resolved).is_equal_to(("0.91.49", DIGEST_B))


def test_ignores_non_release_tags(module: Any) -> None:
    """``sha-``, ``ci-``, ``latest`` and ``cache`` tags are never selected.

    These dominate the tag list — they are exactly why Renovate could not
    enumerate versions in the first place (#1590).

    Args:
        module: The loaded sync module.
    """
    resolved = module.latest_published_release(
        [
            _version_record(digest=DIGEST_A, tags=["sha-95e628b"]),
            _version_record(digest=DIGEST_A, tags=["ci-30192266188"]),
            _version_record(digest=DIGEST_A, tags=["latest"]),
            _version_record(digest=DIGEST_A, tags=["cache-linux-amd64"]),
            _version_record(digest=DIGEST_B, tags=["0.91.48"]),
        ],
    )

    assert_that(resolved).is_equal_to(("0.91.48", DIGEST_B))


def test_release_version_carrying_a_commit_tag_is_still_selectable(
    module: Any,
) -> None:
    """A release tagged alongside ``sha-<commit>`` is still a candidate.

    Release images carry both, so requiring a sole release tag would make the
    newest release invisible.

    Args:
        module: The loaded sync module.
    """
    resolved = module.latest_published_release(
        [_version_record(digest=DIGEST_A, tags=["sha-95e628b", "0.91.48"])],
    )

    assert_that(resolved).is_equal_to(("0.91.48", DIGEST_A))


def test_no_release_versions_yields_none(module: Any) -> None:
    """With nothing release-tagged, the caller must leave the pin alone.

    Args:
        module: The loaded sync module.
    """
    resolved = module.latest_published_release(
        [_version_record(digest=DIGEST_A, tags=["sha-95e628b"])],
    )

    assert_that(resolved).is_none()


def test_malformed_records_are_skipped(module: Any) -> None:
    """Unexpected payload shapes never raise.

    This runs in the unattended release path, so a surprising response must
    degrade to "no change" rather than break the release PR.

    Args:
        module: The loaded sync module.
    """
    resolved = module.latest_published_release(
        [
            "not-a-dict",
            {"name": "not-a-digest", "metadata": {"container": {"tags": ["0.9.9"]}}},
            {"name": DIGEST_A, "metadata": None},
            {"name": DIGEST_A, "metadata": {"container": {"tags": "not-a-list"}}},
            _version_record(digest=DIGEST_B, tags=["0.91.48"]),
        ],
    )

    assert_that(resolved).is_equal_to(("0.91.48", DIGEST_B))


def test_missing_token_is_non_fatal(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing token warns and exits 0 without touching the workflows.

    Args:
        module: The loaded sync module.
        monkeypatch: Fixture used to clear the token environment.
    """
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    assert_that(module.main([])).is_equal_to(0)


def test_every_pinned_site_is_declared(module: Any) -> None:
    """The declared site counts must match the workflows on disk.

    The rewrite refuses to run when the counts disagree, so a drifted
    declaration would silently disable the sync rather than fail loudly.

    Args:
        module: The loaded sync module.
    """
    for relative_path, expected_sites in module.PINNED_SITES.items():
        text = (ROOT / relative_path).read_text(encoding="utf-8")
        found = len(module._PIN_PATTERN.findall(text))  # noqa: SLF001
        assert_that(found).described_as(relative_path).is_equal_to(expected_sites)
