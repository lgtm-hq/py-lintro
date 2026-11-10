"""Local PR diff reconstruction against a real remote and private pull ref."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that
from click.testing import CliRunner

from lintro.ai.review import preparation
from lintro.ai.review.context import pr_diff_fallback
from lintro.ai.review.context.collection import (
    _collect_branch_context,
    collect_review_context,
)
from lintro.ai.review.context.git_ops import _run_git
from lintro.ai.review.enums.review_context_error_code import ReviewContextErrorCode
from lintro.ai.review.exceptions import ReviewContextError
from lintro.ai.review.pr_head import remove_pr_head
from lintro.cli_utils.commands.review import review_command
from tests.unit.ai.review import pr_diff_fixtures
from tests.unit.ai.review import test_review_command_json_error as cli_helpers
from tests.unit.ai.review.test_pr_head_2733 import _git

patched_review = cli_helpers.patched_review
pr_remote = pr_diff_fixtures.pr_remote
github = pr_diff_fixtures.github


@pytest.mark.parametrize("shallow", [False, True])
def test_snapshot_matches_branch_range(
    pr_remote: dict[str, Any],
    github: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    shallow: bool,
) -> None:
    """Both clone types reconstruct the branch snapshot, including unusual files."""
    expected = _collect_branch_context(base=pr_remote["base"])
    if shallow:
        clone = tmp_path / "shallow"
        _git(
            tmp_path,
            "clone",
            "--depth",
            "1",
            "--branch",
            "main",
            pr_remote["origin"].as_uri(),
            str(clone),
        )
        monkeypatch.chdir(clone)
    context = collect_review_context(pr_number=1, repo="base/project")
    try:
        assert_that(context.unified_diff).is_equal_to(expected.unified_diff)
        assert_that(context.changed_files).is_equal_to(expected.changed_files)
        assert_that(context.unified_diff).does_not_contain("base-only.txt")
        assert_that(context.base_ref).is_equal_to(pr_remote["merge_base"])
        assert_that(context.diff_source).is_equal_to("local-git")
        assert_that({f.path for f in context.changed_files}).contains(
            "deleted.txt",
            "renamed.txt",
            "binary.dat",
            "mode.sh",
            "quoted\tfile.txt",
        )
        if not shallow:
            assert_that(
                _run_git(args=["rev-parse", "--is-shallow-repository"]).stdout.strip(),
            ).is_equal_to("false")
    finally:
        remove_pr_head(context.head_worktree)


@pytest.mark.parametrize("excluded", ["rename.txt", "renamed.txt"])
def test_fallback_filters_preserve_rename_skips(
    github: dict[str, Any],
    excluded: str,
) -> None:
    """Either rename path excludes the file and leaves a disclosed skip."""
    context = collect_review_context(
        pr_number=1,
        repo="base/project",
        exclude_globs=[excluded, "*.dat"],
    )
    try:
        assert_that({f.path for f in context.changed_files}).does_not_contain(
            "renamed.txt",
            "binary.dat",
        )
        assert_that({f.path for f in context.skipped_files}).is_equal_to(
            {"renamed.txt", "binary.dat"},
        )
        assert_that(context.unified_diff).contains("keep.py").does_not_contain(
            "rename from",
            "binary.dat",
        )
    finally:
        remove_pr_head(context.head_worktree)


@pytest.mark.parametrize(
    "failure",
    [
        "no-repository",
        "fetch-refused",
        "head-mismatch",
        "compare",
        "missing-merge-base",
        "malformed-compare",
        "compare-http",
        "compare-json",
        "missing-owner",
        "empty-local-diff",
        "git-timeout",
        "compare-timeout",
    ],
)
def test_fallback_failure_discloses_both_causes(
    patched_review: None,
    failure: str,
    pr_remote: dict[str, Any],
    github: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every failed reconstruction exposes a context failure with both causes."""
    if failure == "no-repository":
        monkeypatch.chdir(tmp_path)
    elif failure == "fetch-refused":
        _git(pr_remote["work"], "remote", "set-url", "origin", str(tmp_path / "absent"))
    elif failure == "head-mismatch":
        github["head"] = pr_remote["base"]
    elif failure == "empty-local-diff":
        github["compare"] = {"merge_base_commit": {"sha": pr_remote["head"]}}
    elif failure == "missing-owner":
        github["owner"] = ""
    elif failure == "compare":
        github["compare"] = {}
    elif failure == "missing-merge-base":
        github["compare"] = {"merge_base_commit": {"sha": "f" * 40}}
    elif failure == "malformed-compare":
        github["compare"] = {"merge_base_commit": []}
    elif failure in {"compare-http", "compare-json"}:
        github["compare"] = (
            "http-error" if failure == "compare-http" else "invalid-json"
        )
    elif failure == "compare-timeout":
        github["compare"] = ReviewContextError(
            "gh api timed out after 120s",
            code="gh-command-failed",
        )
    else:

        def timeout(**kwargs: Any) -> Any:
            """Expose the git helper's timeout contract during fallback."""
            raise ReviewContextError(
                "git command timed out after 120s",
                code="git-command-failed",
            )

        monkeypatch.setattr(pr_diff_fallback, "_ensure_git_repo", timeout)
    monkeypatch.setattr(preparation, "collect_review_context", collect_review_context)
    result = CliRunner().invoke(
        review_command,
        ["--pr", "1", "--repo", "base/project", "--output", "json"],
    )
    assert_that(result.exit_code).is_equal_to(2)
    error = json.loads(result.output[result.output.index("{") :])["error"]
    assert_that(error).contains_entry(
        code="pr-local-diff-failed",
        kind="context_unavailable",
    )
    assert_that(error["message"]).contains(
        "GitHub refused",
        "for size",
        "local diff could not be built",
        "persist-credentials",
        "git credentials",
        "retry",
    )


@pytest.mark.parametrize(
    "message",
    ["HTTP 401: Bad credentials", "HTTP 500: server error", "ordinary failure"],
)
def test_non_size_failure_never_uses_local_diff(
    github: dict[str, Any],
    message: str,
) -> None:
    """Ordinary GitHub failures retain their primary error with a usable checkout."""
    github["refusal"] = message
    with pytest.raises(ReviewContextError) as raised:
        collect_review_context(pr_number=1, repo="base/project")
    assert_that(raised.value.code).is_equal_to(ReviewContextErrorCode.GH_COMMAND_FAILED)
    assert_that(str(raised.value)).contains(message).does_not_contain("local diff")


@pytest.mark.parametrize(
    "message",
    [
        "HTTP 406: diff exceeded the maximum number of files",
        "diff too large",
        "diff is too large",
    ],
)
def test_recognized_size_refusals(github: dict[str, Any], message: str) -> None:
    """Line and file limits both produce a locally reconstructed review context."""
    github["refusal"] = message
    context = collect_review_context(
        pr_number=1,
        repo="base/project",
        paths=["keep.py"],
    )
    try:
        assert_that([f.path for f in context.changed_files]).is_equal_to(["keep.py"])
        assert_that(context.unified_diff).contains("+answer = 42").does_not_contain(
            "binary.dat",
        )
    finally:
        remove_pr_head(context.head_worktree)


def test_refusal_to_filtered_review_and_sticky(
    github: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A filtered local diff is reviewed and patchless inline findings stay visible."""
    from unittest.mock import AsyncMock, MagicMock

    from lintro.ai.config import AIConfig
    from lintro.ai.enums import AITransport
    from lintro.ai.providers.capabilities import ProviderCapabilities
    from lintro.ai.providers.response import AIResponse
    from lintro.ai.review.github import post_review_to_github
    from lintro.ai.review.orchestrator import run_review
    from lintro.ai.review.output import render_review_json
    from lintro.ai.review.session import ReviewSessionOptions
    from tests.unit.ai.review.comment_reporter import CommentReporter

    provider = MagicMock()
    provider.aclose = AsyncMock()
    provider.model_name = "m"
    provider.name = "anthropic"
    provider.capabilities = ProviderCapabilities(supports_sessions=False)
    answer = {
        "summary": "Review of the selected Python change",
        "checklist": [],
        "findings": [
            {
                "file": "keep.py",
                "line": 1,
                "priority": "P1",
                "title": "Validate the answer",
                "body": "Validate this value before use.",
            },
        ],
    }
    seen: list[str] = []

    async def call(**kwargs: Any) -> AIResponse:
        """Return a deterministic provider answer and capture review scope."""
        seen.append(str(kwargs))
        return AIResponse(content=json.dumps(answer), model="m", provider="anthropic")

    monkeypatch.setattr("lintro.ai.review.provider_call.call_ai", call)
    context = collect_review_context(
        pr_number=1,
        repo="base/project",
        exclude_globs=["*.txt", "*.dat", "*.sh"],
    )
    result = run_review(
        context,
        options=ReviewSessionOptions(
            provider=provider,
            ai_config=AIConfig(
                review=True,
                transport=AITransport.API,
                max_parallel_calls=1,
            ),
            depth=1,
            checklist_items=[],
            checklist_text="",
            classifications=[],
            synthesis=None,
        ),
    )
    assert_that(result.metadata.diff_source).is_equal_to("local-git")
    assert_that(result.metadata.reviewed_paths).is_equal_to(("keep.py",))
    assert_that(result.findings).is_not_empty()
    assert_that(" ".join(seen)).contains("+answer = 42").does_not_contain(
        "diff --git a/binary.dat",
    )
    assert_that(
        json.loads(render_review_json(result=result))["metadata"],
    ).contains_entry(diff_source="local-git")
    reporter = CommentReporter(())
    monkeypatch.setattr(reporter, "fetch_pr_diff_lines", lambda: {})
    assert_that(post_review_to_github(result=result, reporter=reporter)).is_true()
    body = "\n".join(reporter.comments.values())
    assert_that(body).contains(
        "Diff source: `local-git`",
        "Validate the answer",
        "keep.py",
    )
    assert_that(Path(context.repo_root).exists()).is_false()


def test_missing_merge_base_is_fetched_and_verified(
    pr_remote: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A shallow head checkout obtains its absent merge-base object by exact SHA."""
    from lintro.ai.review.context.pr_diff_fallback import _ensure_merge_base

    clone = tmp_path / "base-missing"
    _git(
        tmp_path,
        "clone",
        "--depth",
        "1",
        "--branch",
        "main",
        pr_remote["origin"].as_uri(),
        str(clone),
    )
    monkeypatch.chdir(clone)
    commit = f"{pr_remote['merge_base']}^{{commit}}"
    assert_that(
        _run_git(args=["cat-file", "-e", commit], check=False).returncode,
    ).is_not_equal_to(0)
    _ensure_merge_base(pr_remote["merge_base"])
    assert_that(
        _run_git(args=["rev-parse", "--verify", commit]).stdout.strip(),
    ).is_equal_to(pr_remote["merge_base"])


@pytest.mark.parametrize(
    "code",
    [
        ReviewContextErrorCode.GH_DIFF_SIZE_REFUSED,
        ReviewContextErrorCode.PR_LOCAL_DIFF_FAILED,
    ],
)
def test_mcp_fallback_errors_are_context_execution_failures(
    code: ReviewContextErrorCode,
) -> None:
    """MCP retains fallback error codes and actionable causes in its error envelope."""
    from lintro.mcp.errors import McpError
    from lintro.mcp.toolkits.review import _raise_context_error

    error = ReviewContextError(
        "GitHub refused for size; local fetch failed; retry with git credentials",
        code=code,
    )
    with pytest.raises(McpError) as raised:
        _raise_context_error(exc=error)
    assert_that(str(raised.value)).contains("GitHub refused", "local fetch failed")
    assert_that(raised.value.envelope.detail).contains_entry(
        context_error=code.value,
        kind="context_unavailable",
    )


def test_cli_ceiling_measures_filtered_fallback(github: dict[str, Any]) -> None:
    """Exclusions reduce the fallback before the CLI byte ceiling is enforced."""
    from lintro.ai.review.cli_limits import assert_cli_diff_within_ceiling

    full = collect_review_context(pr_number=1, repo="base/project")
    full_size = len(full.unified_diff.encode("utf-8"))
    remove_pr_head(full.head_worktree)
    selected = collect_review_context(
        pr_number=1,
        repo="base/project",
        exclude_globs=["*.txt", "*.dat", "*.sh"],
    )
    try:
        selected_size = len(selected.unified_diff.encode("utf-8"))
        assert_that(selected_size).is_less_than(full_size)
        assert_cli_diff_within_ceiling(
            context=selected,
            cli_max_diff_bytes=selected_size,
        )
        with pytest.raises(ReviewContextError) as raised:
            assert_cli_diff_within_ceiling(
                context=selected,
                cli_max_diff_bytes=selected_size - 1,
            )
        assert_that(raised.value.code).is_equal_to(
            ReviewContextErrorCode.DIFF_TOO_LARGE,
        )
        assert_that(str(raised.value)).contains("ai.exclude_paths", "--transport api")
    finally:
        remove_pr_head(selected.head_worktree)
