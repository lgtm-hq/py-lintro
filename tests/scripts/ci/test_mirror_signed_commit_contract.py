# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""Contract tests: the mirror publisher against the real pinned lgtm-ci helper.

The BATS suite stubs lgtm-ci's ``create-signed-commit.sh``, so it pins how
``publish-mirror-release.sh`` calls the helper but not whether the helper at
the pinned commit accepts those flags, prints ``commit-sha=``, or moves the
bump branch the way the publisher assumes (#2835 review). These tests fetch
the helper and the libraries it sources at the exact commit the mirror
workflow checks out, then run the whole publisher against it with only
``gh`` mocked: git runs for real against a local bare "mirror" origin, and
the ``gh`` mock answers only the argv shapes the helper and publisher are
known to send, failing loudly on anything else.

The fetch needs raw.githubusercontent.com (allowed by the Python test jobs'
egress policy); without network the tests skip with the reason.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess  # nosec B404 - drives repo shell scripts with shell=False
import sys
import textwrap
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

import pytest
import yaml
from assertpy import assert_that

ROOT = Path(__file__).resolve().parents[3]
PUBLISH_SCRIPT = ROOT / "scripts" / "ci" / "mirror" / "publish-mirror-release.sh"
MIRROR_WORKFLOW = ROOT / ".github" / "workflows" / "mirror-release.yml"

MIRROR_REPO = "lgtm-hq/lintro-pre-commit"
VERSION = "1.2.3"
BRANCH = f"mirror/bump-lintro-{VERSION}"
CREATED_OID = "c0ffee" + "0" * 34
STALE_HEAD = "5ca1ab1e" + "0" * 32
PR_NUMBER = "31"
OLD_PYPROJECT = (
    '[project]\nname = "lintro-pre-commit"\ndependencies = ["lintro==1.2.2"]\n'
)
NEW_PYPROJECT = OLD_PYPROJECT.replace("lintro==1.2.2", f"lintro=={VERSION}")

# The helper plus every file it sources (directly or through lib/github.sh).
# A new source at a future pin fails the run loudly ("No such file"), which
# is the signal to extend this list.
HELPER_FILES = (
    "scripts/ci/git/create-signed-commit.sh",
    "scripts/ci/lib/log.sh",
    "scripts/ci/lib/github.sh",
    "scripts/ci/lib/github/env.sh",
    "scripts/ci/lib/github/output.sh",
    "scripts/ci/lib/github/summary.sh",
    "scripts/ci/lib/github/format.sh",
)

# Total time the fixture may spend fetching the helper, well under the
# repository's 120 s pytest timeout, so an outage is reported as such.
_FETCH_BUDGET_SECONDS = 60.0
_FETCH_TIMEOUT_SECONDS = 10.0
_FETCH_ATTEMPTS = 3

# Strict gh mock. State (does the bump branch exist on the "server"?) lives
# in a file because each gh call is a fresh process; every call is logged as
# one JSON line, with stdin for the GraphQL call.
GH_MOCK = textwrap.dedent(
    """\
    import json
    import os
    import subprocess
    import sys

    args = sys.argv[1:]
    repo = os.environ["MOCK_REPO"]
    branch = os.environ["MOCK_BRANCH"]
    state_file = os.environ["MOCK_BRANCH_STATE"]
    entry = {"argv": args}
    if args[:2] == ["api", "graphql"]:
        entry["stdin"] = sys.stdin.read()
    with open(os.environ["MOCK_GH_LOG"], "a", encoding="utf-8") as log:
        log.write(json.dumps(entry) + "\\n")


    def branch_head():
        with open(state_file, encoding="utf-8") as handle:
            return handle.read().strip()


    def done(out="", code=0, err=""):
        if out:
            print(out)
        if err:
            print(err, file=sys.stderr)
        sys.exit(code)


    refs = f"repos/{repo}/git/refs"
    bump_ref = f"{refs}/heads/{branch}"
    # --- publisher calls ---
    if args == ["api", f"repos/{repo}", "--jq", ".allow_auto_merge"]:
        done("true")
    if args[:2] == ["pr", "list"]:
        expected = [
            "pr", "list", "--repo", repo, "--head", branch, "--state", "open",
            "--json", "number,baseRefName,mergeStateStatus", "--jq",
        ]
        if args[:-1] != expected:
            done(err=f"unexpected pr list argv: {args}", code=97)
        # Run the publisher's own jq over the mocked API list.
        result = subprocess.run(
            ["jq", "-r", args[-1]],
            input=os.environ.get("MOCK_OPEN_PRS", "[]"),
            capture_output=True,
            text=True,
            check=True,
        )
        sys.stdout.write(result.stdout)
        sys.exit(0)
    if args[:4] == ["pr", "create", "--base", "main"] and args[4:6] == [
        "--head",
        branch,
    ]:
        done(f"https://github.com/{repo}/pull/{os.environ['MOCK_PR_NUMBER']}")
    if args[:2] == ["pr", "view"] and "autoMergeRequest" in args:
        done("false")
    if args[:2] == ["pr", "view"] and "state,mergeStateStatus" in args:
        done("MERGED CLEAN")
    if args[:2] == ["pr", "merge"]:
        done()
    # --- helper and publisher ref calls ---
    if args == ["api", f"repos/{repo}", "--jq", ".default_branch"]:
        done("main")
    if args == ["api", f"repos/{repo}/branches/{branch}", "--jq", ".commit.sha"]:
        head = branch_head()
        if head:
            done(head)
        done(err="gh: Not Found (HTTP 404)", code=1)
    if len(args) == 6 and args[:2] == ["api", refs] and args[2] == "-f":
        if args[3] == f"ref=refs/heads/{branch}":
            with open(state_file, "w", encoding="utf-8") as handle:
                handle.write(args[5].removeprefix("sha="))
        done("{}")
    if args[:4] == ["api", "-X", "PATCH", bump_ref]:
        with open(state_file, "w", encoding="utf-8") as handle:
            handle.write(args[4].removeprefix("sha="))
        done("{}")
    if args[:3] == ["api", "-X", "DELETE"] and args[3].startswith(f"{refs}/heads/"):
        if args[3] == bump_ref:
            with open(state_file, "w", encoding="utf-8") as handle:
                handle.write("")
        done()
    if args == ["api", "graphql", "--input", "-"]:
        error = os.environ.get("MOCK_GRAPHQL_ERROR", "")
        if error:
            done(json.dumps({"errors": [{"message": error}]}), code=1)
        oid = os.environ["MOCK_CREATED_OID"]
        url = f"https://github.com/{repo}/commit/{oid}"
        done(json.dumps({"data": {"createCommitOnBranch": {"commit": {"oid": oid, "url": url}}}}))
    done(err=f"unexpected gh call: {args}", code=97)
    """,
)


def _pinned_lgtm_ci_ref() -> str:
    """Return the lgtm-ci commit the mirror workflow checks the helper out at.

    Read from the workflow rather than hard-coded: this is the exact tooling
    the publish step runs, and
    ``test_all_lgtm_ci_refs_use_the_canonical_pin`` keeps it equal to the
    repo-wide canonical lgtm-ci pin that Renovate maintains.

    Returns:
        The 40-character lgtm-ci commit SHA.
    """
    workflow = yaml.safe_load(MIRROR_WORKFLOW.read_text(encoding="utf-8"))
    checkouts = [
        step["with"]["ref"]
        for step in workflow["jobs"]["mirror-bump"]["steps"]
        if (step.get("with") or {}).get("repository") == "lgtm-hq/lgtm-ci"
    ]
    assert_that(checkouts).is_length(1)
    ref = str(checkouts[0])
    assert_that(ref).matches(r"^[0-9a-f]{40}$")
    return ref


def _helper_unavailable(message: str) -> NoReturn:
    """Fail in CI, skip locally, when the pinned helper cannot be used.

    CI must not pass without running the contract check, so under GitHub
    Actions a missing helper or tool is a failure; on a developer machine
    without network or tools it is a skip.

    Args:
        message: Why the pinned helper is unavailable.
    """
    if os.environ.get("GITHUB_ACTIONS") == "true":
        pytest.fail(f"{message} (required in CI)")
    pytest.skip(message)


def _fetch_helper_file(*, url: str, rel: str, ref: str, deadline: float) -> bytes:
    """Fetch one pinned lgtm-ci file, retrying transient failures.

    Every attempt and backoff stays inside ``deadline`` (a ``time.monotonic``
    value shared by all files), so the fixture reports why the helper is
    unavailable well before pytest's own per-test timeout fires. There is no
    sleep after the final attempt.

    Args:
        url: Raw URL of the file at the pinned ref.
        rel: Repo-relative path, for messages.
        ref: The pinned lgtm-ci ref, for messages.
        deadline: ``time.monotonic()`` value after which no attempt starts.

    Returns:
        The file contents.
    """
    last_error = "fetch budget exhausted before the first attempt"
    for attempt in range(_FETCH_ATTEMPTS):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            response = urllib.request.urlopen(  # nosec B310 - fixed https URL
                url,
                timeout=min(_FETCH_TIMEOUT_SECONDS, remaining),
            )
            with response:
                return bytes(response.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                pytest.fail(f"{rel} is missing at lgtm-ci {ref} (HTTP 404): {url}")
            last_error = f"HTTP {exc.code}"
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = str(exc)
        if attempt + 1 < _FETCH_ATTEMPTS:
            time.sleep(min(2.0 * (attempt + 1), max(0.0, deadline - time.monotonic())))
    _helper_unavailable(f"could not fetch pinned lgtm-ci helper {url}: {last_error}")


@pytest.fixture(scope="module")
def lgtm_ci_tooling(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Fetch the pinned helper and its sourced libraries into a tooling dir.

    Args:
        tmp_path_factory: Pytest factory for a module-scoped temp dir.

    Returns:
        A directory laid out like the workflow's sparse lgtm-ci checkout.
    """
    for tool in ("jq", "git"):
        if shutil.which(tool) is None:
            _helper_unavailable(f"{tool} is not available; the pinned helper needs it")
    ref = _pinned_lgtm_ci_ref()
    tooling = tmp_path_factory.mktemp("lgtm-ci-tooling")
    deadline = time.monotonic() + _FETCH_BUDGET_SECONDS
    for rel in HELPER_FILES:
        url = f"https://raw.githubusercontent.com/lgtm-hq/lgtm-ci/{ref}/{rel}"
        body = _fetch_helper_file(url=url, rel=rel, ref=ref, deadline=deadline)
        target = tooling / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
    return tooling


@dataclass(frozen=True)
class PublishRun:
    """One publisher run: its result, the gh call log and git facts."""

    result: subprocess.CompletedProcess[str]
    calls: list[dict[str, Any]]
    base_oid: str
    origin: Path


def _git(*args: str, cwd: Path, env: dict[str, str]) -> str:
    """Run git with an isolated config and return stdout."""
    return subprocess.run(  # nosec B603 B607 - fixed git argv; shell=False
        ["git", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _run_publisher(
    *,
    tmp_path: Path,
    tooling: Path,
    branch_exists: bool,
    open_prs: list[dict[str, object]] | None = None,
    graphql_error: str = "",
) -> PublishRun:
    """Run publish-mirror-release.sh against the real helper, gh mocked.

    Args:
        tmp_path: Per-test temp dir.
        tooling: The fetched lgtm-ci tooling dir.
        branch_exists: Whether the bump branch is left over from a run.
        open_prs: The API's open PRs for the bump branch head, any base.
        graphql_error: When set, createCommitOnBranch answers this error.

    Returns:
        The run's result, gh calls and the mirror main's commit.
    """
    git_config = tmp_path / "gitconfig"
    git_config.write_text(
        "[user]\n\tname = t\n\temail = t@example.com\n"
        "[commit]\n\tgpgsign = false\n[tag]\n\tgpgsign = false\n"
        "[init]\n\tdefaultBranch = main\n",
        encoding="utf-8",
    )
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("GITHUB_", "GIT_", "COMMIT_", "LGTM_CI_"))
    }
    env["GIT_CONFIG_GLOBAL"] = str(git_config)
    env["GIT_CONFIG_NOSYSTEM"] = "1"

    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    _git("init", "--bare", "-b", "main", str(origin), cwd=tmp_path, env=env)
    _git("init", "-b", "main", str(seed), cwd=tmp_path, env=env)
    (seed / "pyproject.toml").write_text(OLD_PYPROJECT, encoding="utf-8")
    _git("add", "pyproject.toml", cwd=seed, env=env)
    _git("commit", "-q", "-m", "seed", cwd=seed, env=env)
    _git("remote", "add", "origin", str(origin), cwd=seed, env=env)
    _git("push", "-q", "origin", "main", cwd=seed, env=env)
    if branch_exists:
        _git("push", "-q", "origin", f"main:refs/heads/{BRANCH}", cwd=seed, env=env)
    base_oid = _git("rev-parse", "HEAD", cwd=seed, env=env)
    mirror = tmp_path / "mirror"
    _git("clone", "-q", str(origin), str(mirror), cwd=tmp_path, env=env)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(f"#!{sys.executable}\n{GH_MOCK}", encoding="utf-8")
    gh.chmod(0o755)
    gh_log = tmp_path / "gh.log"
    gh_log.touch()
    branch_state = tmp_path / "branch.state"
    branch_state.write_text(STALE_HEAD if branch_exists else "", encoding="utf-8")

    env.update(
        {
            "PATH": f"{bin_dir}{os.pathsep}{env.get('PATH', '')}",
            "GH_TOKEN": "stub",  # nosec B105 - placeholder for the gh mock
            "MIRROR_DIR": str(mirror),
            "MIRROR_REPO": MIRROR_REPO,
            "LGTM_CI_TOOLING_DIR": str(tooling),
            "MERGE_TIMEOUT_SECONDS": "5",
            "MERGE_POLL_SECONDS": "1",
            "MOCK_REPO": MIRROR_REPO,
            "MOCK_BRANCH": BRANCH,
            "MOCK_BRANCH_STATE": str(branch_state),
            "MOCK_GH_LOG": str(gh_log),
            "MOCK_OPEN_PRS": json.dumps(open_prs or []),
            "MOCK_PR_NUMBER": PR_NUMBER,
            "MOCK_CREATED_OID": CREATED_OID,
            "MOCK_GRAPHQL_ERROR": graphql_error,
        },
    )
    result = subprocess.run(  # nosec B603 B607 - fixed argv against repo script
        ["bash", str(PUBLISH_SCRIPT), VERSION],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        cwd=tmp_path,
        timeout=120,
    )
    calls = [
        json.loads(line)
        for line in gh_log.read_text(encoding="utf-8").splitlines()
        if line
    ]
    return PublishRun(result=result, calls=calls, base_oid=base_oid, origin=origin)


def _index(calls: list[dict[str, Any]], predicate: Any) -> list[int]:
    """Return the positions of the calls matching *predicate*."""
    return [i for i, call in enumerate(calls) if predicate(call["argv"])]


def _bump_ref_writes(calls: list[dict[str, Any]]) -> list[list[str]]:
    """Return every call that creates or moves the bump branch."""
    refs = f"repos/{MIRROR_REPO}/git/refs"
    return [
        call["argv"]
        for call in calls
        if (
            call["argv"][:2] == ["api", refs]
            and f"ref=refs/heads/{BRANCH}" in call["argv"]
        )
        or call["argv"][:4] == ["api", "-X", "PATCH", f"{refs}/heads/{BRANCH}"]
    ]


@pytest.mark.parametrize(
    ("branch_exists", "open_prs", "expected_move"),
    [
        pytest.param(False, None, "create", id="fresh-branch"),
        pytest.param(True, [], "patch", id="stale-branch-no-open-pr"),
        pytest.param(
            True,
            [{"number": 21, "baseRefName": "release", "mergeStateStatus": "CLEAN"}],
            "create",
            id="stale-branch-foreign-base-pr",
        ),
    ],
)
def test_publisher_drives_the_pinned_signed_commit_helper(
    lgtm_ci_tooling: Path,
    tmp_path: Path,
    branch_exists: bool,
    open_prs: list[dict[str, object]] | None,
    expected_move: str,
) -> None:
    """The pinned helper accepts the publisher's flags and moves refs as assumed.

    Reset mode commits on a signed-commit-tmp/* branch created at the synced
    main (expectedHeadOid = that base) with the bumped pyproject.toml, then
    points the bump branch at the returned commit — PATCHed when it exists,
    created when it does not, never parked at base — deletes the temp
    branch, and prints the commit-sha= line the publisher parses. A
    foreign-base PR's branch is deleted before the commit, so the helper
    recreates it and the fresh PR targets main.
    """
    run = _run_publisher(
        tmp_path=tmp_path,
        tooling=lgtm_ci_tooling,
        branch_exists=branch_exists,
        open_prs=open_prs,
    )
    output = run.result.stdout + run.result.stderr
    assert_that(run.result.returncode).described_as(output).is_equal_to(0)
    calls = run.calls
    refs = f"repos/{MIRROR_REPO}/git/refs"

    # commit-sha=<oid> reached the publisher's stdout parser.
    assert_that(output).contains(f"Created bump commit {CREATED_OID}")

    # Temp branch at base, then the GraphQL commit on it.
    temp_posts = _index(
        calls,
        lambda a: a[:2] == ["api", refs]
        and len(a) == 6
        and a[3].startswith("ref=refs/heads/signed-commit-tmp/"),
    )
    assert_that(temp_posts).is_length(1)
    temp_post = calls[temp_posts[0]]["argv"]
    temp_branch = temp_post[3].removeprefix("ref=refs/heads/")
    assert_that(temp_post[5]).is_equal_to(f"sha={run.base_oid}")

    graphql = _index(calls, lambda a: a[:2] == ["api", "graphql"])
    assert_that(graphql).is_length(1)
    payload = json.loads(calls[graphql[0]]["stdin"])["variables"]["input"]
    assert_that(payload["branch"]).is_equal_to(
        {"repositoryNameWithOwner": MIRROR_REPO, "branchName": temp_branch},
    )
    assert_that(payload["expectedHeadOid"]).is_equal_to(run.base_oid)
    assert_that(payload["message"]["headline"]).is_equal_to(
        f"chore: bump lintro to {VERSION}",
    )
    assert_that(payload["message"]["body"]).contains(f"v{VERSION}")
    additions = payload["fileChanges"]["additions"]
    assert_that([a["path"] for a in additions]).is_equal_to(["pyproject.toml"])
    assert_that(base64.b64decode(additions[0]["contents"]).decode()).is_equal_to(
        NEW_PYPROJECT,
    )
    assert_that(payload["fileChanges"]).does_not_contain_key("deletions")

    # The bump branch moves exactly once, to the created commit, never to base.
    moves = _bump_ref_writes(calls)
    if expected_move == "patch":
        assert_that(moves).is_equal_to(
            [
                [
                    "api",
                    "-X",
                    "PATCH",
                    f"{refs}/heads/{BRANCH}",
                    "-f",
                    f"sha={CREATED_OID}",
                    "-F",
                    "force=true",
                ],
            ],
        )
    else:
        assert_that(moves).is_equal_to(
            [
                [
                    "api",
                    refs,
                    "-f",
                    f"ref=refs/heads/{BRANCH}",
                    "-f",
                    f"sha={CREATED_OID}",
                ],
            ],
        )
    move_index = _index(calls, lambda a: a in moves)[0]
    temp_delete = _index(
        calls,
        lambda a: a == ["api", "-X", "DELETE", f"{refs}/heads/{temp_branch}"],
    )
    assert_that(temp_delete).is_length(1)
    assert_that(temp_posts[0] < graphql[0] < move_index < temp_delete[0]).is_true()

    # Heal: the foreign-base PR's branch is deleted before the temp branch;
    # a stale branch with no open PR is never deleted before the commit.
    bump_deletes = _index(
        calls,
        lambda a: a == ["api", "-X", "DELETE", f"{refs}/heads/{BRANCH}"],
    )
    early_deletes = [i for i in bump_deletes if i < temp_posts[0]]
    if open_prs:
        assert_that(early_deletes).is_length(1)
    else:
        assert_that(early_deletes).is_empty()

    # One fresh PR, against main, and the release tag lands on the mirror.
    creates = _index(calls, lambda a: a[:2] == ["pr", "create"])
    assert_that(creates).is_length(1)
    assert_that(calls[creates[0]]["argv"][2:6]).is_equal_to(
        ["--base", "main", "--head", BRANCH],
    )
    tags = subprocess.run(  # nosec B603 B607 - fixed git argv; shell=False
        ["git", "--git-dir", str(run.origin), "tag", "--list", f"v{VERSION}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert_that(tags).is_equal_to([f"v{VERSION}"])


def test_pinned_helper_graphql_error_leaves_the_bump_branch_untouched(
    lgtm_ci_tooling: Path,
    tmp_path: Path,
) -> None:
    """A failed createCommitOnBranch never moves the bump branch.

    The helper deletes its temp branch and exits non-zero; the publisher
    stops before opening a PR, and the stale bump branch keeps its head.
    """
    run = _run_publisher(
        tmp_path=tmp_path,
        tooling=lgtm_ci_tooling,
        branch_exists=True,
        graphql_error="Expected branch to point to base but it did not",
    )
    output = run.result.stdout + run.result.stderr
    assert_that(run.result.returncode).described_as(output).is_not_equal_to(0)
    assert_that(output).contains("createCommitOnBranch returned no commit")
    assert_that(output).contains("Expected branch to point to base")
    assert_that(output).does_not_contain("Created bump commit")

    calls = run.calls
    refs = f"repos/{MIRROR_REPO}/git/refs"
    assert_that(_bump_ref_writes(calls)).is_empty()
    assert_that(
        _index(calls, lambda a: a == ["api", "-X", "DELETE", f"{refs}/heads/{BRANCH}"]),
    ).is_empty()
    temp_deletes = _index(
        calls,
        lambda a: a[:3] == ["api", "-X", "DELETE"]
        and a[3].startswith(f"{refs}/heads/signed-commit-tmp/"),
    )
    assert_that(temp_deletes).is_not_empty()
    assert_that(_index(calls, lambda a: a[:2] == ["pr", "create"])).is_empty()
    assert_that(_index(calls, lambda a: a[:2] == ["pr", "merge"])).is_empty()
