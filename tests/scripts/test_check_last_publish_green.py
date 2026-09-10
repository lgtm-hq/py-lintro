# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""Tests for the release publish gate script (#2516)."""

from __future__ import annotations

import importlib.util
import json
import sys
import urllib.error
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that

ROOT = Path(__file__).resolve().parents[2]
GATE_SCRIPT = ROOT / "scripts" / "ci" / "check-last-publish-green.py"

RUN_URL = "https://github.com/lgtm-hq/py-lintro/actions/runs/12345"
OLDER_RUN_URL = "https://github.com/lgtm-hq/py-lintro/actions/runs/12344"


def _load_module() -> Any:
    """Load the hyphenated gate script as an importable module.

    Returns:
        The executed module object.
    """
    spec = importlib.util.spec_from_file_location(
        "check_last_publish_green",
        GATE_SCRIPT,
    )
    assert spec is not None  # narrow type for mypy
    assert spec.loader is not None  # narrow type for mypy
    module = importlib.util.module_from_spec(spec)
    # Register before executing: the module defines a dataclass, and
    # ``dataclasses`` resolves string annotations through ``sys.modules``.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def module() -> Any:
    """Return the loaded gate module.

    Returns:
        The gate script module.
    """
    return _load_module()


def _run_payload(
    *,
    head_branch: str,
    status: str = "completed",
    conclusion: str | None = "success",
    created_at: str = "2026-09-09T12:00:00Z",
    html_url: str = RUN_URL,
) -> dict[str, Any]:
    """Build one stub workflow-run object.

    Args:
        head_branch: Ref the run was triggered for (the tag, for tag pushes).
        status: Run status (``completed``, ``in_progress``, ...).
        conclusion: Run conclusion, or ``None`` while still running.
        created_at: Run creation timestamp.
        html_url: Run URL surfaced in the job summary.

    Returns:
        A workflow-run mapping shaped like the GitHub API response.
    """
    return {
        "head_branch": head_branch,
        "status": status,
        "conclusion": conclusion,
        "created_at": created_at,
        "html_url": html_url,
    }


@dataclass(frozen=True)
class GateRun:
    """Captured result of one gate invocation.

    Attributes:
        code: Process exit code returned by ``main``.
        stdout: Everything the script printed.
        output: Contents of the ``GITHUB_OUTPUT`` file.
        summary: Contents of the ``GITHUB_STEP_SUMMARY`` file.
    """

    code: int
    stdout: str
    output: str
    summary: str


def _invoke(
    *,
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    runs: list[dict[str, Any]] | None = None,
    error: Exception | None = None,
    argv: list[str] | None = None,
) -> GateRun:
    """Run the gate against a stubbed GitHub API and capture its side effects.

    Args:
        module: The loaded gate module.
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Temporary directory for the Actions output files.
        capsys: Capture fixture for stdout.
        runs: Workflow runs the stubbed API returns.
        error: Exception the stubbed fetch raises instead of answering.
        argv: Command-line arguments passed to ``main``.

    Returns:
        The captured gate result.
    """
    output_file = tmp_path / "github_output"
    summary_file = tmp_path / "github_summary"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_file))
    monkeypatch.setenv("GITHUB_REPOSITORY", "lgtm-hq/py-lintro")

    def _fetch(*, url: str) -> str:
        assert_that(url).contains("publish-pypi-on-tag.yml")
        assert_that(url).contains("event=push")
        if error is not None:
            raise error
        return json.dumps({"workflow_runs": runs or []})

    monkeypatch.setattr(module, "fetch_text", _fetch)
    code = module.main(argv if argv is not None else [])
    stdout = capsys.readouterr().out
    return GateRun(
        code=code,
        stdout=stdout,
        output=output_file.read_text(encoding="utf-8"),
        summary=summary_file.read_text(encoding="utf-8"),
    )


def test_successful_tag_publish_reports_green(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A successful last tag publish lets the version PR proceed."""
    result = _invoke(
        module=module,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        capsys=capsys,
        runs=[_run_payload(head_branch="v0.152.7")],
    )

    assert_that(result.code).is_equal_to(0)
    assert_that(result.output).contains("publish_green=true")
    assert_that(result.summary).contains("Publish gate: green")
    assert_that(result.summary).contains("v0.152.7")


def test_failed_tag_publish_skips_the_version_pr(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A failed last tag publish reports red without failing the job."""
    result = _invoke(
        module=module,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        capsys=capsys,
        runs=[_run_payload(head_branch="v0.152.6", conclusion="failure")],
    )

    assert_that(result.code).is_equal_to(0)
    assert_that(result.output).contains("publish_green=false")
    assert_that(result.stdout).contains(RUN_URL)
    assert_that(result.summary).contains("version PR skipped")
    assert_that(result.summary).contains("`failure`")
    assert_that(result.summary).contains(RUN_URL)


def test_startup_failure_conclusion_reports_red(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Any non-success conclusion, including startup_failure, gates."""
    result = _invoke(
        module=module,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        capsys=capsys,
        runs=[_run_payload(head_branch="v0.152.5", conclusion="startup_failure")],
    )

    assert_that(result.output).contains("publish_green=false")
    assert_that(result.summary).contains("`startup_failure`")


def test_no_tag_runs_reports_green(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Branch pushes are not the signal; without tag runs the gate is open."""
    result = _invoke(
        module=module,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        capsys=capsys,
        runs=[
            _run_payload(head_branch="main", conclusion="failure"),
            _run_payload(head_branch="tools-candidate-2026", conclusion="failure"),
        ],
    )

    assert_that(result.code).is_equal_to(0)
    assert_that(result.output).contains("publish_green=true")
    assert_that(result.summary).contains("No completed")


def test_in_flight_run_defers_to_the_last_completed_run(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A newer in-progress run is ignored in favour of the completed one."""
    result = _invoke(
        module=module,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        capsys=capsys,
        runs=[
            _run_payload(
                head_branch="v0.152.8",
                status="in_progress",
                conclusion=None,
                created_at="2026-09-10T09:00:00Z",
            ),
            _run_payload(
                head_branch="v0.152.7",
                created_at="2026-09-09T09:00:00Z",
                html_url=OLDER_RUN_URL,
            ),
        ],
    )

    assert_that(result.output).contains("publish_green=true")
    assert_that(result.summary).contains("v0.152.7")
    assert_that(result.summary).does_not_contain("v0.152.8")


def test_only_in_flight_runs_report_green(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """With nothing completed there is no verdict to gate on."""
    result = _invoke(
        module=module,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        capsys=capsys,
        runs=[
            _run_payload(
                head_branch="v0.152.8",
                status="in_progress",
                conclusion=None,
            ),
        ],
    )

    assert_that(result.output).contains("publish_green=true")
    assert_that(result.summary).contains("no failed publish to gate on")


def test_api_failure_fails_open(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An unreachable API reports green and says the gate was not evaluated."""
    result = _invoke(
        module=module,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        capsys=capsys,
        error=urllib.error.URLError("connection refused"),
    )

    assert_that(result.code).is_equal_to(0)
    assert_that(result.output).contains("publish_green=true")
    assert_that(result.summary).contains("Publish gate: not evaluated")
    assert_that(result.summary).contains("could not be evaluated")


def test_force_reports_green_without_consulting_the_api(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--force`` is the manual override for the first release after a fix."""
    result = _invoke(
        module=module,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        capsys=capsys,
        runs=[_run_payload(head_branch="v0.152.6", conclusion="failure")],
        argv=["--force"],
    )

    assert_that(result.code).is_equal_to(0)
    assert_that(result.output).contains("publish_green=true")
    assert_that(result.summary).contains("Publish gate: forced")
    assert_that(result.summary).does_not_contain(RUN_URL)


def test_empty_flag_word_from_the_workflow_is_ignored(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The workflow passes a quoted, possibly empty force flag."""
    result = _invoke(
        module=module,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        capsys=capsys,
        runs=[_run_payload(head_branch="v0.152.6", conclusion="cancelled")],
        argv=[""],
    )

    assert_that(result.code).is_equal_to(0)
    assert_that(result.output).contains("publish_green=false")
    assert_that(result.summary).contains("`cancelled`")


def test_version_tag_recognition(module: Any) -> None:
    """Only ``v`` plus dot-separated digits counts as a version tag."""
    assert_that(module.is_version_tag(ref="v0.152.7")).is_true()
    assert_that(module.is_version_tag(ref="v1")).is_true()
    assert_that(module.is_version_tag(ref="main")).is_false()
    assert_that(module.is_version_tag(ref="v0.152.7-rc.1")).is_false()
    assert_that(module.is_version_tag(ref="tools-candidate-v1.2.3")).is_false()
