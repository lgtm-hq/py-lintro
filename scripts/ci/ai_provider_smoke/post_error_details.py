#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""Quote the provider error text on the deduplicated main-failure issue (#2600).

The shared main-failure notifier (``lgtm-hq/lgtm-ci``) owns the dedup marker,
the issue title and the labels; it opens one issue per workflow key and
comments on it for repeat failures. What it cannot carry is *why* the provider
failed — its body is built from workflow metadata, so a credit-exhausted
provider and a broken integration arrive on the tracker looking identical,
which is the exact defect #2600 exists to fix.

This script closes that gap without introducing a second filer or a second
dedup key: it looks the issue up by the notifier's own deterministic title, and
comments the error text each failing smoke job recorded. No issue is created
here — when the notifier found nothing to open, there is nothing to annotate.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess  # nosec B404 - fixed gh executable, shell=False
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final

#: Title prefix the shared notifier uses. Mirrored, not guessed: the reusable
#: workflow sets FAILURE_TITLE_PREFIX to this string, and the issue title is
#: "<prefix> <branch> (<workflow key>)".
TITLE_PREFIX: Final[str] = "fix(ci): main workflow failed:"


def failure_issue_title(*, workflow_key: str, branch: str) -> str:
    """Return the title the shared notifier gives this workflow's issue.

    Args:
        workflow_key: The notifier's ``workflow-key`` input.
        branch: The branch the failure is reported against.

    Returns:
        The exact issue title.
    """
    return f"{TITLE_PREFIX} {branch} ({workflow_key})"


def _run_gh(args: Sequence[str]) -> str:
    """Run a ``gh`` command and return its stdout.

    Args:
        args: Arguments after the ``gh`` executable.

    Returns:
        The command's stdout.

    Raises:
        RuntimeError: When the GitHub CLI exits unsuccessfully.
    """
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN", "")
    result = subprocess.run(  # nosec B603, B607 - fixed argv, shell=False
        ["gh", *args],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "GH_TOKEN": token},
    )
    if result.returncode != 0:
        msg = result.stderr.strip() or f"gh {' '.join(args)} failed"
        raise RuntimeError(msg)
    return result.stdout


def find_issue_number(*, repo: str, title: str) -> int | None:
    """Return the open failure issue's number, when the notifier opened one.

    Args:
        repo: ``owner/name`` of the repository.
        title: Exact issue title to search for.

    Returns:
        The issue number, or None when no open issue carries that title.
    """
    stdout = _run_gh(
        [
            "issue",
            "list",
            "--repo",
            repo,
            "--state",
            "open",
            "--limit",
            "10",
            "--search",
            f'"{title}" in:title',
            "--json",
            "number,title",
        ],
    )
    for issue in json.loads(stdout or "[]"):
        if issue.get("title") == title:
            return int(issue["number"])
    return None


def collect_error_details(*, errors_dir: Path) -> str:
    """Return the markdown body built from the recorded provider errors.

    Args:
        errors_dir: Directory the failing smoke jobs uploaded their error
            files into; each file is a markdown block naming one provider.

    Returns:
        The comment body, or an empty string when nothing was recorded.
    """
    blocks = [
        path.read_text(encoding="utf-8").strip()
        for path in sorted(errors_dir.rglob("*.md"))
        if path.read_text(encoding="utf-8").strip()
    ]
    return "\n\n".join(blocks)


def build_comment(*, details: str, run_url: str) -> str:
    """Build the comment the tracker issue receives.

    Args:
        details: Per-provider error markdown.
        run_url: URL of the run that produced the failures.

    Returns:
        The comment body.
    """
    return (
        "## Provider API smoke failures\n\n"
        f"{details}\n\n"
        f"Run: {run_url}\n\n"
        "A credit or quota message above means the account needs topping up; "
        "anything else is an integration failure worth a code look.\n"
    )


def main(argv: list[str] | None = None) -> int:
    """Run the script.

    Args:
        argv: Command-line arguments, defaulting to ``sys.argv[1:]``.

    Returns:
        The process exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--errors-dir", required=True)
    parser.add_argument("--workflow-key", required=True)
    parser.add_argument("--branch", default="main")
    args = parser.parse_args(argv)

    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not repo:
        print("::error::GITHUB_REPOSITORY is required")
        return 2

    details = collect_error_details(errors_dir=Path(args.errors_dir))
    if not details:
        print("::notice::no provider error details were recorded; nothing to post")
        return 0

    title = failure_issue_title(workflow_key=args.workflow_key, branch=args.branch)
    number = find_issue_number(repo=repo, title=title)
    if number is None:
        print(f"::warning::no open failure issue titled {title!r}; skipping comment")
        return 0

    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    run_url = f"{server}/{repo}/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}"
    body = build_comment(details=details, run_url=run_url)
    _run_gh(
        ["issue", "comment", str(number), "--repo", repo, "--body", body],
    )
    print(f"commented provider error details on issue #{number}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
