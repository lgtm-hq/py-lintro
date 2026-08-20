"""Integration tests that template_aware hooks into prepare_execution."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.config.template_aware_config import TemplateAwareConfig
from lintro.enums.action import Action
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
        lambda _definition, **_kwargs: None,
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
        lambda _definition, **_kwargs: None,
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
        lambda _definition, **_kwargs: None,
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
        cwd = os.path.abspath(result["cwd"])
        assert_that(cwd).does_not_start_with(tempfile.gettempdir())
        assert_that(cwd).is_equal_to(os.path.abspath(str(tmp_path)))
    finally:
        session = result.get("template_session")
        if session is not None:
            session.cleanup()


def _ruff_definition() -> ToolDefinition:
    """Minimal ruff ToolDefinition for prepare_execution tests.

    Returns:
        ToolDefinition named ruff with ``*.py`` patterns.
    """
    return ToolDefinition(
        name="ruff",
        description="ruff",
        file_patterns=["*.py"],
        default_timeout=30,
    )


def test_prepare_execution_cwd_stays_in_project_with_mixed_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Merged host+temp files still anchor cwd to the project, not ``/``.

    Args:
        tmp_path: Temporary project directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\n")
    (tmp_path / "ok.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "mod.py.jinja").write_text("x = '{{ name }}'\n", encoding="utf-8")

    monkeypatch.setattr(
        "lintro.template_aware.api.get_template_aware_config",
        lambda: TemplateAwareConfig(enabled=True),
    )
    monkeypatch.setattr(
        "lintro.plugins.execution_preparation.verify_tool_version",
        lambda _definition, **_kwargs: None,
    )

    result = prepare_execution(
        paths=[str(tmp_path)],
        options={},
        definition=_ruff_definition(),
        exclude_patterns=[],
        include_venv=False,
        current_options={},
    )

    try:
        files = result["files"]
        assert_that(
            any("lintro-template-aware-" in path for path in files),
        ).is_true()
        cwd = os.path.abspath(result["cwd"])
        assert_that(cwd).is_equal_to(os.path.abspath(str(tmp_path)))
        assert_that(cwd).is_not_equal_to("/")
        rendered_rel = [
            path
            for path in result["rel_files"]
            if "lintro-template-aware-" in path
        ]
        assert_that(rendered_rel).is_not_empty()
        assert_that(os.path.isabs(rendered_rel[0])).is_true()
    finally:
        session = result.get("template_session")
        if session is not None:
            session.cleanup()


def test_prepare_execution_skips_templates_for_fix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fix/format does not stub-render templates or report them as cleaned.

    Args:
        tmp_path: Temporary project directory.
        monkeypatch: Pytest monkeypatch fixture.
    """
    (tmp_path / "ok.py").write_text("x = 1\n", encoding="utf-8")
    template = tmp_path / "mod.py.jinja"
    original = "x = '{{ name }}'\n"
    template.write_text(original, encoding="utf-8")

    monkeypatch.setattr(
        "lintro.template_aware.api.get_template_aware_config",
        lambda: TemplateAwareConfig(enabled=True),
    )
    monkeypatch.setattr(
        "lintro.plugins.execution_preparation.verify_tool_version",
        lambda _definition, **_kwargs: None,
    )

    result = prepare_execution(
        paths=[str(tmp_path)],
        options={},
        definition=_ruff_definition(),
        exclude_patterns=[],
        include_venv=False,
        current_options={},
        action=Action.FIX,
    )

    files = result["files"]
    assert_that(
        any("lintro-template-aware-" in path for path in files),
    ).is_false()
    session = result.get("template_session")
    assert_that(session is None or not session.active).is_true()
    assert_that(template.read_text(encoding="utf-8")).is_equal_to(original)
    if session is not None:
        session.cleanup()
