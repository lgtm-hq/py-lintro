"""Tests for classify-osv-results.py."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest
from assertpy import assert_that


def _load_classify_module() -> ModuleType:
    """Load classify-osv-results.py as a test module."""
    script_path = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "ci"
        / "classify-osv-results.py"
    )
    spec = importlib.util.spec_from_file_location("classify_osv_results", script_path)
    if spec is None or spec.loader is None:
        msg = f"Unable to load module from {script_path}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def script_path() -> Path:
    """Return the classify-osv-results.py script path."""
    return (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "ci"
        / "classify-osv-results.py"
    )


def test_classify_ok_when_scan_is_clean() -> None:
    """Clean osv_scanner output maps to ok."""
    module = _load_classify_module()
    payload = {
        "results": [
            {
                "tool": "osv_scanner",
                "success": True,
                "issues_count": 0,
                "parse_failures_count": 0,
            },
        ],
    }

    assert_that(module.classify_osv_results(payload=payload)).is_equal_to(
        module.OsvResultClass.OK,
    )


def test_classify_error_when_success_missing() -> None:
    """Missing success is treated as malformed."""
    module = _load_classify_module()
    payload = {
        "results": [
            {
                "tool": "osv_scanner",
                "issues_count": 0,
                "parse_failures_count": 0,
            },
        ],
    }

    assert_that(module.classify_osv_results(payload=payload)).is_equal_to(
        module.OsvResultClass.ERROR,
    )


def test_classify_error_when_later_osv_entry_fails() -> None:
    """Every osv_scanner entry must pass, not just the first."""
    module = _load_classify_module()
    payload = {
        "results": [
            {
                "tool": "osv_scanner",
                "success": True,
                "issues_count": 0,
                "parse_failures_count": 0,
            },
            {
                "tool": "osv_scanner",
                "success": False,
                "issues_count": 0,
                "parse_failures_count": 0,
            },
        ],
    }

    assert_that(module.classify_osv_results(payload=payload)).is_equal_to(
        module.OsvResultClass.ERROR,
    )


def test_classify_vulns_when_issues_present() -> None:
    """Reported issues map to vulns even when success is false."""
    module = _load_classify_module()
    payload = {
        "results": [
            {
                "tool": "osv_scanner",
                "success": False,
                "issues_count": 2,
                "parse_failures_count": 0,
            },
        ],
    }

    assert_that(module.classify_osv_results(payload=payload)).is_equal_to(
        module.OsvResultClass.VULNS,
    )


def test_classify_error_for_malformed_counts() -> None:
    """Boolean counts are rejected as malformed."""
    module = _load_classify_module()
    payload = {
        "results": [
            {
                "tool": "osv_scanner",
                "success": True,
                "issues_count": True,
                "parse_failures_count": 0,
            },
        ],
    }

    assert_that(module.classify_osv_results(payload=payload)).is_equal_to(
        module.OsvResultClass.ERROR,
    )


def test_classify_error_when_parse_failures_preempt_vulns() -> None:
    """Parse failures map to error even when issues are also reported."""
    module = _load_classify_module()
    payload = {
        "results": [
            {
                "tool": "osv_scanner",
                "success": True,
                "issues_count": 3,
                "parse_failures_count": 1,
            },
        ],
    }

    assert_that(module.classify_osv_results(payload=payload)).is_equal_to(
        module.OsvResultClass.ERROR,
    )


def test_classify_error_when_count_fields_missing() -> None:
    """Missing osv_scanner count fields are treated as malformed."""
    module = _load_classify_module()
    payload = {
        "results": [
            {
                "tool": "osv_scanner",
                "success": True,
                "issues_count": 0,
            },
        ],
    }

    assert_that(module.classify_osv_results(payload=payload)).is_equal_to(
        module.OsvResultClass.ERROR,
    )


def test_classify_error_when_scanner_reports_failure() -> None:
    """Scanner success=false maps to error even with zero counts."""
    module = _load_classify_module()
    payload = {
        "results": [
            {
                "tool": "osv_scanner",
                "success": False,
                "issues_count": 0,
                "parse_failures_count": 0,
            },
        ],
    }

    assert_that(module.classify_osv_results(payload=payload)).is_equal_to(
        module.OsvResultClass.ERROR,
    )


def test_classify_error_when_success_is_non_boolean() -> None:
    """Non-boolean success values are treated as malformed."""
    module = _load_classify_module()
    payload = {
        "results": [
            {
                "tool": "osv_scanner",
                "success": 0,
                "issues_count": 0,
                "parse_failures_count": 0,
            },
        ],
    }

    assert_that(module.classify_osv_results(payload=payload)).is_equal_to(
        module.OsvResultClass.ERROR,
    )


def test_classify_ok_for_write_output_file_json_shape() -> None:
    """File-writer JSON includes fields required by classify-osv-results.py."""
    module = _load_classify_module()
    payload = {
        "action": "check",
        "results": [
            {
                "tool": "osv_scanner",
                "success": True,
                "issues_count": 0,
                "parse_failures_count": 0,
            },
        ],
    }

    assert_that(module.classify_osv_results(payload=payload)).is_equal_to(
        module.OsvResultClass.OK,
    )


def test_script_prints_ok_for_clean_results(
    script_path: Path,
    tmp_path: Path,
) -> None:
    """CLI emits ok for a clean osv_scanner payload."""
    results_path = tmp_path / "osv-results.json"
    results_path.write_text(
        '{"results":[{"tool":"osv_scanner","success":true,"issues_count":0,'
        '"parse_failures_count":0}]}',
        encoding="utf-8",
    )

    completed = subprocess.run(
        [sys.executable, str(script_path), str(results_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert_that(completed.returncode).is_equal_to(0)
    assert_that(completed.stdout.strip()).is_equal_to("ok")


def test_script_prints_vulns_for_reported_issues(
    script_path: Path,
    tmp_path: Path,
) -> None:
    """CLI emits vulns when issues are reported."""
    results_path = tmp_path / "osv-results.json"
    results_path.write_text(
        '{"results":[{"tool":"osv_scanner","success":false,"issues_count":2,'
        '"parse_failures_count":0}]}',
        encoding="utf-8",
    )

    completed = subprocess.run(
        [sys.executable, str(script_path), str(results_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert_that(completed.returncode).is_equal_to(0)
    assert_that(completed.stdout.strip()).is_equal_to("vulns")


def test_script_prints_error_for_parse_failures(
    script_path: Path,
    tmp_path: Path,
) -> None:
    """CLI emits error when osv_scanner reports parse failures."""
    results_path = tmp_path / "osv-results.json"
    results_path.write_text(
        '{"results":[{"tool":"osv_scanner","success":true,"issues_count":0,'
        '"parse_failures_count":1}]}',
        encoding="utf-8",
    )

    completed = subprocess.run(
        [sys.executable, str(script_path), str(results_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert_that(completed.returncode).is_equal_to(0)
    assert_that(completed.stdout.strip()).is_equal_to("error")


def test_script_prints_error_for_invalid_json(
    script_path: Path,
    tmp_path: Path,
) -> None:
    """CLI emits error for unreadable JSON without failing the shell."""
    results_path = tmp_path / "osv-results.json"
    results_path.write_text("{not-json", encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, str(script_path), str(results_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert_that(completed.returncode).is_equal_to(0)
    assert_that(completed.stdout.strip()).is_equal_to("error")
