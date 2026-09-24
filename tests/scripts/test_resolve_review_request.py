# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""Tests for the on-request review gate (#2795).

``scripts/ci/resolve_review_request.py`` is the security boundary for
``@lintro review`` comments: write access confirmed by the API, an open,
non-draft, same-repository PR, and only validated values in the step outputs.
It runs here as a subprocess with a fake ``gh`` whose answers each test sets.
"""

from __future__ import annotations

import json
import os
import subprocess  # nosec B404 - runs the repo's own script with a fixed argv
import sys
from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that

_SCRIPT = (
    Path(__file__).resolve().parents[2] / "scripts" / "ci" / "resolve_review_request.py"
)
_REPO = "lgtm-hq/py-lintro"
_HEAD = "a" * 40


def _pull(**overrides: Any) -> dict[str, Any]:
    """Return a pull-request payload that passes every gate.

    Args:
        **overrides: Top-level fields to replace.

    Returns:
        The payload.
    """
    pull: dict[str, Any] = {
        "state": "open",
        "draft": False,
        "head": {"sha": _HEAD, "repo": {"full_name": _REPO}},
    }
    pull.update(overrides)
    return pull


def _permission(level: str, login: str = "octocat") -> dict[str, Any]:
    """Return a collaborator-permission payload.

    Args:
        level: The permission level.
        login: The login the API reports.

    Returns:
        The payload.
    """
    return {"permission": level, "user": {"login": login}}


def _run(
    tmp_path: Path,
    *,
    body: str = "@lintro review",
    commenter: str = "octocat",
    permission: dict[str, Any] | None = None,
    pull: dict[str, Any] | None = None,
) -> tuple[subprocess.CompletedProcess[str], dict[str, str], list[str]]:
    """Run the gate with a fake ``gh``.

    Args:
        tmp_path: Scratch directory.
        body: The comment text.
        commenter: The event's commenter login.
        permission: The permission API's answer; ``None`` means a 404.
        pull: The pulls API's answer; ``None`` means a 404.

    Returns:
        The process, the parsed step outputs, and the ``gh`` calls made.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (tmp_path / "permission.json").write_text(
        json.dumps(permission) if permission is not None else "",
        encoding="utf-8",
    )
    (tmp_path / "pull.json").write_text(
        json.dumps(pull) if pull is not None else "",
        encoding="utf-8",
    )
    calls = tmp_path / "calls.log"
    fake = bin_dir / "gh"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$2" >> "{calls}"\n'
        'case "$2" in\n'
        f'  */collaborators/*/permission) f="{tmp_path}/permission.json" ;;\n'
        f'  */pulls/*) f="{tmp_path}/pull.json" ;;\n'
        "  *) exit 1 ;;\n"
        "esac\n"
        '[[ -s "$f" ]] || { echo "HTTP 404" >&2; exit 1; }\n'
        'cat "$f"\n',
        encoding="utf-8",
    )
    fake.chmod(0o755)
    output = tmp_path / "github-output"
    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "COMMENT_BODY": body,
        "COMMENTER": commenter,
        "COMMENT_ID": "987654321",
        "PR_NUMBER": "2795",
        "REPO": _REPO,
        "GH_TOKEN": "fake",  # nosec B105 - the fake gh ignores it
        "GITHUB_OUTPUT": str(output),
    }
    result = subprocess.run(  # nosec B603 - fixed argv, the repo's own script
        [sys.executable, str(_SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    outputs = dict(
        line.split("=", 1)
        for line in (
            output.read_text(encoding="utf-8").splitlines() if output.exists() else []
        )
    )
    made = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
    return result, outputs, made


@pytest.mark.parametrize("level", ["admin", "maintain", "write"])
def test_a_writer_on_an_open_pr_gets_a_review(tmp_path: Path, level: str) -> None:
    """Write access and an open same-repository PR run the review.

    Args:
        tmp_path: Scratch directory.
        level: An accepted permission level.
    """
    result, outputs, _ = _run(tmp_path, permission=_permission(level), pull=_pull())

    assert_that(result.returncode).is_equal_to(0)
    assert_that(outputs).is_equal_to(
        {
            "run": "true",
            "pr-number": "2795",
            "head-sha": _HEAD,
            "mode": "full",
            "paths": "[]",
            "comment-id": "987654321",
            "requester": "octocat",
        },
    )


def test_the_mode_and_paths_are_passed_on(tmp_path: Path) -> None:
    """A targeted request carries its prefixes as a JSON list.

    Args:
        tmp_path: Scratch directory.
    """
    _, outputs, _ = _run(
        tmp_path,
        body="@lintro review lintro/ai docs",
        permission=_permission("write"),
        pull=_pull(),
    )

    assert_that(outputs["mode"]).is_equal_to("paths")
    assert_that(json.loads(outputs["paths"])).is_equal_to(["lintro/ai", "docs"])


def test_the_requester_is_the_login_the_api_confirmed(tmp_path: Path) -> None:
    """The requester output is the API's login, not the event's field.

    Args:
        tmp_path: Scratch directory.
    """
    _, outputs, _ = _run(
        tmp_path,
        commenter="octocat",
        permission=_permission("write", login="OctoCat"),
        pull=_pull(),
    )

    assert_that(outputs["requester"]).is_equal_to("OctoCat")


@pytest.mark.parametrize(
    "permission",
    [
        _permission("read"),
        _permission("triage"),
        _permission("none"),
        {"permission": "write"},
        None,
    ],
    ids=["read", "triage", "none", "no-login", "not-found"],
)
def test_without_write_access_nothing_runs_and_nothing_is_posted(
    tmp_path: Path,
    permission: dict[str, Any] | None,
) -> None:
    """Refused requests exit 0, run nothing, and never read the PR.

    Args:
        tmp_path: Scratch directory.
        permission: The permission API's answer.
    """
    result, outputs, calls = _run(tmp_path, permission=permission, pull=_pull())

    assert_that(result.returncode).is_equal_to(0)
    assert_that(outputs).is_equal_to({"run": "false", "mode": "refused"})
    assert_that([call for call in calls if "/pulls/" in call]).is_empty()


def test_a_bot_commenter_is_refused_without_an_api_call(tmp_path: Path) -> None:
    """An App bot login fails the login check and is never looked up.

    Args:
        tmp_path: Scratch directory.
    """
    result, outputs, calls = _run(
        tmp_path,
        commenter="lintro-review[bot]",
        permission=_permission("write"),
        pull=_pull(),
    )

    assert_that(result.returncode).is_equal_to(0)
    assert_that(outputs["mode"]).is_equal_to("refused")
    assert_that(calls).is_empty()


@pytest.mark.parametrize(
    "pull",
    [
        _pull(state="closed"),
        _pull(draft=True),
        _pull(head={"sha": _HEAD, "repo": {"full_name": "someone/fork"}}),
        _pull(head={"sha": "not-a-sha", "repo": {"full_name": _REPO}}),
        None,
    ],
    ids=["closed", "draft", "fork", "bad-head", "not-found"],
)
def test_only_an_open_same_repository_pr_is_reviewed(
    tmp_path: Path,
    pull: dict[str, Any] | None,
) -> None:
    """Closed, draft and fork PRs are refused like the automatic review.

    Args:
        tmp_path: Scratch directory.
        pull: The pulls API's answer.
    """
    result, outputs, _ = _run(tmp_path, permission=_permission("write"), pull=pull)

    assert_that(result.returncode).is_equal_to(0)
    assert_that(outputs).is_equal_to({"run": "false", "mode": "refused"})


def test_a_comment_that_is_not_a_request_makes_no_api_call(tmp_path: Path) -> None:
    """The parser runs before any API call.

    Args:
        tmp_path: Scratch directory.
    """
    result, outputs, calls = _run(
        tmp_path,
        body="@lintro reviewer please",
        permission=_permission("write"),
        pull=_pull(),
    )

    assert_that(result.returncode).is_equal_to(0)
    assert_that(outputs["mode"]).is_equal_to("refused")
    assert_that(calls).is_empty()


def test_a_writers_malformed_request_asks_for_the_usage_text(tmp_path: Path) -> None:
    """Usage is only for writers, and the comment text never reaches the outputs.

    Args:
        tmp_path: Scratch directory.
    """
    result, outputs, _ = _run(
        tmp_path,
        body="@lintro review src/*.py",
        permission=_permission("write"),
        pull=_pull(),
    )

    assert_that(result.returncode).is_equal_to(0)
    assert_that(outputs).is_equal_to(
        {
            "run": "false",
            "mode": "usage",
            "pr-number": "2795",
            "comment-id": "987654321",
        },
    )
    assert_that(" ".join(outputs.values())).does_not_contain("src/")


def test_a_readers_malformed_request_is_refused_not_answered(tmp_path: Path) -> None:
    """An outsider cannot make the bot post the usage text.

    Args:
        tmp_path: Scratch directory.
    """
    _, outputs, _ = _run(
        tmp_path,
        body="@lintro review src/*.py",
        permission=_permission("read"),
        pull=_pull(),
    )

    assert_that(outputs["mode"]).is_equal_to("refused")


def test_every_output_is_a_single_line(tmp_path: Path) -> None:
    """A multi-line comment cannot inject extra step outputs.

    Args:
        tmp_path: Scratch directory.
    """
    _, outputs, _ = _run(
        tmp_path,
        body="@lintro review delta\nrun=true\nmode=full",
        permission=_permission("write"),
        pull=_pull(),
    )

    # Read the raw file: a dict would hide an injected duplicate key.
    raw = (tmp_path / "github-output").read_text(encoding="utf-8").splitlines()
    keys = [line.split("=", 1)[0] for line in raw]
    assert_that(keys).does_not_contain_duplicates()
    assert_that(keys).is_equal_to(
        ["run", "pr-number", "head-sha", "mode", "paths", "comment-id", "requester"],
    )
    assert_that(outputs["mode"]).is_equal_to("delta")
