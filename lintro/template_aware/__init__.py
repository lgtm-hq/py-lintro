"""Opt-in template-aware pre-processing for source-language Jinja templates.

Stub-renders ``*.py.jinja`` / ``*.toml.jinja`` / ``*.yml.jinja`` (etc.) so
host linters (ruff, taplo, yamllint, prettier) can run, then source-maps
reported issues back to the original template coordinates.

This feature is **off by default** and is a best-effort pre-pass: stub
rendering can hide real template bugs, and line mapping is imperfect around
Jinja control structures. See ``docs/template-aware.md``.
"""

from lintro.template_aware.api import (
    TemplateAwareSession,
    get_template_aware_config,
    merge_rendered_files,
    prepare_templates_for_tool,
)
from lintro.template_aware.source_map import SourceMap, build_source_map
from lintro.template_aware.translator import translate_issue, translate_issues

__all__ = [
    "SourceMap",
    "TemplateAwareSession",
    "build_source_map",
    "get_template_aware_config",
    "merge_rendered_files",
    "prepare_templates_for_tool",
    "translate_issue",
    "translate_issues",
]
