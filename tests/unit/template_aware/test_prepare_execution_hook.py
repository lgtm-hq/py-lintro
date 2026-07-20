"""Integration tests that template_aware hooks into prepare_execution."""

from __future__ import annotations

from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.config.template_aware_config import TemplateAwareConfig
from lintro.plugins.execution_preparation import prepare_execution
from lintro.plugins.protocol import ToolDefinition
from lintro.template_aware.prerenderer import SENTINEL_STR


def test_prepare_execution_includes_rendered_templates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When enabled, prepare_execution appends rendered *.py.jinja for ruff."""
    template = tmp_path / "mod.py.jinja"
    template.write_text("x = '{{ name }}'\n", encoding="utf-8")
    # Also include a normal python file so discovery is non-empty either way.
    normal = tmp_path / "ok.py"
    normal.write_text("x = 1\n", encoding="utf-8")

    config = TemplateAwareConfig(enabled=True)
    monkeypatch.setattr(
        "lintro.template_aware.api.get_template_aware_config",
        lambda: config,
    )
    monkeypatch.setattr(
        "lintro.plugins.execution_preparation.verify_tool_version",
        lambda _definition: None,
    )

    definition = ToolDefinition(
        name="ruff",
        description="ruff",
        file_patterns=["*.py"],
        default_timeout=30,
    )

    result = prepare_execution(
        paths=[str(tmp_path)],
        options={},
        definition=definition,
        exclude_patterns=[],
        include_venv=False,
        current_options={},
    )

    try:
        assert_that(result).does_not_contain_key("early_result")
        files = result["files"]
        assert_that(
            any(
                str(normal.resolve()) == path or path.endswith("ok.py")
                for path in files
            ),
        ).is_true()
        rendered = [
            path
            for path in files
            if path.endswith(".py") and "lintro-template-aware-" in path
        ]
        assert_that(rendered).is_not_empty()
        assert_that(Path(rendered[0]).read_text(encoding="utf-8")).contains(
            SENTINEL_STR,
        )
        session = result["template_session"]
        assert_that(session.active).is_true()
    finally:
        session = result.get("template_session")
        if session is not None:
            session.cleanup()


def test_prepare_execution_inert_when_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Disabled template_aware leaves prepare_execution unchanged."""
    template = tmp_path / "mod.py.jinja"
    template.write_text("x = '{{ name }}'\n", encoding="utf-8")
    normal = tmp_path / "ok.py"
    normal.write_text("x = 1\n", encoding="utf-8")

    config = TemplateAwareConfig(enabled=False)
    monkeypatch.setattr(
        "lintro.template_aware.api.get_template_aware_config",
        lambda: config,
    )
    monkeypatch.setattr(
        "lintro.plugins.execution_preparation.verify_tool_version",
        lambda _definition: None,
    )

    definition = ToolDefinition(
        name="ruff",
        description="ruff",
        file_patterns=["*.py"],
        default_timeout=30,
    )

    result = prepare_execution(
        paths=[str(tmp_path)],
        options={},
        definition=definition,
        exclude_patterns=[],
        include_venv=False,
        current_options={},
    )

    files = result["files"]
    assert_that(
        any("lintro-template-aware-" in path for path in files),
    ).is_false()
    session = result.get("template_session")
    assert_that(session is None or not session.active).is_true()
    if session is not None:
        session.cleanup()


def test_prepare_execution_templates_only_still_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only *.py.jinja present still yields files when feature is enabled."""
    template = tmp_path / "only.py.jinja"
    template.write_text("x = '{{ name }}'\n", encoding="utf-8")

    config = TemplateAwareConfig(enabled=True)
    monkeypatch.setattr(
        "lintro.template_aware.api.get_template_aware_config",
        lambda: config,
    )
    monkeypatch.setattr(
        "lintro.plugins.execution_preparation.verify_tool_version",
        lambda _definition: None,
    )

    definition = ToolDefinition(
        name="ruff",
        description="ruff",
        file_patterns=["*.py"],
        default_timeout=30,
    )

    result = prepare_execution(
        paths=[str(tmp_path)],
        options={},
        definition=definition,
        exclude_patterns=[],
        include_venv=False,
        current_options={},
    )

    try:
        assert_that(result).does_not_contain_key("early_result")
        assert_that(result["files"]).is_not_empty()
    finally:
        session = result.get("template_session")
        if session is not None:
            session.cleanup()
