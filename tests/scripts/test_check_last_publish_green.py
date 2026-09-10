# SPDX-License-Identifier: MIT
# For license details, see the repository root LICENSE file.
"""Tests for the release publish gate script (#2516, #2550)."""

from __future__ import annotations

import http.client
import importlib.util
import io
import json
import sys
import urllib.error
import urllib.request
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
    body: str | None = None,
    error: BaseException | None = None,
    argv: list[str] | None = None,
) -> GateRun:
    """Run the gate against a stubbed GitHub API and capture its side effects.

    Args:
        module: The loaded gate module.
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Temporary directory for the Actions output files.
        capsys: Capture fixture for stdout.
        runs: Workflow runs the stubbed API returns.
        body: Raw response body, overriding ``runs`` when given.
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
        if body is not None:
            return body
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


@pytest.mark.parametrize(
    "conclusion",
    [
        "success",
        "failure",
        "cancelled",
        "timed_out",
        "action_required",
    ],
)
def test_completed_non_startup_failure_conclusions_report_green(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    conclusion: str,
) -> None:
    """Only a startup failure gates, so every other conclusion is green.

    ``failure``, ``cancelled`` and ``timed_out`` used to redden the gate. They
    no longer do (#2550): a run that started and then broke is a one-off the
    next tag may well clear, and freezing the release train on it costs more
    than the burned version it saves.

    Args:
        module: The loaded gate module.
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Temporary directory for the Actions output files.
        capsys: Capture fixture for stdout.
        conclusion: Completed-run conclusion under test.
    """
    result = _invoke(
        module=module,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        capsys=capsys,
        runs=[_run_payload(head_branch="v0.152.7", conclusion=conclusion)],
    )

    assert_that(result.code).is_equal_to(0)
    assert_that(result.output).contains("publish_green=true")
    assert_that(result.summary).contains("Publish gate: green")
    assert_that(result.summary).contains("v0.152.7")
    assert_that(result.summary).contains(f"`{conclusion}`")


@pytest.mark.parametrize("status", ["queued", "waiting", "in_progress"])
def test_non_completed_statuses_report_green(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    status: str,
) -> None:
    """A run that has not finished carries no startup failure to gate on.

    Args:
        module: The loaded gate module.
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Temporary directory for the Actions output files.
        capsys: Capture fixture for stdout.
        status: Non-completed run status under test.
    """
    result = _invoke(
        module=module,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        capsys=capsys,
        runs=[
            _run_payload(
                head_branch="v0.152.8",
                status=status,
                conclusion=None,
            ),
        ],
    )

    assert_that(result.code).is_equal_to(0)
    assert_that(result.output).contains("publish_green=true")
    assert_that(result.summary).contains("Publish gate: green")
    assert_that(result.summary).contains(f"`{status}`")


@pytest.mark.parametrize("conclusion", ["skipped", "neutral", "stale"])
def test_remaining_documented_conclusions_report_green(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    conclusion: str,
) -> None:
    """The rarer documented conclusions are known values, and green.

    Args:
        module: The loaded gate module.
        monkeypatch: Pytest monkeypatch fixture.
        tmp_path: Temporary directory for the Actions output files.
        capsys: Capture fixture for stdout.
        conclusion: Completed-run conclusion under test.
    """
    result = _invoke(
        module=module,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        capsys=capsys,
        runs=[_run_payload(head_branch="v0.152.7", conclusion=conclusion)],
    )

    assert_that(result.output).contains("publish_green=true")
    assert_that(result.summary).contains("Publish gate: green")
    assert_that(result.stdout).does_not_contain("::warning")


def test_unknown_conclusion_fails_open_loudly(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A conclusion outside the known set greens the gate, but visibly.

    Exactly one value gates, so a renamed or newly added conclusion would read
    as "not a startup failure" and green the gate forever with no trace. An
    unrecognised value is treated like an API error instead: fail open, and say
    so in the summary and in a ``::warning::`` annotation.
    """
    result = _invoke(
        module=module,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        capsys=capsys,
        runs=[_run_payload(head_branch="v0.152.7", conclusion="bootstrap_failure")],
    )

    assert_that(result.code).is_equal_to(0)
    assert_that(result.output).contains("publish_green=true")
    assert_that(result.summary).contains("Publish gate: not evaluated")
    assert_that(result.summary).contains("`bootstrap_failure`")
    assert_that(result.stdout).contains("::warning title=Publish gate::")
    assert_that(result.stdout).contains("bootstrap_failure")


def test_known_conclusions_cover_the_documented_api_values(module: Any) -> None:
    """The allowlist is the documented set, so drift detection is real."""
    assert_that(set(module._KNOWN_CONCLUSIONS)).is_equal_to(
        {
            "success",
            "failure",
            "cancelled",
            "skipped",
            "timed_out",
            "action_required",
            "neutral",
            "stale",
            "startup_failure",
            "none",
        },
    )


def test_startup_failure_conclusion_skips_the_version_pr(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A startup failure reports red without failing the job."""
    result = _invoke(
        module=module,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        capsys=capsys,
        runs=[_run_payload(head_branch="v0.152.5", conclusion="startup_failure")],
    )

    assert_that(result.code).is_equal_to(0)
    assert_that(result.output).contains("publish_green=false")
    assert_that(result.stdout).contains(RUN_URL)
    assert_that(result.summary).contains("version PR skipped")
    assert_that(result.summary).contains("failed at startup")
    assert_that(result.summary).contains("v0.152.5")
    assert_that(result.summary).contains(RUN_URL)


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
            _run_payload(head_branch="main", conclusion="startup_failure"),
            _run_payload(
                head_branch="tools-candidate-2026",
                conclusion="startup_failure",
            ),
        ],
    )

    assert_that(result.code).is_equal_to(0)
    assert_that(result.output).contains("publish_green=true")
    assert_that(result.summary).contains("no broken publish to gate on")


def test_in_flight_run_hides_an_older_startup_failure(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The newest run wins even when it has not finished (#2550).

    The gate used to skip past in-flight runs to the newest *completed* one.
    It now judges the newest run by ``created_at`` whatever its status, so a
    fresh publish already under way is the verdict and an older startup
    failure no longer blocks the next version PR.
    """
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
                conclusion="startup_failure",
                created_at="2026-09-09T09:00:00Z",
                html_url=OLDER_RUN_URL,
            ),
        ],
    )

    assert_that(result.output).contains("publish_green=true")
    assert_that(result.summary).contains("v0.152.8")
    assert_that(result.summary).does_not_contain("v0.152.7")


def test_newest_startup_failure_wins_over_an_older_success(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Only the newest tag run is consulted, not the best recent one."""
    result = _invoke(
        module=module,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        capsys=capsys,
        runs=[
            _run_payload(
                head_branch="v0.152.7",
                conclusion="success",
                created_at="2026-09-09T09:00:00Z",
                html_url=OLDER_RUN_URL,
            ),
            _run_payload(
                head_branch="v0.152.8",
                conclusion="startup_failure",
                created_at="2026-09-10T09:00:00Z",
            ),
        ],
    )

    assert_that(result.output).contains("publish_green=false")
    assert_that(result.summary).contains("failed at startup")
    assert_that(result.summary).contains("v0.152.8")


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
        runs=[_run_payload(head_branch="v0.152.6", conclusion="startup_failure")],
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
    """The workflow passes a quoted, possibly empty force flag.

    The run stubbed here concluded ``startup_failure``, the one verdict that
    gates (#2550), so an empty word silently read as ``--force`` would show up
    as a green output.
    """
    result = _invoke(
        module=module,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        capsys=capsys,
        runs=[_run_payload(head_branch="v0.152.6", conclusion="startup_failure")],
        argv=[""],
    )

    assert_that(result.code).is_equal_to(0)
    assert_that(result.output).contains("publish_green=false")
    assert_that(result.summary).contains("failed at startup")


def test_unexpected_exception_class_fails_open(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An exception outside the named classes still reports green.

    ``http.client`` errors are neither ``OSError`` nor ``ValueError``, so an
    exception list is not a contract; the boundary has to be total.
    """
    result = _invoke(
        module=module,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        capsys=capsys,
        error=http.client.IncompleteRead(b"partial"),
    )

    assert_that(result.code).is_equal_to(0)
    assert_that(result.output).contains("publish_green=true")
    assert_that(result.summary).contains("IncompleteRead")
    assert_that(result.summary).contains("could not be evaluated")


def test_non_dict_payload_fails_open(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A JSON payload that is not an object reports green, not an error."""
    result = _invoke(
        module=module,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        capsys=capsys,
        body=json.dumps(["not", "an", "object"]),
    )

    assert_that(result.code).is_equal_to(0)
    assert_that(result.output).contains("publish_green=true")
    assert_that(result.summary).contains("Publish gate: not evaluated")
    assert_that(result.summary).contains("expected a top-level JSON object")


def test_missing_workflow_runs_key_fails_open(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A payload without ``workflow_runs`` reports green, not an error."""
    result = _invoke(
        module=module,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        capsys=capsys,
        body=json.dumps({"total_count": 0}),
    )

    assert_that(result.code).is_equal_to(0)
    assert_that(result.output).contains("publish_green=true")
    assert_that(result.summary).contains("workflow_runs missing")
    assert_that(result.summary).contains("could not be evaluated")


def test_fail_open_emits_a_workflow_warning_annotation(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A verdict-less fail-open annotates the run, not just the summary."""
    degraded = _invoke(
        module=module,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        capsys=capsys,
        error=urllib.error.URLError("connection refused"),
    )
    healthy = _invoke(
        module=module,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        capsys=capsys,
        runs=[_run_payload(head_branch="v0.152.7")],
    )

    assert_that(degraded.stdout).contains("::warning title=Publish gate::")
    assert_that(degraded.stdout).contains("failed open")
    assert_that(degraded.stdout).contains("publish-pypi-on-tag.yml")
    # A gate that did reach a verdict must not cry wolf.
    assert_that(healthy.stdout).does_not_contain("::warning")


def test_fetch_text_refuses_non_https_urls(module: Any) -> None:
    """The fetcher may not be pointed at file:// or plain HTTP."""
    assert_that(module.fetch_text).raises(ValueError).when_called_with(
        url="http://api.github.com/repos/lgtm-hq/py-lintro",
    )
    assert_that(module.fetch_text).raises(ValueError).when_called_with(
        url="file:///etc/passwd",
    )


def test_fetch_text_sends_the_token_only_to_the_github_api(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bearer token is attached for api.github.com and nowhere else."""
    seen: list[urllib.request.Request] = []

    def _urlopen(
        request: urllib.request.Request,
        timeout: int | None = None,
    ) -> io.BytesIO:
        seen.append(request)
        return io.BytesIO(b"{}")

    monkeypatch.setenv("GH_TOKEN", "s3cret")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)

    module.fetch_text(url="https://api.github.com/repos/lgtm-hq/py-lintro")
    module.fetch_text(url="https://raw.githubusercontent.com/lgtm-hq/py-lintro/main/x")

    assert_that(seen).is_length(2)
    assert_that(seen[0].get_header("Authorization")).is_equal_to("Bearer s3cret")
    assert_that(seen[1].get_header("Authorization")).is_none()


def test_version_tag_recognition(module: Any) -> None:
    """Only ``v`` plus dot-separated digits counts as a version tag."""
    assert_that(module.is_version_tag(ref="v0.152.7")).is_true()
    assert_that(module.is_version_tag(ref="v1")).is_true()
    assert_that(module.is_version_tag(ref="main")).is_false()
    assert_that(module.is_version_tag(ref="v0.152.7-rc.1")).is_false()
    assert_that(module.is_version_tag(ref="tools-candidate-v1.2.3")).is_false()
