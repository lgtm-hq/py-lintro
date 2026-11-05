"""The autouse ``_isolate_review_state`` fixture keeps review state per test.

Both review state stores default to cwd-relative paths (``ai-review-state``
and ``.lintro-cache/ai/review-state``), so a review round driven from the
repository root used to leave state behind for later tests. The root
``tests/conftest.py`` fixture redirects both stores into a fresh directory
for every test, while an explicit ``LINTRO_REVIEW_STATE_DIR`` keeps winning.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.ai.review import state_store
from lintro.ai.review.models.review_state import ReviewState
from tests.conftest import _redirect_review_state

_HEX_ROOT = re.compile(r"[0-9a-f]{32}")


def _store_dirs() -> tuple[Path, Path]:
    """Return the CI and local store directories currently in effect.

    Returns:
        tuple[Path, Path]: The CI artifact directory and the local ledger.
    """
    return state_store.state_dir(ci=True), state_store.state_dir(ci=False)


def test_both_stores_write_into_this_tests_private_root(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both stores resolve to an absent per-test root, never under the cwd.

    Args:
        tmp_path: Working directory for the test.
        tmp_path_factory: Per-worker temporary directory factory.
        monkeypatch: Pytest fixture for patching modules and attributes.
    """
    monkeypatch.delenv("LINTRO_REVIEW_STATE_DIR", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.chdir(tmp_path)

    ci_dir, local_dir = _store_dirs()

    root = ci_dir.parent
    assert_that(local_dir.parent).is_equal_to(root)
    assert_that((ci_dir.name, local_dir.name)).is_equal_to(("ci", "local"))
    assert_that(root.parent).is_equal_to(
        tmp_path_factory.getbasetemp().resolve() / "review-state",
    )
    assert_that(_HEX_ROOT.fullmatch(root.name)).is_not_none()
    assert_that(root.exists()).is_false()

    part = state_store.write_state_part(
        state=ReviewState(),
        directory=ci_dir,
        sequence=1,
        final=True,
    )
    ledger = state_store.write_local_state(state=ReviewState(), key="pr-1")

    assert_that(part.parent).is_equal_to(ci_dir)
    assert_that(ledger).is_equal_to(local_dir / "pr-1.json")
    assert_that(sorted(p.name for p in ci_dir.iterdir())).is_equal_to(
        ["part-0001.json", "state.json"],
    )
    assert_that(ledger.is_file()).is_true()
    assert_that(list(tmp_path.iterdir())).is_empty()


def test_every_redirect_hands_out_a_fresh_absent_root(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Each call of the fixture body moves both stores to a new root.

    Args:
        tmp_path_factory: Per-worker temporary directory factory.
    """
    before = (state_store.CI_STATE_DIRNAME, state_store.LOCAL_STATE_DIR)

    with pytest.MonkeyPatch.context() as patch:
        first = _redirect_review_state(patch, tmp_path_factory)
        first_stores = (state_store.CI_STATE_DIRNAME, state_store.LOCAL_STATE_DIR)
        second = _redirect_review_state(patch, tmp_path_factory)
        second_stores = (state_store.CI_STATE_DIRNAME, state_store.LOCAL_STATE_DIR)

    assert_that(first).is_not_equal_to(second)
    assert_that(first_stores).is_equal_to((str(first / "ci"), first / "local"))
    assert_that(second_stores).is_equal_to((str(second / "ci"), second / "local"))
    assert_that([first.exists(), second.exists()]).is_equal_to([False, False])
    assert_that(before).does_not_contain(first_stores[0], second_stores[0])
    # Leaving the monkeypatch restores this test's own redirect.
    assert_that(
        (state_store.CI_STATE_DIRNAME, state_store.LOCAL_STATE_DIR),
    ).is_equal_to(before)


@pytest.mark.parametrize("github_actions", ["true", ""], ids=["actions", "local"])
def test_explicit_review_state_dir_still_wins(
    github_actions: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``LINTRO_REVIEW_STATE_DIR`` overrides the fixture's redirect.

    Args:
        github_actions: ``GITHUB_ACTIONS`` value for the run.
        tmp_path: Directory holding the explicit override.
        monkeypatch: Pytest fixture for patching modules and attributes.
    """
    override = tmp_path / "downloaded-state"
    monkeypatch.setenv("LINTRO_REVIEW_STATE_DIR", str(override))
    monkeypatch.setenv("GITHUB_ACTIONS", github_actions)

    assert_that(_store_dirs()).is_equal_to((override, override))

    part = state_store.write_state_part(
        state=ReviewState(),
        directory=state_store.state_dir(ci=True),
        sequence=1,
    )

    assert_that(part).is_equal_to(override / "part-0001.json")
    assert_that(part.is_file()).is_true()
