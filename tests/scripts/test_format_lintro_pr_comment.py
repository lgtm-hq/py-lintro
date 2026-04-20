"""Tests for scripts/ci/format-lintro-pr-comment.py."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from assertpy import assert_that

_MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "ci"
    / "format-lintro-pr-comment.py"
)
_SPEC = importlib.util.spec_from_file_location("format_lintro_pr_comment", _MODULE_PATH)
if _SPEC is None:
    raise ImportError(f"Could not load module spec for {_MODULE_PATH}")
if _SPEC.loader is None:
    raise ImportError(f"Module spec for {_MODULE_PATH} has no loader")
module = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = module
_SPEC.loader.exec_module(module)

_FIXTURE_DIR = (
    Path(__file__).resolve().parents[2] / "test_samples" / "fixtures" / "pr_comments"
)


def _fixture_text(name: str) -> str:
    """Return fixture text for exact comment assertions.

    Args:
        name: Fixture filename.

    Returns:
        Fixture contents.
    """
    return (_FIXTURE_DIR / name).read_text(encoding="utf-8").rstrip("\n")


def _write_report_md(
    path: Path,
    *,
    body: str,
    timestamp: str = "2026-04-17T06:20:00Z",
) -> None:
    """Write a report.md that mirrors OutputManager's Markdown wrapper.

    Args:
        path: File to write.
        body: Console dump to embed inside the fenced block.
        timestamp: Timestamp subline for the header.
    """
    path.write_text(
        f"# Lintro Report\n\n_Generated {timestamp} · run-test_\n\n"
        f"```text\n{body}\n```\n",
        encoding="utf-8",
    )


def test_build_payload_from_report_md_uses_execution_summary(tmp_path: Path) -> None:
    """Prefer the execution summary when it exists in report.md."""
    report_md = tmp_path / "report.md"
    _write_report_md(report_md, body="header\nEXECUTION SUMMARY\n✅ all good")

    payload = module.build_payload_from_report_md(
        report_md_path=report_md,
        exit_code="0",
    )

    assert_that(payload.status).is_equal_to("✅ PASSED")
    assert_that(payload.content).is_equal_to(
        "### 📋 Results:\n```\nEXECUTION SUMMARY\n✅ all good\n```",
    )


def test_build_payload_from_report_md_handles_empty_body(tmp_path: Path) -> None:
    """Report an explicit fallback when report.md body is blank."""
    report_md = tmp_path / "report.md"
    _write_report_md(report_md, body="   \n\n")

    payload = module.build_payload_from_report_md(
        report_md_path=report_md,
        exit_code="0",
    )

    assert_that(payload.status).is_equal_to("⚠️ CHECK LOGS")
    assert_that(payload.content).contains("did not capture any output")


def test_build_payload_from_report_md_accepts_raw_console_dump(tmp_path: Path) -> None:
    """Fall back to the raw file contents when no fenced wrapper is present."""
    report_md = tmp_path / "report.md"
    report_md.write_text(
        "EXECUTION SUMMARY\n✅ all good\n",
        encoding="utf-8",
    )

    payload = module.build_payload_from_report_md(
        report_md_path=report_md,
        exit_code="0",
    )

    assert_that(payload.status).is_equal_to("✅ PASSED")
    assert_that(payload.content).contains("EXECUTION SUMMARY")


def test_markdown_code_block_uses_longer_fence_for_backticks() -> None:
    """Use a fence longer than any backtick run in the content."""
    block = module._markdown_code_block("alpha\n```\nomega")

    assert_that(block).is_equal_to("````\nalpha\n```\nomega\n````")


def test_build_payload_from_report_md_uses_safe_fence_for_backticks(
    tmp_path: Path,
) -> None:
    """Wrap summary safely when it contains a fenced block."""
    report_md = tmp_path / "report.md"
    _write_report_md(
        report_md,
        body="EXECUTION SUMMARY\nLine before\n``danger\nLine after",
    )

    payload = module.build_payload_from_report_md(
        report_md_path=report_md,
        exit_code="0",
    )

    assert_that(payload.status).is_equal_to("✅ PASSED")
    assert_that(payload.content).contains("``danger")


def test_build_payload_from_report_md_flags_fail_rows(tmp_path: Path) -> None:
    """A FAIL row in the summary table must flip status to ISSUES FOUND."""
    report_md = tmp_path / "report.md"
    _write_report_md(
        report_md,
        body="EXECUTION SUMMARY\n| Tool | Status |\n| black | FAIL |",
    )

    payload = module.build_payload_from_report_md(
        report_md_path=report_md,
        exit_code="0",
    )

    assert_that(payload.status).is_equal_to("⚠️ ISSUES FOUND")
    assert_that(payload.content).contains("| black | FAIL |")


def test_build_payload_from_report_md_ignores_header_labels(tmp_path: Path) -> None:
    """Header labels like ``Failed | 0`` must not trip failure detection."""
    report_md = tmp_path / "report.md"
    _write_report_md(
        report_md,
        body=(
            "EXECUTION SUMMARY\n"
            "| Tool | Passed | Failed |\n"
            "| pytest | 42 | 0 |\n"
            "| Total Issues | 0 |"
        ),
    )

    payload = module.build_payload_from_report_md(
        report_md_path=report_md,
        exit_code="0",
    )

    assert_that(payload.status).is_equal_to("✅ PASSED")


def test_build_payload_from_report_md_flags_nonzero_issue_totals(
    tmp_path: Path,
) -> None:
    """Non-zero Total Issues must mark the comment as failing."""
    report_md = tmp_path / "report.md"
    _write_report_md(
        report_md,
        body="EXECUTION SUMMARY\n| Total Issues | 3 |",
    )

    payload = module.build_payload_from_report_md(
        report_md_path=report_md,
        exit_code="0",
    )

    assert_that(payload.status).is_equal_to("⚠️ ISSUES FOUND")


def test_build_payload_from_report_md_honours_nonzero_exit_code(
    tmp_path: Path,
) -> None:
    """A non-zero exit code forces ISSUES FOUND even without markers."""
    report_md = tmp_path / "report.md"
    _write_report_md(report_md, body="EXECUTION SUMMARY\n✅ all good")

    payload = module.build_payload_from_report_md(
        report_md_path=report_md,
        exit_code="1",
    )

    assert_that(payload.status).is_equal_to("⚠️ ISSUES FOUND")


def test_build_unavailable_payload_includes_primary_and_recovery_issues() -> None:
    """Describe both the missing report and recovery failure."""
    payload = module.build_unavailable_payload(
        reason="The lintro run artifact (.lintro/run-*/report.md) was not available in the comment job.",
        details="Ensure the lint job uploaded the lintro-run artifact and that this job downloaded it before invoking ci-pr-comment.sh.",
    )

    assert_that(payload.status).is_equal_to("⚠️ OUTPUT UNAVAILABLE")
    assert_that(payload.content).is_equal_to(
        _fixture_text("lintro-output-unavailable.txt"),
    )
