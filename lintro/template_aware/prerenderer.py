"""Stub context builders and Jinja2 pre-rendering for template-aware mode."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml
from jinja2 import BaseLoader, Environment, Undefined
from jinja2.exceptions import TemplateError
from loguru import logger

from lintro.config.template_aware_config import (
    StubStrategy,
    TemplateAwareConfig,
    TemplateEngine,
)
from lintro.template_aware.source_map import SourceMap, build_source_map
from lintro.utils.path_utils import find_file_upward

# Type-stable sentinel placeholders for the ``sentinel`` stub strategy.
SENTINEL_STR = "__STR__"
SENTINEL_INT = "__INT__"

_HOST_SUFFIX_MAP: dict[str, str] = {
    ".py.jinja": ".py",
    ".toml.jinja": ".toml",
    ".yml.jinja": ".yml",
    ".yaml.jinja": ".yaml",
    ".json.jinja": ".json",
}

_COPIER_FILENAMES: tuple[str, ...] = (
    "copier.yml",
    "copier.yaml",
    ".copier-answers.yml",
    ".copier-answers.yaml",
)

_COOKIECUTTER_FILENAMES: tuple[str, ...] = ("cookiecutter.json",)


class _SentinelUndefined(Undefined):
    """Truthy, chainable undefined that renders as a string sentinel.

    Keeps ``{% if %}`` on the true branch and yields one item for
    ``{% for %}`` so loop bodies are not silently skipped.
    """

    def __str__(self) -> str:
        return SENTINEL_STR

    def __iter__(self) -> Any:
        yield _SentinelUndefined(name="item")

    def __bool__(self) -> bool:
        return True

    def __getattr__(self, _name: str) -> _SentinelUndefined:
        return _SentinelUndefined(name=_name)


def host_suffix_for_template(template_path: Path) -> str:
    """Return the host-language suffix for a ``*.jinja`` template path.

    Args:
        template_path: Path to the template file.

    Returns:
        Host suffix such as ``.py``, or ``.txt`` when unknown.
    """
    name = template_path.name.lower()
    for jinja_suffix, host_suffix in _HOST_SUFFIX_MAP.items():
        if name.endswith(jinja_suffix):
            return host_suffix
    # Generic ``*.jinja`` → strip ``.jinja``
    if name.endswith(".jinja"):
        stem = template_path.stem  # drops final .jinja
        suffix = Path(stem).suffix
        return suffix if suffix else ".txt"
    return ".txt"


def rendered_filename_for(template_path: Path) -> str:
    """Build a stable rendered filename for ``template_path``.

    Args:
        template_path: Original template path.

    Returns:
        Filename to use inside the render temp directory.
    """
    name = template_path.name
    lower = name.lower()
    for jinja_suffix in _HOST_SUFFIX_MAP:
        if lower.endswith(jinja_suffix):
            return name[: -len(".jinja")]
    if lower.endswith(".jinja"):
        # Strip only the trailing ``.jinja`` so ``main.rs.jinja`` → ``main.rs``
        # (do not append host_suffix again; the stem already carries it).
        return name[: -len(".jinja")]
    host_suffix = host_suffix_for_template(template_path)
    return f"{template_path.stem}{host_suffix}"


def _load_mapping_file(path: Path) -> dict[str, Any]:
    """Load a YAML or JSON mapping file.

    Args:
        path: Path to the answers/defaults file.

    Returns:
        Mapping of variable names to values (empty on failure).
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not read template context file {}: {}", path, exc)
        return {}

    suffix = path.suffix.lower()
    try:
        if suffix in {".yaml", ".yml"}:
            data = yaml.safe_load(text)
        elif suffix == ".json":
            data = json.loads(text)
        else:
            data = yaml.safe_load(text)
    except (yaml.YAMLError, json.JSONDecodeError) as exc:
        logger.warning("Could not parse template context file {}: {}", path, exc)
        return {}

    if not isinstance(data, dict):
        return {}
    return dict(data)


def _extract_copier_defaults(data: dict[str, Any]) -> dict[str, Any]:
    """Extract default values from a copier.yml-style mapping.

    Args:
        data: Parsed copier config.

    Returns:
        Flat defaults dict suitable for Jinja rendering.
    """
    defaults: dict[str, Any] = {}
    for key, value in data.items():
        if key.startswith("_"):
            continue
        if isinstance(value, dict) and "default" in value:
            defaults[key] = value["default"]
        elif not isinstance(value, (dict, list)):
            defaults[key] = value
    return defaults


def load_defaults_context(
    template_path: Path,
    engine: TemplateEngine,
) -> dict[str, Any]:
    """Load defaults from copier.yml / cookiecutter.json near ``template_path``.

    Args:
        template_path: Template being rendered.
        engine: Engine hint selecting which defaults files to prefer.

    Returns:
        Context dict of default values (may be empty).
    """
    start = template_path.parent
    if engine == TemplateEngine.COOKIECUTTER:
        found = find_file_upward(
            start,
            _COOKIECUTTER_FILENAMES,
            max_depth=20,
        )
        if found is None:
            found = find_file_upward(start, _COPIER_FILENAMES, max_depth=20)
    else:
        found = find_file_upward(start, _COPIER_FILENAMES, max_depth=20)
        if found is None:
            found = find_file_upward(
                start,
                _COOKIECUTTER_FILENAMES,
                max_depth=20,
            )

    if found is None:
        logger.debug("No defaults file found for {}", template_path)
        return {}

    data = _load_mapping_file(found)
    if found.name.startswith("cookiecutter") or found.name == "cookiecutter.json":
        return data
    return _extract_copier_defaults(data)


def load_context_file(
    context_file: str | None,
    *,
    search_from: Path,
) -> dict[str, Any]:
    """Load a user-supplied context file for the ``context_file`` strategy.

    Args:
        context_file: Relative or absolute path from config.
        search_from: Directory used to resolve relative paths.

    Returns:
        Context mapping (empty when missing/unreadable).
    """
    if not context_file:
        logger.warning(
            "stub_strategy=context_file but no context_file configured",
        )
        return {}

    path = Path(context_file)
    if not path.is_absolute():
        candidate = search_from / path
        if candidate.exists():
            path = candidate
        else:
            found = find_file_upward(
                search_from,
                (path.name,),
                max_depth=20,
            )
            if found is not None:
                path = found

    if not path.exists():
        logger.warning("Template context file not found: {}", context_file)
        return {}
    return _load_mapping_file(path)


def build_render_context(
    template_path: Path,
    config: TemplateAwareConfig,
) -> dict[str, Any]:
    """Build the Jinja render context for ``template_path``.

    Args:
        template_path: Template being rendered.
        config: Template-aware configuration.

    Returns:
        Context dict passed to Jinja2.
    """
    if config.stub_strategy == StubStrategy.SENTINEL:
        return {}
    if config.stub_strategy == StubStrategy.DEFAULTS:
        return load_defaults_context(
            template_path=template_path,
            engine=config.engine,
        )
    return load_context_file(
        config.context_file,
        search_from=template_path.parent,
    )


def _make_environment(stub_strategy: StubStrategy) -> Environment:
    """Create a Jinja2 environment for the given stub strategy.

    Args:
        stub_strategy: Active stub strategy.

    Returns:
        Configured Jinja2 Environment.
    """
    if stub_strategy == StubStrategy.SENTINEL:
        undefined: type[Undefined] = _SentinelUndefined
    else:
        # DEFAULTS / CONTEXT_FILE: missing keys should surface as render errors
        # rather than silently becoming ``__STR__`` sentinels.
        undefined = Undefined

    # nosemgrep: direct-use-of-jinja2
    return Environment(
        loader=BaseLoader(),
        undefined=undefined,
        autoescape=False,  # nosec B701 - source-language templates, not HTML output
        keep_trailing_newline=True,
    )


_INT_VAR_RE = re.compile(
    r"{{\s*([\w.]+)\s*(?:\|[^}]*)?\s*}}",
)

_INT_NAME_HINTS: tuple[str, ...] = (
    "count",
    "port",
    "year",
    "age",
    "num",
    "number",
    "index",
    "size",
    "length",
    "timeout",
    "retries",
    "workers",
)


def _int_sentinel_var_names(original: str) -> set[str]:
    """Collect template variable names that look integer-typed.

    Args:
        original: Original template source.

    Returns:
        Set of variable base names that should render as ``__INT__``.
    """
    names: set[str] = set()
    for match in _INT_VAR_RE.finditer(original):
        base = match.group(1).rsplit(".", maxsplit=1)[-1].lower()
        if base == "id" or any(hint in base for hint in _INT_NAME_HINTS):
            names.add(match.group(1))
    return names


def _pre_replace_int_vars(original: str) -> str:
    """Rewrite integer-hinted ``{{ var }}`` expressions to ``__INT__`` literals.

    Args:
        original: Original template source.

    Returns:
        Template text with int-hinted expressions replaced by ``__INT__``.
    """
    int_vars = _int_sentinel_var_names(original)
    if not int_vars:
        return original

    def _replace(match: re.Match[str]) -> str:
        if match.group(1) in int_vars:
            return SENTINEL_INT
        return match.group(0)

    return _INT_VAR_RE.sub(_replace, original)


def render_template(
    template_path: Path,
    config: TemplateAwareConfig,
) -> tuple[str, SourceMap]:
    """Stub-render a template and build its source map.

    Args:
        template_path: Absolute path to the ``*.jinja`` template.
        config: Template-aware configuration.

    Returns:
        Tuple of ``(rendered_text, source_map)``. On render failure, returns
        the original text unchanged with a 1:1 map so the host linter still
        sees something (and will report syntax errors against the template).
    """
    original_text = template_path.read_text(encoding="utf-8")
    render_source = original_text
    if config.stub_strategy == StubStrategy.SENTINEL:
        render_source = _pre_replace_int_vars(original_text)

    context = build_render_context(template_path=template_path, config=config)
    env = _make_environment(stub_strategy=config.stub_strategy)

    try:
        template = env.from_string(render_source)
        rendered_text = template.render(**context)
    except (TemplateError, TypeError, ValueError) as exc:
        # TemplateError covers TemplateSyntaxError, UndefinedError,
        # TemplateRuntimeError, TemplateAssertionError, and related Jinja
        # failures so a bad template cannot crash the lint run.
        logger.warning(
            "Template-aware render failed for {}: {}; using original text",
            template_path,
            exc,
        )
        rendered_text = original_text

    source_map = build_source_map(
        original_text=original_text,
        rendered_text=rendered_text,
        original_path=str(template_path.resolve()),
        rendered_path="",  # filled by caller after writing
    )
    return rendered_text, source_map
