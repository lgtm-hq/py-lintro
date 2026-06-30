"""Git, bash, and GitHub CLI subprocess helpers for review context."""

from __future__ import annotations

import secrets
import shlex
import shutil
import subprocess

from lintro.ai.review.enums.review_context_error_code import ReviewContextErrorCode
from lintro.ai.review.exceptions import ReviewContextError

_GIT_GH_TIMEOUT_SECONDS = 120.0


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


def _ensure_bash() -> None:
    """Verify bash is available for combined git diff collection."""
    _resolve_executable(
        command="bash",
        code=ReviewContextErrorCode.BASH_UNAVAILABLE,
        message="bash is not installed or not on PATH — required for lintro review.",
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


def _run_bash(*, script: str) -> subprocess.CompletedProcess[str]:
    """Run a bash script and return captured output.

    Args:
        script: Shell script executed via ``bash -c`` with quoted internal args.

    Returns:
        Completed process with stdout/stderr captured as text.

    Raises:
        ReviewContextError: When bash execution fails or is unavailable.
    """
    bash_bin = _resolve_executable(
        command="bash",
        code=ReviewContextErrorCode.BASH_UNAVAILABLE,
        message="bash is not installed or not on PATH — required for lintro review.",
    )
    try:
        result = subprocess.run(  # nosec B603 - argv is [resolved bash, "-c", script]; script body uses shlex-quoted git refs from _git_diff_triple_snapshot, not caller shell input
            [bash_bin, "-c", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
            check=False,
            timeout=_GIT_GH_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise ReviewContextError(
            f"bash snapshot timed out after {_GIT_GH_TIMEOUT_SECONDS}s",
            code=ReviewContextErrorCode.GIT_COMMAND_FAILED,
        ) from exc
    except OSError as exc:
        raise ReviewContextError(
            f"Failed to run bash snapshot: {exc}",
            code=ReviewContextErrorCode.GIT_COMMAND_FAILED,
        ) from exc

    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip() or "unknown bash error"
        raise ReviewContextError(
            f"git diff snapshot failed: {stderr}",
            code=ReviewContextErrorCode.GIT_COMMAND_FAILED,
        )

    return result


def _git_diff_triple_snapshot(*, diff_args: list[str]) -> tuple[str, str, str]:
    """Collect unified diff, name-status, and numstat in one subprocess.

    The three ``git diff`` variants run inside a single shell script to avoid
    process setup drift while preserving each view's native git formatting.
    Output sections are separated by a random nonce delimiter; if diff content
    ever contained that exact sentinel the snapshot would raise
    ``GIT_OUTPUT_PARSE_FAILED``.

    Args:
        diff_args: Arguments passed to ``git diff`` (for example ``HEAD`` or
            ``merge-base...HEAD``).

    Returns:
        Tuple of ``(unified_diff, name_status, numstat)`` raw stdout payloads.

    Raises:
        ReviewContextError: When the snapshot command fails or returns malformed
            output.
    """
    ref = " ".join(shlex.quote(part) for part in diff_args)
    git_bin = shlex.quote(
        _resolve_executable(
            command="git",
            code=ReviewContextErrorCode.GIT_UNAVAILABLE,
            message="git is not installed or not on PATH — required for lintro review.",
        ),
    )
    delimiter = f"\n---LINTRO_DIFF_SNAP_{secrets.token_hex(16)}---\n"
    delim_shell = delimiter.replace("'", "'\"'\"'")
    git_diff_cfg = (
        "-c diff.mnemonicPrefix=false -c diff.noprefix=false " "-c color.ui=false"
    )
    diff_flags = "-M --no-color --no-ext-diff --no-textconv"
    script = (
        f"set -euo pipefail; "
        f"{git_bin} {git_diff_cfg} diff {diff_flags} {ref}; "
        f"printf '%s' '{delim_shell}'; "
        f"{git_bin} {git_diff_cfg} diff {diff_flags} --name-status -z {ref}; "
        f"printf '%s' '{delim_shell}'; "
        f"{git_bin} {git_diff_cfg} diff {diff_flags} --numstat -z {ref}"
    )
    result = _run_bash(script=script)
    parts = result.stdout.split(delimiter)
    if len(parts) != 3:
        raise ReviewContextError(
            "Failed to parse combined git diff output.",
            code=ReviewContextErrorCode.GIT_OUTPUT_PARSE_FAILED,
        )
    return parts[0], parts[1], parts[2]


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
