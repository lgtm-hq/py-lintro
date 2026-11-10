"""Real git remote fixtures and GitHub CLI responses for PR diff tests."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from tests.unit.ai.review.test_pr_head_2733 import _git


@pytest.fixture
def pr_remote(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Create diverged base/head commits and a remote serving the private PR ref."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    _git(tmp_path, "init", "--bare", str(origin))
    _git(tmp_path, "clone", str(origin), str(work))
    for name in ("deleted.txt", "rename.txt", "mode.sh", "quoted\tfile.txt"):
        (work / name).write_text(f"original {name}\n" * 20)
    (work / "binary.dat").write_bytes(b"\x00old")
    _git(work, "add", ".")
    _git(work, "commit", "-m", "base")
    merge_base = _git(work, "rev-parse", "HEAD")
    (work / "deleted.txt").unlink()
    (work / "rename.txt").rename(work / "renamed.txt")
    (work / "mode.sh").chmod(0o755)
    (work / "quoted\tfile.txt").write_text("changed\n")
    (work / "binary.dat").write_bytes(b"\x00new")
    (work / "keep.py").write_text("answer = 42\n")
    _git(work, "add", ".")
    _git(work, "commit", "-m", "head")
    head = _git(work, "rev-parse", "HEAD")
    _git(work, "push", "origin", "HEAD:refs/pull/1/head")
    _git(work, "checkout", "-B", "main", merge_base)
    (work / "base-only.txt").write_text("not part of the pull request\n")
    _git(work, "add", ".")
    _git(work, "commit", "-m", "diverged base")
    base = _git(work, "rev-parse", "HEAD")
    _git(work, "push", "origin", "HEAD:refs/heads/main")
    _git(work, "checkout", "--detach", head)
    monkeypatch.chdir(work)
    return {
        "work": work,
        "origin": origin,
        "merge_base": merge_base,
        "head": head,
        "base": base,
    }


@pytest.fixture
def github(
    pr_remote: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    """Stub gh at the process boundary, retaining real git execution."""
    state = {
        "refusal": "HTTP 406: diff exceeded the maximum number of lines",
        "compare": {"merge_base_commit": {"sha": pr_remote["merge_base"]}},
        "head": pr_remote["head"],
        "owner": "fork",
    }
    original, _ = patch(
        "lintro.ai.review.context.git_ops.subprocess.run",
    ).get_original()

    def run(argv: list[str], **kwargs: Any) -> Any:
        """Serve GitHub data and leave git subprocesses intact."""
        if Path(argv[0]).name != "gh":
            return original(argv, **kwargs)
        if argv[1:3] == ["pr", "view"]:
            payload = {
                "title": "Review change",
                "body": "",
                "number": 1,
                "baseRefOid": pr_remote["base"],
                "headRefOid": state["head"],
                "headRepository": {"nameWithOwner": "fork/project"},
                "headRepositoryOwner": {"login": state["owner"]},
                "changedFiles": 6,
            }
            return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")
        if argv[1:3] == ["pr", "diff"]:
            return SimpleNamespace(
                returncode=1,
                stdout="",
                stderr=str(state["refusal"]),
            )
        endpoint = (
            f"repos/base/project/compare/{pr_remote['base']}...fork:{state['head']}"
        )
        if argv[1:] != ["api", endpoint]:
            raise ValueError(f"Unexpected GitHub request: {argv}")
        if state["compare"] == "http-error":
            return SimpleNamespace(
                returncode=1,
                stdout="",
                stderr="HTTP 403: compare unavailable",
            )
        if state["compare"] == "invalid-json":
            return SimpleNamespace(returncode=0, stdout="not JSON", stderr="")
        if isinstance(state["compare"], Exception):
            raise state["compare"]
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(state["compare"]),
            stderr="",
        )

    monkeypatch.setattr("lintro.ai.review.context.git_ops.subprocess.run", run)
    return state
