"""Tests for the ``@lintro review`` comment parser (#2627 PR 2, #2795)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.ai.review import commands
from lintro.ai.review.commands import (
    MAX_PATHS,
    ReviewCommand,
    ReviewRequestMode,
    parse_review_command,
)


@pytest.mark.parametrize(
    "body",
    [
        "",
        "LGTM",
        "please @lintro review this",
        "@lintro reviewer",
        "@lintro reviews",
        " @lintro review",
        "\n@lintro review",
    ],
    ids=[
        "empty",
        "unrelated",
        "not-at-start",
        "reviewer",
        "reviews",
        "leading-space",
        "leading-newline",
    ],
)
def test_a_comment_that_is_not_a_request_parses_to_none(body: str) -> None:
    """Only a comment that starts with the command is a request.

    Args:
        body: The comment text.
    """
    assert_that(parse_review_command(body)).is_none()


@pytest.mark.parametrize(
    "body",
    [
        "@lintro review",
        "@lintro review   ",
        "@LINTRO Review",
        "@lintro review\nthanks!",
    ],
    ids=["bare", "trailing-space", "case-insensitive", "second-line-ignored"],
)
def test_the_bare_command_asks_for_a_full_review(body: str) -> None:
    """``@lintro review`` alone is a full review; later lines are ignored.

    Args:
        body: The comment text.
    """
    assert_that(parse_review_command(body)).is_equal_to(
        ReviewCommand(mode=ReviewRequestMode.FULL),
    )


@pytest.mark.parametrize("body", ["@lintro review delta", "@lintro review DELTA"])
def test_delta_asks_for_the_change_since_the_last_round(body: str) -> None:
    """``delta`` is the explicit delta round, case-insensitive.

    Args:
        body: The comment text.
    """
    assert_that(parse_review_command(body)).is_equal_to(
        ReviewCommand(mode=ReviewRequestMode.DELTA),
    )


def test_path_prefixes_ask_for_a_targeted_review() -> None:
    """Each word after the command is one path prefix, in order."""
    command = parse_review_command(
        "@lintro review lintro/ai/review scripts/ci/run-ai-review.sh docs",
    )

    assert_that(command).is_equal_to(
        ReviewCommand(
            mode=ReviewRequestMode.PATHS,
            paths=("lintro/ai/review", "scripts/ci/run-ai-review.sh", "docs"),
        ),
    )


@pytest.mark.parametrize(
    ("body", "problem"),
    [
        ("@lintro review delta now", "`delta` takes no arguments"),
        ("@lintro review src/*.py", "character other than"),
        ("@lintro review src/**", "character other than"),
        ("@lintro review src/?", "character other than"),
        ("@lintro review ../etc", "`..` segment"),
        ("@lintro review a/../b", "`..` segment"),
        ("@lintro review /etc/passwd", "absolute"),
        ("@lintro review --full", "starts with `-`"),
        ("@lintro review -x", "starts with `-`"),
        ("@lintro review $(id)", "character other than"),
        ("@lintro review " + "a" * 201, "longer than 200"),
        ("@lintro review " + " ".join(["p"] * (MAX_PATHS + 1)), f"at most {MAX_PATHS}"),
    ],
    ids=[
        "delta-with-args",
        "star",
        "double-star",
        "question-mark",
        "dotdot",
        "inner-dotdot",
        "absolute",
        "leading-double-dash",
        "leading-dash",
        "shell-syntax",
        "too-long",
        "too-many",
    ],
)
def test_a_malformed_request_is_a_usage_command(body: str, problem: str) -> None:
    """A malformed request asks for the usage text, naming the problem.

    Args:
        body: The comment text.
        problem: Text the recorded problem must contain.
    """
    command = parse_review_command(body)

    assert_that(command).is_not_none()
    assert command is not None
    assert_that(command.mode).is_equal_to(ReviewRequestMode.USAGE)
    assert_that(command.paths).is_empty()
    assert_that(command.problem).contains(problem)


def test_the_problem_never_echoes_the_comment() -> None:
    """The recorded problem is constant text, never the comment's words."""
    command = parse_review_command("@lintro review secret-token-shaped$thing")

    assert command is not None
    assert_that(command.problem).does_not_contain("secret-token-shaped")


def test_the_module_imports_only_the_standard_library() -> None:
    """The CI gate loads this file by path before lintro is installed."""
    tree = ast.parse(Path(commands.__file__).read_text(encoding="utf-8"))
    imported = {
        (node.module or "").split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    } | {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }

    assert_that(imported).is_subset_of(
        {"__future__", "re", "dataclasses", "enum", "typing"},
    )
