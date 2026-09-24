#!/usr/bin/env python3
"""Decide whether a ``@lintro review`` comment may start a review (#2795).

Runs in the AI Review workflow's ``request`` job on ``issue_comment``. That
job holds no provider credential and no App token: ``GITHUB_TOKEN`` with
``contents: read`` and ``pull-requests: read`` only. This script is the
security boundary for on-request reviews, in this order:

1. The comment must parse as a request (``lintro/ai/review/commands.py``,
   loaded by file path because lintro is not installed in this job).
2. The commenter must have ``admin``, ``maintain`` or ``write`` on the
   repository, read from ``repos/{repo}/collaborators/{login}/permission``.
   Anything else (``read``, ``triage``, ``none``, a 404, an API error, an App
   bot account) refuses the request with a log line and exit 0. There is no
   reply, so an outsider cannot make the bot post.
3. The pull request must be open, not a draft, and from a branch of this
   repository (the automatic review's guard), so fork PRs never run.

Only validated values are written to ``$GITHUB_OUTPUT``: the PR number, the
head SHA, the mode, the path prefixes (JSON), the comment id and the requester
login as returned by the permission API. The comment text itself never leaves
this script.

Environment:
    COMMENT_BODY   The comment text (passed through env, never a template).
    COMMENTER      The comment author's login, as the event reports it.
    COMMENT_ID     The comment's numeric id.
    PR_NUMBER      The issue (pull request) number.
    REPO           owner/name.
    GH_TOKEN       Token for ``gh api``.
    GITHUB_OUTPUT  File the step outputs are appended to.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess  # nosec B404 - fixed argv to the gh CLI
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Final

#: Repository permissions allowed to request a review.
ALLOWED_PERMISSIONS: Final[frozenset[str]] = frozenset({"admin", "maintain", "write"})

#: The ``mode`` output for every refused request (not a command, no write
#: access, not an open same-repository PR). The acknowledge job never runs for
#: it: a refusal is a log line and nothing else.
REFUSED: Final[str] = "refused"

_REPO_RE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_NUMBER_RE: Final[re.Pattern[str]] = re.compile(r"[1-9][0-9]{0,11}")
_LOGIN_RE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")
_SHA_RE: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{40}")

_COMMANDS_PATH: Final[Path] = (
    Path(__file__).resolve().parents[2] / "lintro" / "ai" / "review" / "commands.py"
)


def _commands() -> ModuleType:
    """Load the command parser by path, without importing the lintro package.

    Returns:
        The loaded ``commands`` module.

    Raises:
        RuntimeError: When the module cannot be loaded.
    """
    spec = importlib.util.spec_from_file_location(
        "lintro_review_commands",
        _COMMANDS_PATH,
    )
    if spec is None or spec.loader is None:
        msg = f"cannot load {_COMMANDS_PATH}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _gh_json(path: str) -> Any | None:
    """Call ``gh api`` and return the decoded JSON, or None on any failure.

    Args:
        path: API path, already validated by the caller.

    Returns:
        The decoded response, or None when the call or the decode failed.
    """
    result = subprocess.run(  # nosec B603 B607 - fixed argv, validated path
        ["gh", "api", path],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except ValueError:
        return None


def _write_outputs(values: dict[str, str]) -> None:
    """Append step outputs; every value is single-line by construction.

    Args:
        values: Output names and values.
    """
    output = os.environ.get("GITHUB_OUTPUT")
    lines = "".join(f"{name}={value}\n" for name, value in values.items())
    if output:
        with Path(output).open("a", encoding="utf-8") as handle:
            handle.write(lines)
    else:
        sys.stdout.write(lines)


def _refuse(reason: str) -> int:
    """Record a refused request and exit cleanly.

    Args:
        reason: Why no review runs; logged, never posted.

    Returns:
        The process exit code (always 0: a refusal is not a failure).
    """
    print(f"No on-request review: {reason}")
    _write_outputs({"run": "false", "mode": REFUSED})
    return 0


def resolve(env: dict[str, str]) -> int:
    """Apply the gates in order and write the step outputs.

    Args:
        env: The process environment.

    Returns:
        The process exit code.
    """
    repo = env.get("REPO", "")
    pr_number = env.get("PR_NUMBER", "")
    comment_id = env.get("COMMENT_ID", "")
    commenter = env.get("COMMENTER", "")
    if not _REPO_RE.fullmatch(repo):
        return _refuse("REPO is not owner/name")
    if not (_NUMBER_RE.fullmatch(pr_number) and _NUMBER_RE.fullmatch(comment_id)):
        return _refuse("the PR number or comment id is not a positive integer")

    commands = _commands()
    command = commands.parse_review_command(env.get("COMMENT_BODY", ""))
    if command is None:
        return _refuse("the comment is not a review request")

    # The security boundary: write access, confirmed by the API. A bot login
    # ("name[bot]") fails the login pattern and is never looked up.
    if not _LOGIN_RE.fullmatch(commenter):
        return _refuse("the commenter is not a user account")
    permission = _gh_json(f"repos/{repo}/collaborators/{commenter}/permission")
    if not isinstance(permission, dict):
        return _refuse(f"no permission record for {commenter} (404 or API error)")
    level = str(permission.get("permission", ""))
    login = str((permission.get("user") or {}).get("login", ""))
    print(f"Permission of {commenter}: {level or 'none'}")
    if level not in ALLOWED_PERMISSIONS or not _LOGIN_RE.fullmatch(login):
        return _refuse(f"{commenter} has {level or 'no'} access, not write")

    if command.mode == commands.ReviewRequestMode.USAGE:
        print(f"Malformed request from {login}: {command.problem}")
        _write_outputs(
            {
                "run": "false",
                "mode": str(command.mode),
                "pr-number": pr_number,
                "comment-id": comment_id,
            },
        )
        return 0

    pull = _gh_json(f"repos/{repo}/pulls/{pr_number}")
    if not isinstance(pull, dict):
        return _refuse(f"pull request #{pr_number} could not be read")
    head = pull.get("head") or {}
    head_repo = str((head.get("repo") or {}).get("full_name", ""))
    head_sha = str(head.get("sha", ""))
    if pull.get("state") != "open":
        return _refuse(f"pull request #{pr_number} is not open")
    if pull.get("draft"):
        return _refuse(f"pull request #{pr_number} is a draft")
    if head_repo != repo:
        return _refuse(f"pull request #{pr_number} is not from a branch of {repo}")
    if not _SHA_RE.fullmatch(head_sha):
        return _refuse(f"pull request #{pr_number} has no readable head commit")

    print(
        f"On-request review by {login}: {command.mode} on #{pr_number} "
        f"at {head_sha[:12]}",
    )
    _write_outputs(
        {
            "run": "true",
            "pr-number": pr_number,
            "mode": str(command.mode),
            "paths": json.dumps(list(command.paths), separators=(",", ":")),
            "comment-id": comment_id,
            "requester": login,
        },
    )
    return 0


def main() -> int:
    """Run the gate against the process environment.

    Returns:
        The process exit code.
    """
    return resolve(dict(os.environ))


if __name__ == "__main__":
    sys.exit(main())
