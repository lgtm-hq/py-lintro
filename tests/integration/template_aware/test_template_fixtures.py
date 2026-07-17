"""Integration-style fixtures for Copier, Cookiecutter, and Ansible templates."""

from __future__ import annotations

from pathlib import Path

from assertpy import assert_that

from lintro.config.template_aware_config import (
    StubStrategy,
    TemplateAwareConfig,
    TemplateEngine,
)
from lintro.template_aware import prepare_templates_for_tool
from lintro.template_aware.prerenderer import SENTINEL_STR, render_template


def test_copier_template_integration(tmp_path: Path) -> None:
    """Copier-style layout: copier.yml defaults feed a Python template."""
    (tmp_path / "copier.yml").write_text(
        (
            "project_name:\n"
            "  type: str\n"
            "  default: CopierApp\n"
            "use_cli:\n"
            "  type: bool\n"
            "  default: true\n"
        ),
        encoding="utf-8",
    )
    src = tmp_path / "{{ project_name }}"
    src.mkdir()
    template = src / "main.py.jinja"
    template.write_text(
        (
            "'''{{ project_name }} entrypoint.'''\n"
            "\n"
            "APP_NAME = '{{ project_name }}'\n"
            "{% if use_cli %}\n"
            "def main() -> None:\n"
            "    print(APP_NAME)\n"
            "{% endif %}\n"
        ),
        encoding="utf-8",
    )
    config = TemplateAwareConfig(
        enabled=True,
        engine=TemplateEngine.COPIER,
        stub_strategy=StubStrategy.DEFAULTS,
    )

    session = prepare_templates_for_tool(
        tool_name="ruff",
        paths=[str(tmp_path)],
        exclude_patterns=[],
        config=config,
    )
    try:
        assert_that(session.active).is_true()
        rendered = Path(session.rendered_files[0]).read_text(encoding="utf-8")
        assert_that(rendered).contains("CopierApp")
        assert_that(rendered).contains("def main()")
    finally:
        session.cleanup()


def test_cookiecutter_template_integration(tmp_path: Path) -> None:
    """Cookiecutter-style layout: cookiecutter.json defaults feed a template."""
    (tmp_path / "cookiecutter.json").write_text(
        '{"project_slug": "cookie_demo", "author": "Ada"}\n',
        encoding="utf-8",
    )
    package = tmp_path / "{{ cookiecutter.project_slug }}"
    package.mkdir()
    template = package / "__init__.py.jinja"
    # cookiecutter vars are typically referenced as cookiecutter.X; our
    # defaults loader flattens cookiecutter.json keys at the top level, so
    # templates under lintro use flat names (or nested via attr access on
    # a provided cookiecutter mapping). Use flat keys matching the JSON.
    template.write_text(
        '"""Package {{ project_slug }} by {{ author }}."""\n__version__ = "0.1.0"\n',
        encoding="utf-8",
    )
    config = TemplateAwareConfig(
        enabled=True,
        engine=TemplateEngine.COOKIECUTTER,
        stub_strategy=StubStrategy.DEFAULTS,
    )

    rendered, source_map = render_template(template_path=template, config=config)

    assert_that(rendered).contains("cookie_demo")
    assert_that(rendered).contains("Ada")
    assert_that(source_map.lookup_line(1)).is_equal_to(1)


def test_ansible_playbook_yml_jinja_integration(tmp_path: Path) -> None:
    """Ansible-style YAML playbook template routes to yamllint."""
    template = tmp_path / "site.yml.jinja"
    template.write_text(
        (
            "---\n"
            "- name: Configure {{ app_name }}\n"
            "  hosts: {{ target_hosts }}\n"
            "  tasks:\n"
            "    - name: Ensure service running\n"
            "      ansible.builtin.service:\n"
            "        name: {{ service_name }}\n"
            "        state: started\n"
        ),
        encoding="utf-8",
    )
    config = TemplateAwareConfig(enabled=True, stub_strategy=StubStrategy.SENTINEL)

    session = prepare_templates_for_tool(
        tool_name="yamllint",
        paths=[str(tmp_path)],
        exclude_patterns=[],
        config=config,
    )
    try:
        assert_that(session.active).is_true()
        rendered = Path(session.rendered_files[0]).read_text(encoding="utf-8")
        assert_that(rendered).contains(SENTINEL_STR)
        assert_that(rendered).contains("ansible.builtin.service")
        assert_that(session.source_maps).is_not_empty()
    finally:
        session.cleanup()


def test_toml_jinja_routes_to_taplo(tmp_path: Path) -> None:
    """*.toml.jinja templates are prepared for taplo."""
    template = tmp_path / "pyproject.toml.jinja"
    template.write_text(
        '[project]\nname = "{{ project_name }}"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    config = TemplateAwareConfig(enabled=True)

    session = prepare_templates_for_tool(
        tool_name="taplo",
        paths=[str(tmp_path)],
        exclude_patterns=[],
        config=config,
    )
    try:
        assert_that(session.active).is_true()
        assert_that(Path(session.rendered_files[0]).name).ends_with(".toml")
    finally:
        session.cleanup()
