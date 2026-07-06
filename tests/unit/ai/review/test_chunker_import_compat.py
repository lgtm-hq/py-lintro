"""Import-compatibility tests for the split chunker package.

Issue #1024 decomposed the oversized ``chunker/workflow_scripts`` module into
``shell_run_parse`` (shell tokenizing) and ``github_action_paths`` (local action
resolution). These tests pin the public API and the internal import seams that
callers such as ``chunker.grouping`` rely on, so the refactor stays behaviour and
import compatible.
"""

from __future__ import annotations

import importlib

from assertpy import assert_that


def test_public_chunk_review_context_import_paths_are_stable() -> None:
    """The public ``chunk_review_context`` remains importable from both paths."""
    from lintro.ai.review import chunk_review_context as via_review
    from lintro.ai.review.chunker import chunk_review_context as via_chunker

    assert_that(via_review).is_same_as(via_chunker)
    assert_that(callable(via_chunker)).is_true()


def test_chunker_package_reexports_are_stable() -> None:
    """The chunker package still re-exports its documented helper surface."""
    chunker = importlib.import_module("lintro.ai.review.chunker")

    for name in ("chunk_review_context", "_hunk_signature", "_prune_semantic_groups"):
        assert_that(hasattr(chunker, name)).described_as(name).is_true()


def test_workflow_scripts_public_seam_is_stable() -> None:
    """``chunker.grouping`` imports these three names from ``workflow_scripts``."""
    module = importlib.import_module(
        "lintro.ai.review.chunker.workflow_scripts",
    )

    for name in (
        "_is_workflow_linked_script",
        "_script_referenced_in_workflow",
        "_workflow_text_for_matching",
    ):
        assert_that(hasattr(module, name)).described_as(name).is_true()


def test_extracted_submodules_expose_moved_helpers() -> None:
    """Shell parsing and action-path helpers live in their new leaf modules."""
    shell = importlib.import_module(
        "lintro.ai.review.chunker.shell_run_parse",
    )
    actions = importlib.import_module(
        "lintro.ai.review.chunker.github_action_paths",
    )

    for name in (
        "_line_references_path",
        "_segment_executes_reference_path",
        "_strip_run_command_prefix",
        "_RUN_COMMAND_PREFIX",
    ):
        assert_that(hasattr(shell, name)).described_as(name).is_true()

    for name in (
        "_github_action_directory",
        "_github_action_reference_paths",
        "_resolve_github_action_root",
    ):
        assert_that(hasattr(actions, name)).described_as(name).is_true()
