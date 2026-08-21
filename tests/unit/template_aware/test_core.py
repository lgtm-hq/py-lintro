"""Unit tests for template-aware source maps, routing, and rendering."""

from __future__ import annotations

from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.config.template_aware_config import (
    StubStrategy,
    TemplateAwareConfig,
    TemplateEngine,
)
from lintro.parsers.base_issue import BaseIssue
from lintro.template_aware import (
    build_source_map,
    prepare_templates_for_tool,
    translate_issue,
    translate_issues,
)
from lintro.template_aware.prerenderer import (
    SENTINEL_INT,
    SENTINEL_STR,
    render_template,
)
from lintro.template_aware.router import (
    patterns_for_tool,
    resolve_tool_for_path,
)


def test_source_map_round_trip_equal_line_counts() -> None:
    """Equal-length original/rendered texts map 1:1."""
    original = "a = 1\nb = 2\nc = 3\n"
    rendered = "a = 1\nb = 2\nc = 3\n"

    source_map = build_source_map(
        original_text=original,
        rendered_text=rendered,
        original_path="/tmp/main.py.jinja",
        rendered_path="/tmp/main.py",
    )

    assert_that(source_map.lookup_line(1)).is_equal_to(1)
    assert_that(source_map.lookup_line(2)).is_equal_to(2)
    assert_that(source_map.lookup_line(3)).is_equal_to(3)


def test_source_map_handles_expanded_control_flow() -> None:
    """Inserted rendered lines map back to a nearby original line."""
    original = "{% if true %}\nx = 1\n{% endif %}\n"
    rendered = "x = 1\n"

    source_map = build_source_map(
        original_text=original,
        rendered_text=rendered,
        original_path="/tmp/t.py.jinja",
        rendered_path="/tmp/t.py",
    )

    assert_that(source_map.lookup_line(1)).is_greater_than(0)


def test_translate_issue_rewrites_file_and_line(tmp_path: Path) -> None:
    """Translator rewrites rendered path/line onto the original template."""
    original = tmp_path / "main.py.jinja"
    rendered = tmp_path / "main.py"
    original.write_text("x = {{ name }}\n", encoding="utf-8")
    rendered.write_text(f"x = {SENTINEL_STR}\n", encoding="utf-8")

    source_map = build_source_map(
        original_text=original.read_text(encoding="utf-8"),
        rendered_text=rendered.read_text(encoding="utf-8"),
        original_path=str(original.resolve()),
        rendered_path=str(rendered.resolve()),
    )
    issue = BaseIssue(
        file=str(rendered.resolve()),
        line=1,
        column=1,
        message="demo",
    )

    translated = translate_issue(
        issue=issue,
        source_maps={str(rendered.resolve()): source_map},
    )

    assert_that(translated.file).is_equal_to(str(original.resolve()))
    assert_that(translated.line).is_equal_to(1)


def test_translate_issues_noop_without_maps() -> None:
    """Empty source maps leave issues unchanged."""
    issue = BaseIssue(file="a.py", line=3, message="x")
    result = translate_issues(issues=[issue], source_maps={})

    assert_that(result[0].file).is_equal_to("a.py")
    assert_that(result[0].line).is_equal_to(3)


def test_router_resolves_py_jinja_to_ruff() -> None:
    """Default route maps *.py.jinja to ruff."""
    config = TemplateAwareConfig()
    tool = resolve_tool_for_path(path="pkg/main.py.jinja", config=config)

    assert_that(tool).is_equal_to("ruff")
    assert_that(patterns_for_tool(tool_name="ruff", config=config)).contains(
        "*.py.jinja",
    )


def test_sentinel_render_replaces_placeholders(tmp_path: Path) -> None:
    """Sentinel strategy replaces {{ var }} with type-stable tokens."""
    template = tmp_path / "app.py.jinja"
    template.write_text(
        "name = '{{ project_name }}'\nport = {{ port }}\n",
        encoding="utf-8",
    )
    config = TemplateAwareConfig(
        enabled=True,
        stub_strategy=StubStrategy.SENTINEL,
    )

    rendered, _source_map = render_template(template_path=template, config=config)

    assert_that(rendered).contains(SENTINEL_STR)
    assert_that(rendered).contains(SENTINEL_INT)
    assert_that(rendered).does_not_contain("{{")


def test_sentinel_keeps_if_true_branch(tmp_path: Path) -> None:
    """Sentinel undefined is truthy so {% if %} keeps the true branch."""
    template = tmp_path / "app.py.jinja"
    template.write_text(
        "{% if feature %}\nenabled = True\n{% else %}\nenabled = False\n{% endif %}\n",
        encoding="utf-8",
    )
    config = TemplateAwareConfig(enabled=True, stub_strategy=StubStrategy.SENTINEL)

    rendered, _source_map = render_template(template_path=template, config=config)

    assert_that(rendered).contains("enabled = True")
    assert_that(rendered).does_not_contain("enabled = False")


def test_render_template_swallows_runtime_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TemplateRuntimeError falls back to original text instead of crashing."""
    from jinja2 import Template
    from jinja2.exceptions import TemplateRuntimeError

    template = tmp_path / "bad.py.jinja"
    original = "VALUE = '{{ project_name }}'\n"
    template.write_text(original, encoding="utf-8")
    config = TemplateAwareConfig(enabled=True, stub_strategy=StubStrategy.SENTINEL)

    def _boom(self: Template, *args: object, **kwargs: object) -> str:
        raise TemplateRuntimeError("simulated runtime failure")

    monkeypatch.setattr(Template, "render", _boom)

    rendered, source_map = render_template(template_path=template, config=config)

    assert_that(rendered).is_equal_to(original)
    assert_that(source_map.lookup_line(1)).is_equal_to(1)


def test_render_template_swallows_missing_include(tmp_path: Path) -> None:
    """Missing {% include %} (TemplateError) degrades to original text."""
    template = tmp_path / "inc.py.jinja"
    original = "{% include 'missing-fragment.j2' %}\nx = 1\n"
    template.write_text(original, encoding="utf-8")
    config = TemplateAwareConfig(enabled=True, stub_strategy=StubStrategy.SENTINEL)

    rendered, _source_map = render_template(template_path=template, config=config)

    assert_that(rendered).is_equal_to(original)


def test_rendered_filename_strips_jinja_without_doubling() -> None:
    """Custom ``*.rs.jinja`` names keep a single host extension."""
    from lintro.template_aware.prerenderer import rendered_filename_for

    assert_that(rendered_filename_for(Path("main.rs.jinja"))).is_equal_to("main.rs")
    assert_that(rendered_filename_for(Path("app.py.jinja"))).is_equal_to("app.py")


def test_defaults_strategy_reads_copier_yml(tmp_path: Path) -> None:
    """Defaults strategy loads values from copier.yml."""
    (tmp_path / "copier.yml").write_text(
        "project_name:\n  type: str\n  default: Acme\n",
        encoding="utf-8",
    )
    template = tmp_path / "main.py.jinja"
    template.write_text("NAME = '{{ project_name }}'\n", encoding="utf-8")
    config = TemplateAwareConfig(
        enabled=True,
        engine=TemplateEngine.COPIER,
        stub_strategy=StubStrategy.DEFAULTS,
    )

    rendered, _source_map = render_template(template_path=template, config=config)

    assert_that(rendered).contains("Acme")


def test_cookiecutter_defaults_wrap_namespace(tmp_path: Path) -> None:
    """cookiecutter.json is exposed as ``{{ cookiecutter.project_slug }}``.

    Args:
        tmp_path: Temporary cookiecutter-style project.
    """
    (tmp_path / "cookiecutter.json").write_text(
        '{"project_slug": "cookie_demo", "author": "Ada"}\n',
        encoding="utf-8",
    )
    template = tmp_path / "main.py.jinja"
    template.write_text(
        "NAME = '{{ cookiecutter.project_slug }}'\n",
        encoding="utf-8",
    )
    config = TemplateAwareConfig(
        enabled=True,
        engine=TemplateEngine.COOKIECUTTER,
        stub_strategy=StubStrategy.DEFAULTS,
    )

    rendered, _source_map = render_template(template_path=template, config=config)

    assert_that(rendered).contains("cookie_demo")
    assert_that(rendered).does_not_contain("{{")


def test_context_file_strategy(tmp_path: Path) -> None:
    """Context-file strategy uses the supplied answers file."""
    answers = tmp_path / "answers.yml"
    answers.write_text("project_name: FromAnswers\n", encoding="utf-8")
    template = tmp_path / "main.py.jinja"
    template.write_text("NAME = '{{ project_name }}'\n", encoding="utf-8")
    config = TemplateAwareConfig(
        enabled=True,
        stub_strategy=StubStrategy.CONTEXT_FILE,
        context_file=str(answers),
    )

    rendered, _source_map = render_template(template_path=template, config=config)

    assert_that(rendered).contains("FromAnswers")


def test_prepare_templates_inert_when_disabled(tmp_path: Path) -> None:
    """prepare_templates_for_tool is a no-op when enabled is false."""
    template = tmp_path / "main.py.jinja"
    template.write_text("x = {{ v }}\n", encoding="utf-8")
    config = TemplateAwareConfig(enabled=False)

    session = prepare_templates_for_tool(
        tool_name="ruff",
        paths=[str(tmp_path)],
        exclude_patterns=[],
        config=config,
    )

    assert_that(session.active).is_false()
    assert_that(session.rendered_files).is_empty()


def test_prepare_templates_for_ruff_renders_py_jinja(tmp_path: Path) -> None:
    """Enabled session renders *.py.jinja for the ruff route."""
    template = tmp_path / "main.py.jinja"
    template.write_text("x = '{{ name }}'\n", encoding="utf-8")
    config = TemplateAwareConfig(enabled=True)

    session = prepare_templates_for_tool(
        tool_name="ruff",
        paths=[str(tmp_path)],
        exclude_patterns=[],
        config=config,
    )

    try:
        assert_that(session.active).is_true()
        assert_that(session.rendered_files).is_length(1)
        rendered_path = Path(session.rendered_files[0])
        assert_that(rendered_path.exists()).is_true()
        assert_that(rendered_path.read_text(encoding="utf-8")).contains(SENTINEL_STR)
        assert_that(session.source_maps).contains_key(str(rendered_path.resolve()))
    finally:
        session.cleanup()


def test_translate_issue_skips_ambiguous_basename(
    tmp_path: Path,
) -> None:
    """Ambiguous basename matches leave the issue unmapped."""
    from lintro.template_aware.source_map import SourceMap
    from lintro.template_aware.translator import translate_issue

    map_a = SourceMap(
        original_path=str((tmp_path / "a" / "main.py.jinja").resolve()),
        rendered_path=str((tmp_path / "t0" / "main.py").resolve()),
        rendered_to_original={1: 1},
    )
    map_b = SourceMap(
        original_path=str((tmp_path / "b" / "main.py.jinja").resolve()),
        rendered_path=str((tmp_path / "t1" / "main.py").resolve()),
        rendered_to_original={1: 1},
    )
    issue = BaseIssue(file="main.py", line=1, message="x")
    translated = translate_issue(
        issue=issue,
        source_maps={
            map_a.rendered_path: map_a,
            map_b.rendered_path: map_b,
        },
    )
    assert_that(translated.file).is_equal_to("main.py")


def test_render_translate_round_trip(tmp_path: Path) -> None:
    """Render → fake lint issue → translate restores original line."""
    template = tmp_path / "svc.py.jinja"
    template.write_text(
        "# header\nvalue = '{{ project_name }}'\n",
        encoding="utf-8",
    )
    config = TemplateAwareConfig(enabled=True)

    session = prepare_templates_for_tool(
        tool_name="ruff",
        paths=[str(tmp_path)],
        exclude_patterns=[],
        config=config,
    )
    try:
        rendered = session.rendered_files[0]
        issue = BaseIssue(file=rendered, line=2, column=1, message="unused")
        translated = session.translate_issues([issue])[0]

        assert_that(translated.file).ends_with("svc.py.jinja")
        assert_that(translated.line).is_equal_to(2)
    finally:
        session.cleanup()


def test_int_sentinel_uses_token_equality_not_substring(tmp_path: Path) -> None:
    """``port`` matches as a token; ``import`` / ``report`` stay string sentinels.

    Args:
        tmp_path: Temporary directory for the template file.
    """
    template = tmp_path / "app.py.jinja"
    template.write_text(
        "from {{ import }} import x\nreport = {{ report }}\nport = {{ port }}\n",
        encoding="utf-8",
    )
    config = TemplateAwareConfig(enabled=True, stub_strategy=StubStrategy.SENTINEL)

    rendered, _source_map = render_template(template_path=template, config=config)

    assert_that(rendered).contains(f"from {SENTINEL_STR} import x")
    assert_that(rendered).contains(f"report = {SENTINEL_STR}")
    assert_that(rendered).contains(f"port = {SENTINEL_INT}")


def test_translate_issue_remaps_end_line_via_replace(tmp_path: Path) -> None:
    """RuffIssue ``end_line`` is remapped with ``dataclasses.replace``, not a cast.

    Args:
        tmp_path: Temporary directory for source-map paths.
    """
    from lintro.parsers.ruff.ruff_issue import RuffIssue

    original = tmp_path / "main.py.jinja"
    rendered = tmp_path / "main.py"
    original.write_text("a = 1\nb = 2\n", encoding="utf-8")
    rendered.write_text("a = 1\nb = 2\n", encoding="utf-8")
    source_map = build_source_map(
        original_text=original.read_text(encoding="utf-8"),
        rendered_text=rendered.read_text(encoding="utf-8"),
        original_path=str(original.resolve()),
        rendered_path=str(rendered.resolve()),
    )
    issue = RuffIssue(
        file=str(rendered.resolve()),
        line=1,
        end_line=2,
        message="demo",
        code="F401",
    )

    translated = translate_issue(
        issue=issue,
        source_maps={str(rendered.resolve()): source_map},
    )

    assert_that(translated.file).is_equal_to(str(original.resolve()))
    assert_that(translated.line).is_equal_to(1)
    assert_that(translated.end_line).is_equal_to(2)
    assert_that(issue.file).is_equal_to(str(rendered.resolve()))


def test_translate_result_preserves_metadata_and_timed_out(
    tmp_path: Path,
) -> None:
    """Active-session ``translate_result`` remaps issues without ``ai_metadata``.

    Args:
        tmp_path: Temporary directory for a rendered template.
    """
    from lintro.models.core.tool_result import ToolResult
    from lintro.parsers.ruff.ruff_issue import RuffIssue

    template = tmp_path / "svc.py.jinja"
    template.write_text("value = '{{ project_name }}'\n", encoding="utf-8")
    config = TemplateAwareConfig(enabled=True)

    session = prepare_templates_for_tool(
        tool_name="ruff",
        paths=[str(tmp_path)],
        exclude_patterns=[],
        config=config,
    )
    try:
        rendered = session.rendered_files[0]
        metadata = {"fixed_count": 0, "verified_count": 1}
        result = ToolResult(
            name="ruff",
            success=True,
            issues_count=1,
            issues=[
                RuffIssue(
                    file=rendered,
                    line=1,
                    end_line=1,
                    message="unused",
                    code="F401",
                ),
            ],
            metadata=metadata,
            timed_out=True,
            cwd="/project",
        )

        translated = session.translate_result(result)

        assert_that(translated.issues).is_length(1)
        assert_that(translated.issues[0].file).ends_with("svc.py.jinja")
        assert_that(translated.metadata).is_equal_to(metadata)
        assert_that(translated.timed_out).is_true()
        assert_that(translated.cwd).is_equal_to("/project")
        assert_that(hasattr(translated, "ai_metadata")).is_false()
    finally:
        session.cleanup()


def test_prepare_templates_cleans_temp_dir_on_unexpected_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-OSError render failures still release the temporary directory.

    Args:
        tmp_path: Temporary directory containing a template.
        monkeypatch: Pytest monkeypatch fixture.
    """
    import tempfile

    template = tmp_path / "boom.py.jinja"
    template.write_text("x = '{{ name }}'\n", encoding="utf-8")
    created: list[str] = []
    real_td = tempfile.TemporaryDirectory

    def _tracking_temporary_directory(
        *args: object,
        **kwargs: object,
    ) -> tempfile.TemporaryDirectory[str]:
        temp_dir = real_td(*args, **kwargs)
        created.append(temp_dir.name)
        return temp_dir

    monkeypatch.setattr(
        "lintro.template_aware.api.tempfile.TemporaryDirectory",
        _tracking_temporary_directory,
    )

    def _boom(**_kwargs: object) -> tuple[str, object]:
        raise RuntimeError("unexpected render failure")

    monkeypatch.setattr("lintro.template_aware.api.render_template", _boom)

    with pytest.raises(RuntimeError, match="unexpected render failure"):
        prepare_templates_for_tool(
            tool_name="ruff",
            paths=[str(tmp_path)],
            exclude_patterns=[],
            config=TemplateAwareConfig(enabled=True),
        )

    assert_that(created).is_not_empty()
    assert_that(Path(created[0]).exists()).is_false()


def test_get_template_aware_config_swallows_expected_load_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LookupError / ValidationError disable the feature; other errors surface.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
    """
    from pydantic import ValidationError

    from lintro.template_aware.api import get_template_aware_config

    def _missing_config() -> object:
        raise LookupError("missing")

    monkeypatch.setattr(
        "lintro.plugins.execution_preparation.get_lintro_config",
        _missing_config,
    )
    disabled = get_template_aware_config()
    assert_that(disabled.enabled).is_false()

    def _validation_error() -> object:
        raise ValidationError.from_exception_data("TemplateAwareConfig", [])

    monkeypatch.setattr(
        "lintro.plugins.execution_preparation.get_lintro_config",
        _validation_error,
    )
    still_disabled = get_template_aware_config()
    assert_that(still_disabled.enabled).is_false()

    def _programming_error() -> object:
        raise RuntimeError("config loader exploded")

    monkeypatch.setattr(
        "lintro.plugins.execution_preparation.get_lintro_config",
        _programming_error,
    )
    assert_that(get_template_aware_config).raises(RuntimeError).when_called_with()
