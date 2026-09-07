"""Tests for the shared manifest-name vocabulary (issue #1973).

The chunker's local-action resolver and the changed-file classifier used to keep
independent literal sets of manifest and lockfile names, which drifted. These
tests pin the reconciled shape: one shared core both consumers derive from, and
no regression in what either consumer recognises.
"""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.ai.review.chunker.github_action_paths import (
    _ACTION_MANIFEST_NAMES,
    _github_action_reference_paths,
)
from lintro.ai.review.classifier import (
    _DEPENDENCY_MANIFEST_NAMES,
    classify_changed_files,
)
from lintro.ai.review.enums.changed_file_status import ChangedFileStatus
from lintro.ai.review.enums.file_domain import FileDomain
from lintro.ai.review.models.changed_file import ChangedFile
from lintro.ai.review.vocab import (
    DEPENDENCY_MANIFEST_EXTRA_NAMES,
    SHARED_MANIFEST_NAMES,
)


def _domains_for(*, path: str) -> set[FileDomain]:
    """Classify one path and return its domains as a set.

    Args:
        path: Repository-relative file path to classify.

    Returns:
        set[FileDomain]: Domains the classifier assigned to the path.
    """
    classifications = classify_changed_files(
        [
            ChangedFile(
                path=path,
                status=ChangedFileStatus.MODIFIED,
                additions=1,
                deletions=0,
            ),
        ],
    )
    return set(classifications[0].domains)


def test_action_manifest_names_are_the_shared_core() -> None:
    """The action resolver's set is exactly the shared vocabulary core."""
    assert_that(set(_ACTION_MANIFEST_NAMES)).is_equal_to(set(SHARED_MANIFEST_NAMES))


def test_dependency_manifest_names_contain_the_shared_core() -> None:
    """The classifier's set is a superset of the shared vocabulary core."""
    assert_that(set(_DEPENDENCY_MANIFEST_NAMES)).contains(*SHARED_MANIFEST_NAMES)


def test_dependency_manifest_names_are_core_plus_extras() -> None:
    """The classifier's set adds only the declared scope-specific extras."""
    assert_that(set(_DEPENDENCY_MANIFEST_NAMES)).is_equal_to(
        set(SHARED_MANIFEST_NAMES) | set(DEPENDENCY_MANIFEST_EXTRA_NAMES),
    )


def test_shared_and_extra_vocabularies_do_not_overlap() -> None:
    """Each manifest name is registered in exactly one vocabulary set."""
    assert_that(
        set(SHARED_MANIFEST_NAMES) & set(DEPENDENCY_MANIFEST_EXTRA_NAMES),
    ).is_empty()


def test_manifest_names_are_lowercase() -> None:
    """Both vocabularies stay lowercase, since consumers match on a lowered name."""
    names = set(SHARED_MANIFEST_NAMES) | set(DEPENDENCY_MANIFEST_EXTRA_NAMES)
    assert_that([name for name in names if name != name.lower()]).is_empty()


@pytest.mark.parametrize(
    "name",
    [
        "yarn.lock",
        "uv.lock",
        "poetry.lock",
        "cargo.lock",
        "package-lock.json",
        "bun.lockb",
    ],
    ids=[
        "yarn",
        "uv",
        "poetry",
        "cargo",
        "npm",
        "bun_binary",
    ],
)
def test_lockfiles_classify_as_dependency_manifests(name: str) -> None:
    """Every reconciled lockfile name lands in the dependency domain."""
    assert_that(_domains_for(path=name)).contains(FileDomain.DEPS)


@pytest.mark.parametrize(
    "name",
    sorted(SHARED_MANIFEST_NAMES),
)
def test_shared_manifests_resolve_a_local_action_root(name: str) -> None:
    """Every shared-core manifest resolves up to its local action directory."""
    assert_that(
        _github_action_reference_paths(path=f".github/actions/demo/{name}"),
    ).is_equal_to([".github/actions/demo"])
