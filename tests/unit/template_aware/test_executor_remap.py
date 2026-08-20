"""Tests that executor finalize remaps a real ToolResult with metadata."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from assertpy import assert_that

from lintro.config.template_aware_config import TemplateAwareConfig
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.ruff.ruff_issue import RuffIssue
from lintro.template_aware import prepare_templates_for_tool
from lintro.utils.tool_executor import _finalize_template_aware_result


def test_finalize_tool_result_with_metadata_remaps_files(
    tmp_path: Path,
) -> None:
    """Finalize of a real ToolResult with metadata+issues remaps to *.jinja.

    Args:
        tmp_path: Temporary directory containing a Python Jinja template.
    """
    template = tmp_path / "svc.py.jinja"
    template.write_text(
        "# header\nvalue = '{{ project_name }}'\n",
        encoding="utf-8",
    )
    session = prepare_templates_for_tool(
        tool_name="ruff",
        paths=[str(tmp_path)],
        exclude_patterns=[],
        config=TemplateAwareConfig(enabled=True),
    )
    rendered = session.rendered_files[0]
    metadata = {"fixed_count": 0, "verified_count": 1}
    result = ToolResult(
        name="ruff",
        success=False,
        issues_count=1,
        issues=[
            RuffIssue(
                file=rendered,
                line=2,
                end_line=2,
                column=1,
                message="unused",
                code="F401",
            ),
        ],
        metadata=metadata,
        timed_out=False,
        cwd=str(tmp_path),
    )
    tool = SimpleNamespace(_template_aware_session=session)

    finalized = _finalize_template_aware_result(tool=tool, result=result)

    assert_that(finalized.issues).is_length(1)
    assert_that(finalized.issues[0].file).is_equal_to(str(template.resolve()))
    assert_that(finalized.issues[0].line).is_equal_to(2)
    assert_that(finalized.issues[0].end_line).is_equal_to(2)
    assert_that(finalized.metadata).is_equal_to(metadata)
    assert_that(finalized.timed_out).is_false()
    assert_that(hasattr(finalized, "ai_metadata")).is_false()
    assert_that(tool._template_aware_session).is_none()
    assert_that(session.temp_dir).is_none()
