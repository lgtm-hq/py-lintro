"""AI-powered ``idiom-review`` tool definition.

Unlike every other tool, ``idiom-review`` has no external binary: it uses
lintro's existing AI engine to find issues that structurally cannot be
detected by a syntax-matching linter. Two modes are offered:

* **per-file** (Mode 1) — flag idiomatic misses (verbose, cross-language
  patterns instead of the language's built-in idioms).
* **duplication** (Mode 2) — flag the same utility logic reimplemented
  across files, invisible to any per-file linter.

The tool ships disabled by default via the ``enabled: False`` option in
``default_options``: ``check()`` returns a skipped result immediately unless
the caller opts in via ``tools.idiom-review`` config or
``--tool-options idiom-review:enabled=true``. When no AI provider is
available (missing SDK, key, or credits) it degrades gracefully to a
skipped result rather than failing the run.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from loguru import logger

from lintro.ai.availability import is_ai_available
from lintro.ai.budget import CostBudget
from lintro.ai.enums import ConfidenceLevel
from lintro.ai.exceptions import AIError
from lintro.ai.paths import resolve_workspace_root
from lintro.ai.providers import get_provider
from lintro.enums.tool_type import ToolType
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.idiom_review.idiom_review_issue import IdiomReviewIssue
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition
from lintro.plugins.registry import register_tool
from lintro.tools.idiom_review.engine import IdiomReviewEngine, IdiomReviewMode
from lintro.tools.idiom_review.signatures import (
    Signature,
    extract_python_signatures,
)

IDIOM_REVIEW_TOOL_NAME = "idiom-review"
# Late priority so idiom review runs after the fast syntax linters.
IDIOM_REVIEW_PRIORITY = 95
IDIOM_REVIEW_DEFAULT_TIMEOUT = 120
IDIOM_REVIEW_FILE_PATTERNS = ["*.py"]
# Bound cost: cap files reviewed per run unless the user raises it.
IDIOM_REVIEW_DEFAULT_MAX_FILES = 25


def _confidence_rank(value: str) -> int:
    """Return the numeric rank for a confidence string.

    Args:
        value: Confidence string (``high``/``medium``/``low``).

    Returns:
        Numeric rank (3=high, 2=medium, 1=low), defaulting to medium.
    """
    try:
        return ConfidenceLevel(value.lower()).numeric_order
    except ValueError:
        return ConfidenceLevel.MEDIUM.numeric_order


@register_tool
@dataclass
class IdiomReviewPlugin(BaseToolPlugin):
    """AI idiom-review plugin (idiomatic-miss + cross-file duplication)."""

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            ToolDefinition with idiom-review metadata.
        """
        return ToolDefinition(
            name=IDIOM_REVIEW_TOOL_NAME,
            description=(
                "AI reviewer that flags idiomatic misses and cross-file "
                "duplicate logic (no external binary; uses the AI provider)"
            ),
            can_fix=False,
            tool_type=ToolType.LINTER,
            file_patterns=IDIOM_REVIEW_FILE_PATTERNS,
            priority=IDIOM_REVIEW_PRIORITY,
            conflicts_with=[],
            native_configs=[],
            version_command=None,
            min_version=None,
            default_options={
                "timeout": IDIOM_REVIEW_DEFAULT_TIMEOUT,
                # Opt-in gate: the tool is a no-op until this is true.
                "enabled": False,
                # "per-file" | "duplication" | "both".
                "mode": IdiomReviewMode.PER_FILE.value,
                "language": "python",
                # Drop findings below this confidence level.
                "min_confidence": ConfidenceLevel.MEDIUM.value,
                "max_files": IDIOM_REVIEW_DEFAULT_MAX_FILES,
            },
            default_timeout=IDIOM_REVIEW_DEFAULT_TIMEOUT,
        )

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Run AI idiom review over the discovered Python files.

        Args:
            paths: File or directory paths to review.
            options: Runtime options overriding defaults.

        Returns:
            ToolResult with idiom findings, or a skipped result when the
            tool is not opted in or no AI provider is available.
        """
        merged: dict[str, object] = dict(self.options)
        merged.update(options)

        # Opt-in gate — disabled by default.
        if not bool(merged.get("enabled", False)):
            return ToolResult(
                name=IDIOM_REVIEW_TOOL_NAME,
                skipped=True,
                skip_reason=(
                    "idiom-review is disabled by default; enable it via "
                    "tools.idiom-review or "
                    "--tool-options idiom-review:enabled=true"
                ),
                output="idiom-review is disabled (opt-in required).",
            )

        # Graceful no-credentials path — never require live API access.
        if not is_ai_available():
            return ToolResult(
                name=IDIOM_REVIEW_TOOL_NAME,
                skipped=True,
                skip_reason=(
                    "No AI provider available. Install lintro[ai] and set an "
                    "API key to enable idiom-review."
                ),
                output="idiom-review skipped: no AI provider available.",
            )

        ctx = self._prepare_execution(paths, options)
        if ctx.should_skip:
            return ctx.early_result  # type: ignore[return-value]

        try:
            return self._run_review(files=ctx.files, options=merged)
        except AIError as exc:
            # Depleted credits / auth / rate limits degrade gracefully.
            logger.warning("[idiom-review] AI unavailable, skipping: {}", exc)
            return ToolResult(
                name=IDIOM_REVIEW_TOOL_NAME,
                skipped=True,
                skip_reason=f"AI provider error: {exc}",
                output=f"idiom-review skipped: {exc}",
            )

    def _run_review(
        self,
        *,
        files: list[str],
        options: dict[str, object],
    ) -> ToolResult:
        """Execute the requested review modes with a configured engine.

        Args:
            files: Absolute paths of Python files to review.
            options: Merged runtime options.

        Returns:
            ToolResult with the aggregated, confidence-filtered findings.
        """
        lintro_config = self._get_lintro_config()
        ai_config = lintro_config.ai
        workspace_root = resolve_workspace_root(lintro_config.config_path)
        provider = get_provider(ai_config, workspace_root=workspace_root)
        budget = CostBudget(max_cost_usd=ai_config.max_cost_usd)

        engine = IdiomReviewEngine(
            provider=provider,
            ai_config=ai_config,
            budget=budget,
            workspace_root=workspace_root,
            cache_ttl=int(ai_config.cache_ttl),
        )

        mode = str(options.get("mode", IdiomReviewMode.PER_FILE.value))
        valid_modes = {m.value for m in IdiomReviewMode}
        if mode not in valid_modes:
            # An unknown mode must fail loudly: silently running neither
            # review would report success while doing nothing.
            message = (
                f"idiom-review: invalid mode {mode!r}; "
                f"expected one of {sorted(valid_modes)}."
            )
            return ToolResult(
                name=IDIOM_REVIEW_TOOL_NAME,
                success=False,
                output=message,
            )

        language = str(options.get("language", "python"))
        min_conf = str(options.get("min_confidence", ConfidenceLevel.MEDIUM.value))
        raw_max_files = options.get("max_files", IDIOM_REVIEW_DEFAULT_MAX_FILES)
        try:
            max_files = int(cast("int", raw_max_files))
        except (TypeError, ValueError):
            max_files = -1
        if max_files < 1:
            # A cost cap of zero/negative (or a non-numeric value) is a
            # configuration error, not a request to review everything.
            message = (
                f"idiom-review: invalid max_files {raw_max_files!r}; "
                "expected a positive integer."
            )
            return ToolResult(
                name=IDIOM_REVIEW_TOOL_NAME,
                success=False,
                output=message,
            )

        run_per_file = mode in (
            IdiomReviewMode.PER_FILE.value,
            IdiomReviewMode.BOTH.value,
        )
        run_dupe = mode in (
            IdiomReviewMode.DUPLICATION.value,
            IdiomReviewMode.BOTH.value,
        )

        scoped = files[:max_files]
        issues: list[IdiomReviewIssue] = []
        signatures: list[Signature] = []

        for file_path in scoped:
            source = self._read_source(file_path)
            if source is None:
                continue
            if run_per_file:
                issues.extend(
                    engine.review_file(
                        file_path=file_path,
                        source=source,
                        language=language,
                    ),
                )
            if run_dupe:
                signatures.extend(extract_python_signatures(file_path, source))

        if run_dupe:
            issues.extend(engine.review_duplication(signatures))

        min_rank = _confidence_rank(min_conf)
        filtered = [i for i in issues if _confidence_rank(i.confidence) >= min_rank]

        return ToolResult(
            name=IDIOM_REVIEW_TOOL_NAME,
            success=len(filtered) == 0,
            output=None,
            issues_count=len(filtered),
            issues=filtered,
        )

    @staticmethod
    def _read_source(file_path: str) -> str | None:
        """Read a file's text, returning ``None`` on failure.

        Args:
            file_path: Path of the file to read.

        Returns:
            File content, or ``None`` when it cannot be read.
        """
        try:
            with open(file_path, encoding="utf-8") as handle:
                return handle.read()
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("[idiom-review] Could not read {}: {}", file_path, exc)
            return None

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """idiom-review is a reporter; it does not fix.

        Args:
            paths: Unused.
            options: Unused.

        Returns:
            Never returns.

        Raises:
            NotImplementedError: Always; the tool cannot fix issues.
        """
        raise NotImplementedError(
            "idiom-review reports findings only. Use 'lintro chk --fix' to "
            "generate AI fix suggestions for its findings.",
        )
