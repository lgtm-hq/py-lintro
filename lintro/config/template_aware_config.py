"""Template-aware preprocessing configuration model.

Opt-in section for stub-rendering ``*.py.jinja`` / ``*.toml.jinja`` /
``*.yml.jinja`` (and similar) source templates so host-language linters can
run against rendered output with issues mapped back to the original template.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class TemplateEngine(StrEnum):
    """Supported template engines for template-aware preprocessing."""

    JINJA2 = "jinja2"
    COPIER = "copier"
    COOKIECUTTER = "cookiecutter"


class StubStrategy(StrEnum):
    """How stub values are supplied when pre-rendering templates."""

    SENTINEL = "sentinel"
    DEFAULTS = "defaults"
    CONTEXT_FILE = "context_file"


_DEFAULT_PATTERNS: list[str] = [
    "*.py.jinja",
    "*.toml.jinja",
    "*.yml.jinja",
    "*.yaml.jinja",
    "*.json.jinja",
]

_DEFAULT_ROUTE: dict[str, str] = {
    "*.py.jinja": "ruff",
    "*.toml.jinja": "taplo",
    "*.yml.jinja": "yamllint",
    "*.yaml.jinja": "yamllint",
    "*.json.jinja": "prettier",
}


class TemplateAwareConfig(BaseModel):
    """Configuration for opt-in template-aware pre-processing.

    Disabled by default. When enabled, matching ``*.jinja`` source templates
    are stub-rendered into a temporary directory, routed to the configured
    host linter, and reported issues are source-mapped back to the original
    template path.

    Attributes:
        model_config: Pydantic model configuration.
        enabled: Master switch. Must be true for any preprocessing to run.
        patterns: Glob patterns (filename-style) for template files to include.
        engine: Template engine dialect hint (``jinja2``, ``copier``,
            ``cookiecutter``). Affects defaults discovery; rendering uses
            Jinja2 for all engines.
        stub_strategy: How to supply stub values for ``{{ var }}`` placeholders.
        context_file: Optional path to a YAML/JSON answers file when
            ``stub_strategy`` is ``context_file``.
        route: Mapping of template glob pattern to host tool name.
    """

    model_config = ConfigDict(frozen=False, extra="forbid")

    enabled: bool = Field(
        default=False,
        description=(
            "Master switch for template-aware preprocessing. Off by default. "
            "This is a best-effort pre-pass: stub rendering can hide real "
            "template bugs, and line mapping is imperfect around Jinja "
            "control structures."
        ),
    )
    patterns: list[str] = Field(default_factory=lambda: list(_DEFAULT_PATTERNS))
    engine: TemplateEngine = Field(default=TemplateEngine.JINJA2)
    stub_strategy: StubStrategy = Field(default=StubStrategy.SENTINEL)
    context_file: str | None = Field(default=None)
    route: dict[str, str] = Field(default_factory=lambda: dict(_DEFAULT_ROUTE))
