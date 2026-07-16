"""Git, bash, and GitHub CLI subprocess helpers for review context."""

from __future__ import annotations

import shutil
import subprocess  # nosec B404 - subprocess is the core mechanism for invoking external tools; all invocations use shell=False

from lintro.ai.review.enums.review_context_error_code import ReviewContextErrorCode
from lintro.ai.review.exceptions import ReviewContextError

_GIT_GH_TIMEOUT_SECONDS = 120.0

# Shared ``git`` configuration and ``diff`` flags used for the three diff
# snapshot variants. Kept identical across variants so the unified diff,
# name-status, and numstat views share the same normalized formatting.
_DIFF_SNAPSHOT_CONFIG_ARGS: list[str] = [
    "-c",
    "diff.mnemonicPrefix=false",
    "-c",
    "diff.noprefix=false",
    "-c",
    "color.ui=false",
]
_DIFF_SNAPSHOT_FLAGS: list[str] = [
    "-M",
    "--no-color",
    "--no-ext-diff",
    "--no-textconv",
]


def _ensure_git_repo() -> None:
    """Verify the current directory is inside a git repository.

    Raises:
        ReviewContextError: When git is unavailable or not in a repository.
    """
    _resolve_executable(
        command="git",
        code=ReviewContextErrorCode.GIT_UNAVAILABLE,
        message="git is not installed or not on PATH — required for lintro review.",
    )
    result = _run_git(args=["rev-parse", "--git-dir"], check=False)
    if result.returncode != 0:
        raise ReviewContextError(
            "Not a git repository — lintro review requires a git checkout.",
            code=ReviewContextErrorCode.NOT_GIT_REPO,
        )


def _resolve_executable(
    *,
    command: str,
    code: ReviewContextErrorCode,
    message: str,
) -> str:
    """Return an executable path or raise a review context error."""
    executable = shutil.which(command)
    if executable is None:
        raise ReviewContextError(message, code=code)
    return executable


def _run_git(
    *,
    args: list[str],
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a git subprocess and return captured output.

    Args:
        args: Git command arguments (excluding ``git`` itself).
        check: When True, raise on non-zero exit codes.

    Returns:
        Completed process with stdout/stderr captured as text.

    Raises:
        ReviewContextError: When git execution fails and ``check`` is True.
    """
    git_bin = _resolve_executable(
        command="git",
        code=ReviewContextErrorCode.GIT_UNAVAILABLE,
        message="git is not installed or not on PATH — required for lintro review.",
    )
    try:
        result = subprocess.run(  # nosec B603 - argv is [resolved git binary, *args]; shell=False; args are git subcommands only, not user shell input
            [git_bin, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
            check=False,
            timeout=_GIT_GH_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise ReviewContextError(
            f"git {' '.join(args)} timed out after {_GIT_GH_TIMEOUT_SECONDS}s",
            code=ReviewContextErrorCode.GIT_COMMAND_FAILED,
        ) from exc
    except OSError as exc:
        raise ReviewContextError(
            f"Failed to run git {' '.join(args)}: {exc}",
            code=ReviewContextErrorCode.GIT_COMMAND_FAILED,
        ) from exc

    if check and result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip() or "unknown git error"
        raise ReviewContextError(
            f"git {' '.join(args)} failed: {stderr}",
            code=ReviewContextErrorCode.GIT_COMMAND_FAILED,
        )

    return result


def _git_diff_triple_snapshot(*, diff_args: list[str]) -> tuple[str, str, str]:
    """Collect unified diff, name-status, and numstat via three git calls.

    Each ``git diff`` variant runs as its own plain ``subprocess.run`` (via
    :func:`_run_git`). This replaces the previous ``bash -c`` snapshot, dropping
    the hard ``bash`` dependency, the nonce-delimiter collision failure mode,
    and the Windows portability gap in ``lintro review``.

    Each ``git diff`` runs via :func:`_run_git`, which raises
    ``ReviewContextError`` on failure.

    The three calls run back-to-back without yielding; callers must not
    mutate the worktree concurrently during collection (same assumption as the
    prior single-shell snapshot this replaced).

    Args:
        diff_args: Arguments passed to ``git diff`` (for example ``HEAD`` or
            ``merge-base...HEAD``).

    Returns:
        Tuple of ``(unified_diff, name_status, numstat)`` raw stdout payloads.
    """
    base_args = [*_DIFF_SNAPSHOT_CONFIG_ARGS, "diff", *_DIFF_SNAPSHOT_FLAGS]
    unified_diff = _run_git(args=[*base_args, *diff_args]).stdout
    name_status = _run_git(
        args=[*base_args, "--name-status", "-z", *diff_args],
    ).stdout
    numstat = _run_git(
        args=[*base_args, "--numstat", "-z", *diff_args],
    ).stdout
    return unified_diff, name_status, numstat


def _run_gh(*, args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a GitHub CLI subprocess.

    Args:
        args: ``gh`` command arguments (excluding ``gh`` itself).

    Returns:
        Completed process with stdout/stderr captured as text.

    Raises:
        ReviewContextError: When ``gh`` is missing or the command fails.
    """
    gh_bin = _resolve_executable(
        command="gh",
        code=ReviewContextErrorCode.GH_UNAVAILABLE,
        message="GitHub CLI (gh) is not installed — required for --pr review mode.",
    )
    try:
        result = subprocess.run(  # nosec B603 - argv is [resolved gh binary, *args]; shell=False; args are gh subcommands only, not user shell input
            [gh_bin, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
            check=False,
            timeout=_GIT_GH_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise ReviewContextError(
            f"gh {' '.join(args)} timed out after {_GIT_GH_TIMEOUT_SECONDS}s",
            code=ReviewContextErrorCode.GH_COMMAND_FAILED,
        ) from exc
    except OSError as exc:
        raise ReviewContextError(
            f"Failed to run gh {' '.join(args)}: {exc}",
            code=ReviewContextErrorCode.GH_COMMAND_FAILED,
        ) from exc

    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip() or "unknown gh error"
        raise ReviewContextError(
            f"gh {' '.join(args)} failed: {stderr}",
            code=ReviewContextErrorCode.GH_COMMAND_FAILED,
        )

    return result
