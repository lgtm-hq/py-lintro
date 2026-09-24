# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""Tests for the CodeRabbit review request on bot-authored PRs (#2726).

The workflow holds the owner's PAT, which can write to every lgtm-hq repo, so
its security shape is pinned here: the base-branch definition
(``pull_request_target``), a Bot-only gate, no PR code on disk, and the secret
reaching only the script. The script itself is driven with a fake ``gh``.
"""

from __future__ import annotations

import os
import subprocess  # nosec B404 - runs the repo's own script with a fixed argv
from pathlib import Path
from typing import Any

import pytest
import yaml
from assertpy import assert_that

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "pr-coderabbit-review-request.yml"
_SCRIPT = _REPO_ROOT / "scripts" / "ci" / "request-coderabbit-review.sh"
_SCRIPT_PATH = "scripts/ci/request-coderabbit-review.sh"

#: Stand-in for the PAT, assembled at runtime so no credential-shaped literal
#: is committed.
_FAKE_TOKEN = "github_pat_" + "coderabbit" + "0" * 30


def _workflow() -> dict[str, Any]:
    """Return the parsed workflow.

    Returns:
        The workflow mapping.
    """
    data: dict[str, Any] = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    return data


def _job() -> dict[str, Any]:
    """Return the workflow's only job.

    Returns:
        The job mapping.
    """
    jobs = _workflow()["jobs"]
    assert_that(jobs).is_length(1)
    job: dict[str, Any] = next(iter(jobs.values()))
    return job


def _steps_using(*, action: str) -> list[dict[str, Any]]:
    """Return the job's steps that use a given action.

    Args:
        action: Action name without a ref, e.g. ``actions/checkout``.

    Returns:
        The matching steps.
    """
    return [
        step
        for step in _job()["steps"]
        if str(step.get("uses", "")).split("@", 1)[0] == action
    ]


# --- the workflow -----------------------------------------------------------


def test_it_runs_from_the_base_definition_on_the_three_pr_events() -> None:
    """Only pull_request_target, so a PR can never edit the job holding the PAT.

    On ``pull_request`` GitHub runs the head's copy of the file with secrets;
    any branch could then drop the Bot gate and read the token.
    """
    triggers = _workflow()["on"]

    assert_that(list(triggers)).is_equal_to(["pull_request_target"])
    assert_that(triggers["pull_request_target"]["types"]).is_equal_to(
        ["opened", "synchronize", "reopened"],
    )


def test_it_fires_only_for_bot_authored_same_repository_pull_requests() -> None:
    """Bot author and a branch of this repository, both required.

    Human-authored PRs are reviewed by CodeRabbit on its own. The Bot check
    alone is not enough: a GitHub App can author a PR from a fork, and that PR
    must never reach the job holding the owner's PAT.
    """
    condition = " ".join(_job()["if"].split())

    assert_that(condition).is_equal_to(
        "github.event.pull_request.user.type == 'Bot' && "
        "github.event.pull_request.head.repo.full_name == github.repository",
    )


def test_a_newer_push_supersedes_a_queued_request() -> None:
    """Per-PR concurrency, as in the repo's other PR workflows."""
    concurrency = _workflow()["concurrency"]

    assert_that(concurrency["group"]).contains("github.event.pull_request.number")
    assert_that(concurrency["cancel-in-progress"]).is_true()


def test_github_token_gets_nothing_beyond_the_checkout() -> None:
    """The comment is posted with the PAT; GITHUB_TOKEN only reads."""
    assert_that(_workflow()["permissions"]).is_equal_to({})
    assert_that(_job()["permissions"]).is_equal_to({"contents": "read"})


def test_the_only_checkout_is_the_base_commit_script_without_credentials() -> None:
    """No PR code is on disk in the job that holds the secret.

    Exactly one checkout: the base commit, sparse to the one script, with no
    persisted credentials. Never the head or the merge ref.
    """
    checkouts = _steps_using(action="actions/checkout")

    assert_that(checkouts).is_length(1)
    options = checkouts[0]["with"]
    assert_that(options["ref"]).is_equal_to(
        "${{ github.event.pull_request.base.sha }}",
    )
    assert_that(options["persist-credentials"]).is_false()
    assert_that(options["sparse-checkout"]).is_equal_to(_SCRIPT_PATH)
    assert_that(options["sparse-checkout-cone-mode"]).is_false()
    assert_that(options["ref"]).does_not_contain("head")
    assert_that(options["ref"]).does_not_contain("merge")


def test_the_secret_reaches_only_the_script_step() -> None:
    """The PAT is in one step's environment, and that step runs the script."""
    holders = [
        step
        for step in _job()["steps"]
        if "CODERABBIT_TRIGGER_TOKEN" in yaml.safe_dump(step)
    ]

    assert_that(holders).is_length(1)
    step = holders[0]
    assert_that(step["env"]["GH_TOKEN"]).is_equal_to(
        "${{ secrets.CODERABBIT_TRIGGER_TOKEN }}",
    )
    assert_that(step["run"]).is_equal_to(_SCRIPT_PATH)
    assert_that(yaml.safe_dump(_workflow().get("env", {}))).does_not_contain(
        "CODERABBIT_TRIGGER_TOKEN",
    )


def test_egress_is_blocked_except_the_fetch_and_the_api() -> None:
    """harden-runner blocks everything but the sparse fetch and the comment."""
    (harden,) = _steps_using(action="step-security/harden-runner")
    options = harden["with"]

    assert_that(options["egress-policy"]).is_equal_to("block")
    assert_that(options["allowed-endpoints"].split()).is_equal_to(
        ["github.com:443", "api.github.com:443"],
    )
    assert_that(_job()["steps"][0]).is_equal_to(harden)


def test_every_action_is_pinned_to_a_full_sha() -> None:
    """Supply-chain rule for the one workflow that holds an org-wide PAT."""
    for step in _job()["steps"]:
        if "uses" not in step:
            continue
        ref = step["uses"].split("@", 1)[1]
        assert_that(ref).matches(r"^[0-9a-f]{40}$")


# --- the script ---------------------------------------------------------------


def _fake_gh(tmp_path: Path, *, exit_code: int = 0) -> tuple[Path, Path]:
    """Install a fake ``gh`` that records its argv and exits as told.

    Args:
        tmp_path: Directory for the fake binary and its log.
        exit_code: Exit status the fake returns.

    Returns:
        The directory to put on ``PATH`` and the argv log file.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)
    log = tmp_path / "gh-calls.log"
    fake = bin_dir / "gh"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$@" >> "{log}"\n'
        'echo "--end--" >> ' + f'"{log}"\n'
        f"exit {exit_code}\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    return bin_dir, log


def _run(
    *,
    tmp_path: Path,
    env: dict[str, str],
    exit_code: int = 0,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Run the script with a fake ``gh`` first on ``PATH``.

    Args:
        tmp_path: Directory for the fake binary.
        env: Script inputs.
        exit_code: Exit status of the fake ``gh``.

    Returns:
        The completed process and the fake's argv log.
    """
    bin_dir, log = _fake_gh(tmp_path, exit_code=exit_code)
    base = {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    result = subprocess.run(  # nosec B603 - fixed argv, the repo's own script
        [str(_SCRIPT)],
        env={**base, **env},
        capture_output=True,
        text=True,
        check=False,
    )
    return result, log


_GOOD_ENV = {"GH_TOKEN": _FAKE_TOKEN, "REPO": "lgtm-hq/py-lintro", "PR_NUMBER": "2726"}


def test_the_request_is_one_comment_with_exactly_the_command(tmp_path: Path) -> None:
    """One call, to the PR's issue comments, with the body CodeRabbit answers.

    Args:
        tmp_path: Temporary directory for the fake ``gh``.
    """
    result, log = _run(tmp_path=tmp_path, env=_GOOD_ENV)

    assert_that(result.returncode).is_equal_to(0)
    calls = log.read_text(encoding="utf-8").split("--end--\n")[:-1]
    assert_that(calls).is_length(1)
    # The whole argv, so an extra argument (an auth header carrying the PAT,
    # say) cannot slip in unnoticed.
    assert_that(calls[0].splitlines()).is_equal_to(
        [
            "api",
            "repos/lgtm-hq/py-lintro/issues/2726/comments",
            "-f",
            "body=@coderabbitai review",
            "--silent",
        ],
    )
    assert_that(log.read_text(encoding="utf-8")).does_not_contain(_FAKE_TOKEN)


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"GH_TOKEN": ""}, "GH_TOKEN is empty"),  # nosec B105 — the empty-secret case
        ({"REPO": "not-a-repo"}, "REPO must be owner/name"),
        ({"PR_NUMBER": "0"}, "PR_NUMBER must be a positive integer"),
        ({"PR_NUMBER": "12; rm -rf /"}, "PR_NUMBER must be a positive integer"),
    ],
)
def test_bad_input_fails_loudly_without_calling_gh(
    tmp_path: Path,
    override: dict[str, str],
    message: str,
) -> None:
    """A missing secret or bad input is a red job, never a silent skip.

    Args:
        tmp_path: Temporary directory for the fake ``gh``.
        override: The input replaced with a bad value.
        message: Text the error must name.
    """
    result, log = _run(tmp_path=tmp_path, env={**_GOOD_ENV, **override})

    assert_that(result.returncode).is_not_equal_to(0)
    assert_that(result.stderr).contains(message)
    assert_that(log.exists()).is_false()


def test_a_failed_post_fails_the_job(tmp_path: Path) -> None:
    """A refused comment (expired PAT, missing scope) must redden the job.

    Args:
        tmp_path: Temporary directory for the fake ``gh``.
    """
    result, _log = _run(tmp_path=tmp_path, env=_GOOD_ENV, exit_code=1)

    assert_that(result.returncode).is_not_equal_to(0)
    assert_that(result.stderr).contains("could not post the CodeRabbit review request")


def test_the_token_never_appears_in_the_output(tmp_path: Path) -> None:
    """Neither the success nor the failure path prints the PAT.

    Args:
        tmp_path: Temporary directory for the fake ``gh``.
    """
    ok, _ = _run(tmp_path=tmp_path / "ok", env=_GOOD_ENV)
    failed, _ = _run(tmp_path=tmp_path / "failed", env=_GOOD_ENV, exit_code=1)

    for result in (ok, failed):
        assert_that(result.stdout + result.stderr).does_not_contain(_FAKE_TOKEN)


def test_the_script_is_executable() -> None:
    """The workflow runs it by path, so it must carry the executable bit."""
    assert_that(os.access(_SCRIPT, os.X_OK)).is_true()
