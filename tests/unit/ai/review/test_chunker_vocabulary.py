"""Table-driven tests for the extracted chunker shell/CI vocabulary.

Locks the contents of :mod:`lintro.ai.review.chunker.vocabulary` and exercises
the high-churn wrapper-stripping paths (env/sudo/timeout, uv/node/bun flags)
through the private matcher helpers that consume that vocabulary, so a future
vocabulary edit that changes parsing behaviour is caught here.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from assertpy import assert_that

from lintro.ai.review.chunker import vocabulary
from lintro.ai.review.chunker.workflow_scripts import (
    _is_workflow_linked_script,
    _normalize_invoked_command_segment,
    _segment_executes_reference_path,
)


@dataclass(frozen=True)
class VocabularyGroupCase:
    """One expected vocabulary set/dict and its members."""

    id: str
    actual: frozenset[str]
    expected: frozenset[str]


_GROUP_CASES: tuple[VocabularyGroupCase, ...] = (
    VocabularyGroupCase(
        id="uv-arg-options",
        actual=vocabulary._UV_ARG_OPTIONS,
        expected=frozenset({"with", "directory", "project", "package", "python"}),
    ),
    VocabularyGroupCase(
        id="command-dispatch-wrappers",
        actual=vocabulary._COMMAND_DISPATCH_WRAPPERS,
        expected=frozenset({"sudo", "timeout"}),
    ),
    VocabularyGroupCase(
        id="shell-dispatch-wrappers",
        actual=vocabulary._SHELL_DISPATCH_WRAPPERS,
        expected=frozenset({"exec", "command"}),
    ),
    VocabularyGroupCase(
        id="shell-compound-leaders",
        actual=vocabulary._SHELL_COMPOUND_LEADERS,
        expected=frozenset({"then", "do", "else", "elif"}),
    ),
    VocabularyGroupCase(
        id="action-manifest-names",
        actual=vocabulary._ACTION_MANIFEST_NAMES,
        expected=frozenset(
            {
                "package.json",
                "package-lock.json",
                "pnpm-lock.yaml",
                "bun.lock",
                "bun.lockb",
                "yarn.lock",
            },
        ),
    ),
)


@pytest.mark.parametrize("case", _GROUP_CASES, ids=[c.id for c in _GROUP_CASES])
def test_vocabulary_group_membership(case: VocabularyGroupCase) -> None:
    """Each vocabulary group holds exactly its documented members."""
    assert_that(case.actual).is_equal_to(case.expected)


def test_dispatch_positional_operands_cover_wrappers() -> None:
    """Every command-dispatch wrapper has a positional-operand count."""
    assert_that(set(vocabulary._DISPATCH_POSITIONAL_OPERANDS.keys())).is_equal_to(
        set(vocabulary._COMMAND_DISPATCH_WRAPPERS),
    )
    assert_that(vocabulary._DISPATCH_POSITIONAL_OPERANDS["timeout"]).is_equal_to(1)
    assert_that(vocabulary._DISPATCH_POSITIONAL_OPERANDS["sudo"]).is_equal_to(0)


@dataclass(frozen=True)
class StripCase:
    """A shell segment and the command token expected after wrapper stripping."""

    id: str
    segment: str
    expected_command: str


_STRIP_CASES: tuple[StripCase, ...] = (
    StripCase(
        id="timeout-consumes-duration",
        segment="timeout 30 scripts/build.sh",
        expected_command="scripts/build.sh",
    ),
    StripCase(
        id="sudo-consumes-user-flag",
        segment="sudo -u ci scripts/build.sh",
        expected_command="scripts/build.sh",
    ),
    StripCase(
        id="env-var-assignment",
        segment="env FOO=bar scripts/build.sh",
        expected_command="scripts/build.sh",
    ),
    StripCase(
        id="exec-wrapper",
        segment="exec scripts/build.sh",
        expected_command="scripts/build.sh",
    ),
    StripCase(
        id="compound-leader-then",
        segment="then scripts/build.sh",
        expected_command="scripts/build.sh",
    ),
)


@pytest.mark.parametrize("case", _STRIP_CASES, ids=[c.id for c in _STRIP_CASES])
def test_wrapper_stripping_exposes_command(case: StripCase) -> None:
    """Dispatch/env/compound wrappers are consumed before the invoked command."""
    normalized = _normalize_invoked_command_segment(segment=case.segment)
    assert_that(normalized).starts_with(case.expected_command)


@dataclass(frozen=True)
class ExecutesCase:
    """A shell segment plus whether it executes ``scripts/build.sh``."""

    id: str
    segment: str
    executes: bool


_EXECUTES_CASES: tuple[ExecutesCase, ...] = (
    ExecutesCase(
        id="timeout-executes-script",
        segment="timeout 30 scripts/build.sh",
        executes=True,
    ),
    ExecutesCase(
        id="sudo-executes-script",
        segment="sudo -u ci scripts/build.sh",
        executes=True,
    ),
    ExecutesCase(
        id="non-execution-command-cat",
        segment="cat scripts/build.sh",
        executes=False,
    ),
)


@pytest.mark.parametrize("case", _EXECUTES_CASES, ids=[c.id for c in _EXECUTES_CASES])
def test_segment_execution_uses_vocabulary(case: ExecutesCase) -> None:
    """Vocabulary-driven wrapper stripping decides script execution."""
    result = _segment_executes_reference_path(
        segment=case.segment,
        path="scripts/build.sh",
        cwd="",
    )
    assert_that(result).is_equal_to(case.executes)


@dataclass(frozen=True)
class ManifestCase:
    """A changed local-action file and whether it is workflow-linked."""

    id: str
    path: str
    linked: bool


_MANIFEST_CASES: tuple[ManifestCase, ...] = (
    ManifestCase(
        id="package-json-linked",
        path=".github/actions/setup/package.json",
        linked=True,
    ),
    ManifestCase(
        id="yarn-lock-linked",
        path=".github/actions/setup/yarn.lock",
        linked=True,
    ),
    ManifestCase(
        id="readme-not-linked",
        path=".github/actions/setup/README.md",
        linked=False,
    ),
)


@pytest.mark.parametrize("case", _MANIFEST_CASES, ids=[c.id for c in _MANIFEST_CASES])
def test_action_manifest_names_drive_linking(case: ManifestCase) -> None:
    """Action manifest vocabulary decides whether a changed file is linked."""
    assert_that(_is_workflow_linked_script(path=case.path)).is_equal_to(case.linked)
