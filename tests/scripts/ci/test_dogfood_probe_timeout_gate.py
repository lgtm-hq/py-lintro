"""Probe-timeout handling in the dogfood no-silent-skip gate (#3383).

A version probe that timed out twice is reported as ``probe_failure:
probe_timeout`` (#2337). The gate absorbs it only when another attempt of the
same run reported a verdict for that tool; otherwise it fails, and
``is-infra-flake-failure.sh`` classifies that failure as infrastructure.
``binary_missing`` rows and rows with no ``probe_failure`` stay fatal.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess  # nosec B404 - fixed script argv in an isolated test
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from assertpy import assert_that

from lintro.enums.action import Action
from lintro.enums.probe_failure import ProbeFailure
from lintro.models.core.tool_outcome import ToolOutcome, Unavailable
from lintro.models.core.tool_result import ToolResult
from lintro.utils.json_output import serialize_tool_result

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CHECKER = _REPO_ROOT / "scripts" / "ci" / "check-dogfood-skips.py"
_INFRA = _REPO_ROOT / "scripts" / "ci" / "is-infra-flake-failure.sh"


def _load_checker() -> ModuleType:
    """Load check-dogfood-skips.py as an importable module.

    Returns:
        The loaded checker module.

    Raises:
        RuntimeError: If the module spec cannot be built.
    """
    spec = importlib.util.spec_from_file_location(
        "check_dogfood_skips_probe_timeout",
        _CHECKER,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {_CHECKER}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


checker = _load_checker()


def _row(result: ToolResult) -> dict[str, Any]:
    """Serialize a tool result exactly as ``lintro chk --output-format json``.

    Args:
        result: The tool result to serialize.

    Returns:
        The JSON report row.
    """
    return serialize_tool_result(result, action=Action.CHECK)


def _probe_timeout(tool: str) -> dict[str, Any]:
    """Build the report row of a version probe that timed out twice.

    Args:
        tool: Tool name.

    Returns:
        An unavailable row carrying ``probe_timeout`` and ``probe_seconds``.
    """
    return _row(
        ToolResult.from_outcome(
            ToolOutcome(
                Unavailable(
                    f"{tool} version check timed out",
                    probe_failure=ProbeFailure.PROBE_TIMEOUT,
                    probe_seconds=120.0,
                ),
            ),
            name=tool,
        ),
    )


def _unavailable(tool: str, probe_failure: ProbeFailure | None) -> dict[str, Any]:
    """Build an unavailable row with an optional non-timeout cause.

    Args:
        tool: Tool name.
        probe_failure: The structured cause, or None for a launch error.

    Returns:
        The unavailable report row.
    """
    return _row(
        ToolResult.from_outcome(
            ToolOutcome(
                Unavailable(
                    f"{tool} could not be launched",
                    probe_failure=probe_failure,
                ),
            ),
            name=tool,
        ),
    )


def _verdict(tool: str, *, issues: int = 0) -> dict[str, Any]:
    """Build the report row of a tool that ran and returned a verdict.

    Args:
        tool: Tool name.
        issues: Issue count; non-zero gives ``status: failed``.

    Returns:
        A row with ``status`` ``ok`` or ``failed``.
    """
    return _row(
        ToolResult(
            name=tool,
            success=issues == 0,
            issues_count=issues,
            output="",
        ),
    )


def _report(*rows: dict[str, Any]) -> dict[str, Any]:
    """Wrap rows in a lintro JSON report envelope.

    Args:
        *rows: Report rows.

    Returns:
        The report document.
    """
    return {"results": list(rows), "summary": {"total_issues": 0}}


def _write(path: Path, payload: dict[str, Any]) -> Path:
    """Write a JSON document and return its path.

    Args:
        path: Destination file.
        payload: Document to write.

    Returns:
        ``path``.
    """
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _run_checker(
    tmp_path: Path,
    *,
    report: dict[str, Any],
    siblings: list[dict[str, Any]],
) -> subprocess.CompletedProcess[str]:
    """Run the checker CLI on a report and sibling reports.

    Args:
        tmp_path: Pytest temporary directory.
        report: The gate's own report.
        siblings: Reports from other attempts of the run.

    Returns:
        The completed checker process.
    """
    allowlist = tmp_path / "allowlist.yaml"
    allowlist.write_text("allowlist: []\n", encoding="utf-8")
    argv = [
        sys.executable,
        str(_CHECKER),
        "--report",
        str(_write(tmp_path / "report.json", report)),
        "--allowlist",
        str(allowlist),
    ]
    for index, sibling in enumerate(siblings):
        sibling_path = _write(tmp_path / f"sibling-{index}.json", sibling)
        argv += ["--sibling-report", str(sibling_path)]
    return subprocess.run(  # nosec B603 - fixed argv, shell=False
        argv,
        capture_output=True,
        text=True,
        check=False,
    )


def _gate_outputs(stdout: str) -> dict[str, str]:
    """Parse the checker's ``gate-output:`` lines.

    Args:
        stdout: Checker standard output.

    Returns:
        The published key/value pairs.
    """
    pairs = (
        line.removeprefix("gate-output: ").split("=", 1)
        for line in stdout.splitlines()
        if line.startswith("gate-output: ")
    )
    return dict(pairs)


@pytest.mark.parametrize("issues", [0, 3], ids=["ok", "failed"])
def test_probe_timeout_with_a_sibling_verdict_passes_and_names_the_timeout(
    tmp_path: Path,
    issues: int,
) -> None:
    """A verdict for the tool in another attempt absorbs its probe timeout."""
    result = _run_checker(
        tmp_path,
        report=_report(_probe_timeout("semgrep"), _verdict("ruff")),
        siblings=[_report(_verdict("semgrep", issues=issues))],
    )

    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stderr).contains(
        "WARN  semgrep: unavailable (probe_timeout after 120s) absorbed",
    )
    assert_that(_gate_outputs(result.stdout)).is_equal_to(
        {"probe-timeout-flake": "false", "probe-timed-out-tools": ""},
    )


@pytest.mark.parametrize(
    "siblings",
    [
        [],
        [_report(_verdict("ruff"))],
        [_report(_probe_timeout("semgrep"))],
        [_report({"tool": "semgrep", "skipped": True, "skip_reason": "x"})],
        [_report({"tool": "semgrep", "success": True, "issues_count": 0})],
    ],
    ids=[
        "no-sibling",
        "other-tool-only",
        "sibling-also-timed-out",
        "sibling-skipped",
        "sibling-without-status",
    ],
)
def test_probe_timeout_without_a_sibling_verdict_fails(
    tmp_path: Path,
    siblings: list[dict[str, Any]],
) -> None:
    """No verdict for the tool elsewhere leaves the timeout fatal."""
    result = _run_checker(
        tmp_path,
        report=_report(_probe_timeout("semgrep")),
        siblings=siblings,
    )

    assert_that(result.returncode).is_equal_to(1)
    assert_that(result.stderr).contains(
        "FAIL  semgrep: unavailable (probe_timeout after 120s)",
        "no sibling verdict for this tool",
    )
    assert_that(_gate_outputs(result.stdout)).is_equal_to(
        {"probe-timeout-flake": "true", "probe-timed-out-tools": "semgrep"},
    )


@pytest.mark.parametrize(
    "probe_failure",
    [ProbeFailure.BINARY_MISSING, None],
    ids=["binary-missing", "no-code"],
)
def test_non_timeout_unavailable_row_fails_despite_a_sibling_verdict(
    tmp_path: Path,
    probe_failure: ProbeFailure | None,
) -> None:
    """Only a probe timeout can be absorbed; other unavailable rows stay fatal."""
    result = _run_checker(
        tmp_path,
        report=_report(_unavailable("gitleaks", probe_failure)),
        siblings=[_report(_verdict("gitleaks"))],
    )

    assert_that(result.returncode).is_equal_to(1)
    assert_that(result.stderr).contains(
        "FAIL  gitleaks: unavailable (binary_missing) with no allowlist entry",
    )
    assert_that(_gate_outputs(result.stdout)).is_equal_to(
        {"probe-timeout-flake": "false", "probe-timed-out-tools": ""},
    )


def test_a_missing_binary_beside_a_probe_timeout_is_not_a_flake(
    tmp_path: Path,
) -> None:
    """One real image failure keeps the whole failure out of the infra class."""
    result = _run_checker(
        tmp_path,
        report=_report(
            _probe_timeout("semgrep"),
            _unavailable("gitleaks", ProbeFailure.BINARY_MISSING),
        ),
        siblings=[],
    )

    assert_that(result.returncode).is_equal_to(1)
    assert_that(_gate_outputs(result.stdout)["probe-timeout-flake"]).is_equal_to(
        "false",
    )


def _run_with_sibling_text(
    tmp_path: Path,
    *,
    report: dict[str, Any],
    sibling_text: str | None,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Run the checker with one sibling given as raw file content.

    Args:
        tmp_path: Pytest temporary directory.
        report: The gate's own report.
        sibling_text: Sibling file content, or None for a file that is absent.

    Returns:
        The completed checker process and the sibling path it was given.
    """
    allowlist = tmp_path / "allowlist.yaml"
    allowlist.write_text("allowlist: []\n", encoding="utf-8")
    sibling = tmp_path / "sibling.json"
    if sibling_text is not None:
        sibling.write_text(sibling_text, encoding="utf-8")
    result = subprocess.run(  # nosec B603 - fixed argv, shell=False
        [
            sys.executable,
            str(_CHECKER),
            "--report",
            str(_write(tmp_path / "report.json", report)),
            "--allowlist",
            str(allowlist),
            "--sibling-report",
            str(sibling),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return result, sibling


_BROKEN_SIBLINGS = pytest.mark.parametrize(
    "sibling_text",
    [None, "", '{"results": [', "null", '{"summary": {}}', '{"results": 3}'],
    ids=["absent", "empty", "truncated", "null", "no-results", "results-not-list"],
)


@_BROKEN_SIBLINGS
def test_broken_sibling_is_never_read_without_a_probe_timeout(
    tmp_path: Path,
    sibling_text: str | None,
) -> None:
    """A clean report passes whatever state its sibling is in."""
    result, _ = _run_with_sibling_text(
        tmp_path,
        report=_report(_verdict("ruff")),
        sibling_text=sibling_text,
    )

    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stderr).does_not_contain("sibling")
    assert_that(_gate_outputs(result.stdout)["probe-timeout-flake"]).is_equal_to(
        "false",
    )


@_BROKEN_SIBLINGS
def test_broken_sibling_is_no_evidence_and_keeps_the_timeout_fatal(
    tmp_path: Path,
    sibling_text: str | None,
) -> None:
    """An unreadable sibling is skipped with a warning naming the file."""
    result, sibling = _run_with_sibling_text(
        tmp_path,
        report=_report(_probe_timeout("semgrep")),
        sibling_text=sibling_text,
    )

    assert_that(result.returncode).is_equal_to(1)
    assert_that(result.stderr).contains(
        f"warning: ignoring sibling report {sibling} (no evidence)",
    )
    assert_that(result.stderr).does_not_contain("failed to read lintro report")
    assert_that(_gate_outputs(result.stdout)).is_equal_to(
        {"probe-timeout-flake": "true", "probe-timed-out-tools": "semgrep"},
    )


# Real ``lintro chk --tools shellcheck --output-format json`` reports, with the
# temporary project path replaced by /workspace. Generated with a stub
# ``shellcheck`` on PATH: one that sleeps on ``--version`` under
# LINTRO_VERSION_TIMEOUT=1 (probe_timeout), one that answers ``--version`` but
# exits 2 with non-JSON output (execution_failed), one that answers
# ``--version`` but hangs under ``shellcheck:timeout=1`` (timed_out), and the
# real binary on a clean script (ok).
_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "dogfood_probe_timeout"


def _fixture(name: str) -> dict[str, Any]:
    """Load one recorded lintro report.

    Args:
        name: Fixture file stem.

    Returns:
        The parsed report.
    """
    loaded: dict[str, Any] = json.loads(
        (_FIXTURES / f"{name}.json").read_text(encoding="utf-8"),
    )
    return loaded


def test_recorded_fixtures_have_the_shapes_the_gate_relies_on() -> None:
    """The recorded rows carry the markers the verdict test reads."""
    rows = {
        name: _fixture(f"shellcheck_{name}")["results"][0]
        for name in ("probe_timeout", "exec_failed", "timed_out", "ok")
    }

    assert_that(rows["probe_timeout"]).contains_entry(
        {"status": "unavailable"},
        {"probe_failure": "probe_timeout"},
    )
    assert_that(rows["exec_failed"]).contains_entry(
        {"status": "failed"},
        {"execution_failed": True},
    )
    assert_that(rows["timed_out"]).contains_entry(
        {"status": "failed"},
        {"timed_out": True},
    )
    assert_that(rows["ok"]).contains_entry({"status": "ok"})


@pytest.mark.parametrize(
    ("sibling", "expected_exit"),
    [
        ("shellcheck_exec_failed", 1),
        ("shellcheck_timed_out", 1),
        ("shellcheck_ok", 0),
    ],
)
def test_only_a_real_verdict_absorbs_a_recorded_probe_timeout(
    tmp_path: Path,
    sibling: str,
    expected_exit: int,
) -> None:
    """A run that crashed or timed out is ``status: failed`` but not a verdict.

    lintro serializes every unsuccessful run as ``failed``; only a row without
    ``execution_failed`` and ``timed_out`` proves the tool answered.
    """
    result = _run_checker(
        tmp_path,
        report=_fixture("shellcheck_probe_timeout"),
        siblings=[_fixture(sibling)],
    )

    assert_that(result.returncode).is_equal_to(expected_exit)
    assert_that(result.stderr).contains("shellcheck: unavailable (probe_timeout after")


def test_sibling_tool_names_match_across_hyphen_and_underscore() -> None:
    """Sibling evidence uses the same tool-name normalisation as the allowlist."""
    findings = checker.evaluate_skips(
        _report(_probe_timeout("osv-scanner")),
        checker.load_allowlist(None),
        sibling_payloads=[_report(_verdict("osv_scanner"))],
    )

    assert_that([(f.tool, f.allowed) for f in findings]).is_equal_to(
        [("osv-scanner", True)],
    )


def _classify(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run is-infra-flake-failure.sh for a failed skip-gate attempt.

    Args:
        env: Extra environment for the classifier.

    Returns:
        The completed classifier process.
    """
    return subprocess.run(  # nosec B603 - fixed argv against a repo script
        [str(_INFRA)],
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ,
            "UPSTREAM_RESULT": "failure",
            "STATUS_OUTPUT": "failed",
            "EXIT_CODE_OUTPUT": "1",
            **env,
        },
    )


def test_infra_classifier_treats_a_probe_timeout_failure_as_infrastructure() -> None:
    """``probe-timeout-flake=true`` from the gate is infrastructure noise."""
    result = _classify(
        {"PROBE_TIMEOUT_FLAKE": "true", "PROBE_TIMED_OUT_TOOLS": "semgrep"},
    )

    assert_that(result.returncode).is_equal_to(0)
    assert_that(result.stdout).contains("Version probe timed out", "semgrep")


@pytest.mark.parametrize("flag", ["", "false", "True", "1", "true "])
def test_infra_classifier_keeps_other_skip_failures_red(flag: str) -> None:
    """Anything but the literal ``true`` leaves a failed gate verdict genuine."""
    result = _classify({"PROBE_TIMEOUT_FLAKE": flag})

    assert_that(result.returncode).is_equal_to(1)
