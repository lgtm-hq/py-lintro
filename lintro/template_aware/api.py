"""Public entry point for template-aware preprocessing.

Discovers ``*.jinja`` templates routed to a host tool, stub-renders them into
a temporary directory, and returns rendered paths plus source maps for issue
translation.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

from lintro.config.template_aware_config import TemplateAwareConfig
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.base_issue import BaseIssue
from lintro.template_aware.prerenderer import (
    render_template,
    rendered_filename_for,
)
from lintro.template_aware.router import patterns_for_tool
from lintro.template_aware.source_map import SourceMap
from lintro.template_aware.translator import translate_issues
from lintro.utils.path_filtering import walk_files_with_excludes


@dataclass
class TemplateAwareSession:
    """Holds rendered files and source maps for one tool invocation.

    Attributes:
        rendered_files: Absolute paths of stub-rendered host-language files.
        source_maps: Mapping of rendered absolute path → SourceMap.
        temp_dir: Temporary directory owning the rendered files (kept alive
            for the duration of the tool run).
    """

    rendered_files: list[str] = field(default_factory=list)
    source_maps: dict[str, SourceMap] = field(default_factory=dict)
    temp_dir: tempfile.TemporaryDirectory[str] | None = None

    @property
    def active(self) -> bool:
        """Whether this session has any rendered files.

        Returns:
            True when at least one template was pre-rendered.
        """
        return bool(self.rendered_files)

    def translate_issues(
        self,
        issues: Sequence[BaseIssue],
    ) -> list[BaseIssue]:
        """Translate issues using this session's source maps.

        Args:
            issues: Host-linter issues.

        Returns:
            Issues remapped onto original template paths/lines.
        """
        return translate_issues(issues=issues, source_maps=self.source_maps)

    def translate_result(self, result: ToolResult) -> ToolResult:
        """Return a ToolResult with remapped issues (and initial_issues).

        Args:
            result: Tool result from the host linter.

        Returns:
            New ToolResult with translated issue paths/lines.
        """
        if not self.active:
            return result

        issues = (
            self.translate_issues(result.issues) if result.issues else result.issues
        )
        initial_issues = (
            self.translate_issues(result.initial_issues)
            if result.initial_issues
            else result.initial_issues
        )
        return ToolResult(
            name=result.name,
            success=result.success,
            output=result.output,
            issues_count=result.issues_count,
            formatted_output=result.formatted_output,
            issues=issues,
            initial_issues_count=result.initial_issues_count,
            fixed_issues_count=result.fixed_issues_count,
            remaining_issues_count=result.remaining_issues_count,
            initial_issues=initial_issues,
            pytest_summary=result.pytest_summary,
            ai_metadata=result.ai_metadata,
            cwd=result.cwd,
            skipped=result.skipped,
            skip_reason=result.skip_reason,
            parse_failures_count=result.parse_failures_count,
        )

    def cleanup(self) -> None:
        """Release the temporary render directory."""
        if self.temp_dir is not None:
            try:
                self.temp_dir.cleanup()
            except OSError as exc:
                logger.debug("Template-aware temp cleanup failed: {}", exc)
            self.temp_dir = None


def get_template_aware_config() -> TemplateAwareConfig:
    """Load the current ``template_aware`` config section.

    Returns:
        TemplateAwareConfig (disabled defaults when config unavailable).
    """
    try:
        from lintro.plugins.execution_preparation import get_lintro_config

        return get_lintro_config().template_aware
    except Exception as exc:  # noqa: BLE001 - feature must stay inert on errors
        logger.debug("template_aware config unavailable: {}", exc)
        return TemplateAwareConfig()


def prepare_templates_for_tool(
    *,
    tool_name: str,
    paths: list[str],
    exclude_patterns: list[str],
    include_venv: bool = False,
    config: TemplateAwareConfig | None = None,
) -> TemplateAwareSession:
    """Pre-render templates routed to ``tool_name`` into a temp directory.

    This is the single public entry point hooked from
    ``BaseToolPlugin._prepare_execution``. When the feature is disabled or no
    matching templates exist, returns an inactive session.

    Args:
        tool_name: Host tool currently preparing execution (e.g. ``ruff``).
        paths: User-supplied input paths (files or directories).
        exclude_patterns: Exclude patterns from the plugin.
        include_venv: Whether to include virtualenv directories.
        config: Optional explicit config (defaults to loaded lintro config).

    Returns:
        TemplateAwareSession with rendered files and source maps.
    """
    cfg = config if config is not None else get_template_aware_config()
    if not cfg.enabled:
        return TemplateAwareSession()

    patterns = patterns_for_tool(tool_name=tool_name, config=cfg)
    if not patterns:
        return TemplateAwareSession()

    template_files = walk_files_with_excludes(
        paths=paths,
        file_patterns=patterns,
        exclude_patterns=exclude_patterns,
        include_venv=include_venv,
    )
    if not template_files:
        return TemplateAwareSession()

    temp_dir = tempfile.TemporaryDirectory(prefix="lintro-template-aware-")
    session = TemplateAwareSession(temp_dir=temp_dir)
    temp_root = Path(temp_dir.name)

    for index, template_file in enumerate(template_files):
        template_path = Path(template_file)
        try:
            rendered_text, source_map = render_template(
                template_path=template_path,
                config=cfg,
            )
        except OSError as exc:
            logger.warning(
                "Skipping template {}: {}",
                template_path,
                exc,
            )
            continue

        # Disambiguate collisions when multiple templates share a basename.
        rendered_name = rendered_filename_for(template_path)
        dest_dir = temp_root / f"t{index}"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / rendered_name
        dest_path.write_text(rendered_text, encoding="utf-8")
        rendered_abs = str(dest_path.resolve())

        session.source_maps[rendered_abs] = SourceMap(
            original_path=str(template_path.resolve()),
            rendered_path=rendered_abs,
            rendered_to_original=source_map.rendered_to_original,
        )
        session.rendered_files.append(rendered_abs)
        logger.debug(
            "template_aware: rendered {} → {} for tool {}",
            template_path,
            rendered_abs,
            tool_name,
        )

    return session


def merge_rendered_files(
    discovered_files: list[str],
    session: TemplateAwareSession,
) -> list[str]:
    """Append rendered template files onto the discovered host-language list.

    Args:
        discovered_files: Files already discovered for the host tool.
        session: Active template-aware session.

    Returns:
        Combined file list (discovered first, then rendered).
    """
    if not session.active:
        return discovered_files
    # Avoid duplicates if a path somehow appears in both lists.
    existing = {os.path.abspath(path) for path in discovered_files}
    merged = list(discovered_files)
    for rendered in session.rendered_files:
        if os.path.abspath(rendered) not in existing:
            merged.append(rendered)
    return merged
