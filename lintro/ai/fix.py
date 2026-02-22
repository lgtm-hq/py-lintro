"""AI fix generation service.

Generates fix suggestions for issues that native tools cannot auto-fix.
Reads file contents, asks the AI for a corrected version, and produces
unified diffs. Supports parallel API calls for improved performance.
"""

from __future__ import annotations

import difflib
import json
import threading
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from lintro.ai.models import AIFixSuggestion
from lintro.ai.paths import (
    relative_path,
    resolve_workspace_file,
    resolve_workspace_root,
    to_provider_path,
)
from lintro.ai.prompts import FIX_PROMPT_TEMPLATE, FIX_SYSTEM
from lintro.ai.retry import with_retry

if TYPE_CHECKING:
    from lintro.ai.providers.base import AIResponse, BaseAIProvider
    from lintro.parsers.base_issue import BaseIssue


def _call_provider(
    provider: BaseAIProvider,
    prompt: str,
    system: str,
    max_tokens: int,
    timeout: float = 60.0,
) -> AIResponse:
    """Call the AI provider (no retry — caller wraps with retry)."""
    return provider.complete(
        prompt,
        system=system,
        max_tokens=max_tokens,
        timeout=timeout,
    )


# Context window around the issue line (lines before/after)
CONTEXT_LINES = 15

# Maximum concurrent API calls for fix generation
DEFAULT_MAX_WORKERS = 5


def _read_file_safely(file_path: str) -> str | None:
    """Read a file's contents, returning None on failure.

    Args:
        file_path: Path to the file.

    Returns:
        File contents as a string, or None if unreadable.
    """
    try:
        return Path(file_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        logger.debug(f"Could not read file: {file_path}")
        return None


def _extract_context(
    content: str,
    line: int,
    context_lines: int = CONTEXT_LINES,
) -> tuple[str, int, int]:
    """Extract a code context window around a specific line.

    Args:
        content: Full file content.
        line: 1-based line number.
        context_lines: Number of lines before and after.

    Returns:
        Tuple of (context_string, start_line, end_line).
    """
    lines = content.splitlines()
    total = len(lines)

    start = max(0, line - 1 - context_lines)
    end = min(total, line + context_lines)

    context = "\n".join(lines[start:end])
    return context, start + 1, end


def _generate_diff(
    file_path: str,
    original: str,
    suggested: str,
) -> str:
    """Generate a unified diff between original and suggested code.

    Args:
        file_path: Path for the diff header.
        original: Original code snippet.
        suggested: Suggested replacement.

    Returns:
        Unified diff string.
    """
    original_lines = original.splitlines()
    suggested_lines = suggested.splitlines()

    rel = relative_path(file_path)
    diff = difflib.unified_diff(
        original_lines,
        suggested_lines,
        fromfile=f"a/{rel}",
        tofile=f"b/{rel}",
    )
    return "\n".join(diff)


def _parse_fix_response(
    content: str,
    file_path: str,
    line: int,
    code: str,
) -> AIFixSuggestion | None:
    """Parse an AI response into an AIFixSuggestion.

    Args:
        content: Raw AI response content.
        file_path: Path to the file.
        line: Line number of the issue.
        code: Error code of the issue.

    Returns:
        Parsed AIFixSuggestion, or None if parsing fails.
    """
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        logger.debug(f"Failed to parse AI fix response for {file_path}:{line}")
        return None

    original = data.get("original_code", "")
    suggested = data.get("suggested_code", "")

    if not original or not suggested or original == suggested:
        return None

    diff = _generate_diff(file_path, original, suggested)

    return AIFixSuggestion(
        file=file_path,
        line=line,
        code=code,
        original_code=original,
        suggested_code=suggested,
        diff=diff,
        explanation=data.get("explanation", ""),
        confidence=data.get("confidence", "medium"),
        risk_level=data.get("risk_level", ""),
    )


def _generate_single_fix(
    issue: BaseIssue,
    provider: BaseAIProvider,
    tool_name: str,
    file_cache: dict[str, str | None],
    cache_lock: threading.Lock,
    workspace_root: Path,
    max_tokens: int,
    max_retries: int = 2,
    timeout: float = 60.0,
) -> AIFixSuggestion | None:
    """Generate a fix suggestion for a single issue.

    Thread-safe — uses a lock for the shared file cache.

    Args:
        issue: The issue to fix.
        provider: AI provider instance.
        tool_name: Name of the tool.
        file_cache: Shared file content cache.
        cache_lock: Lock for thread-safe cache access.
        workspace_root: Root directory AI is allowed to edit/read.
        max_tokens: Maximum tokens to request from provider.
        max_retries: Maximum retry attempts for transient failures.
        timeout: Request timeout in seconds.

    Returns:
        AIFixSuggestion, or None if generation fails.
    """
    if not issue.file or not issue.line:
        logger.debug(
            f"Skipping issue without file/line: "
            f"file={issue.file!r} line={issue.line}",
        )
        return None

    resolved_file = resolve_workspace_file(issue.file, workspace_root)
    if resolved_file is None:
        logger.debug(
            f"Skipping issue outside workspace root: "
            f"file={issue.file!r}, root={workspace_root}",
        )
        return None
    issue_file = str(resolved_file)

    # Read file with thread-safe cache
    with cache_lock:
        if issue_file not in file_cache:
            file_cache[issue_file] = _read_file_safely(issue_file)
        file_content = file_cache[issue_file]

    if file_content is None:
        logger.debug(f"Cannot read file: {issue_file!r}")
        return None

    code = getattr(issue, "code", "") or ""
    context, context_start, context_end = _extract_context(
        file_content,
        issue.line,
    )

    prompt = FIX_PROMPT_TEMPLATE.format(
        tool_name=tool_name,
        code=code,
        file=to_provider_path(issue_file, workspace_root),
        line=issue.line,
        message=issue.message,
        context_start=context_start,
        context_end=context_end,
        code_context=context,
    )

    try:
        retrying_call = with_retry(max_retries=max_retries)(_call_provider)
        response = retrying_call(provider, prompt, FIX_SYSTEM, max_tokens, timeout)

        suggestion = _parse_fix_response(
            response.content,
            issue_file,
            issue.line,
            code,
        )

        if suggestion:
            suggestion.tool_name = tool_name
            suggestion.input_tokens = response.input_tokens
            suggestion.output_tokens = response.output_tokens
            suggestion.cost_estimate = response.cost_estimate
            return suggestion

    except Exception:
        logger.debug(
            f"AI fix generation failed for {issue.file}:{issue.line}",
            exc_info=True,
        )

    return None


def generate_fixes(
    issues: Sequence[BaseIssue],
    provider: BaseAIProvider,
    *,
    tool_name: str,
    max_issues: int = 20,
    max_workers: int = DEFAULT_MAX_WORKERS,
    workspace_root: Path | None = None,
    max_tokens: int = 2048,
    max_retries: int = 2,
    timeout: float = 60.0,
) -> list[AIFixSuggestion]:
    """Generate AI fix suggestions for unfixable issues.

    Reads the source file for each issue, sends context to the AI,
    and produces a unified diff. Runs API calls in parallel.

    Args:
        issues: Sequence of issues to fix.
        provider: AI provider instance.
        tool_name: Name of the tool that produced these issues.
        max_issues: Maximum number of issues to process.
        max_workers: Maximum concurrent API calls.
        workspace_root: Optional root directory limiting AI file access.
        max_tokens: Maximum tokens requested per fix generation call.
        max_retries: Maximum retry attempts for transient API failures.
        timeout: Request timeout in seconds per API call.

    Returns:
        List of fix suggestions.
    """
    if not issues:
        return []

    # Limit the number of issues to process
    target_issues = list(issues)[:max_issues]
    logger.debug(
        f"generate_fixes: {tool_name} received {len(issues)} issues, "
        f"processing {len(target_issues)} (max={max_issues})",
    )

    root = workspace_root or resolve_workspace_root()

    # Shared file cache with thread safety
    file_cache: dict[str, str | None] = {}
    cache_lock = threading.Lock()

    suggestions: list[AIFixSuggestion] = []

    workers = min(len(target_issues), max_workers)

    if workers <= 1:
        # Single issue — no thread pool overhead
        for issue in target_issues:
            result = _generate_single_fix(
                issue,
                provider,
                tool_name,
                file_cache,
                cache_lock,
                root,
                max_tokens,
                max_retries,
                timeout,
            )
            if result:
                suggestions.append(result)
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [
                executor.submit(
                    _generate_single_fix,
                    issue,
                    provider,
                    tool_name,
                    file_cache,
                    cache_lock,
                    root,
                    max_tokens,
                    max_retries,
                    timeout,
                )
                for issue in target_issues
            ]
            for future in as_completed(futures):
                result = future.result()
                if result:
                    suggestions.append(result)

    logger.debug(
        f"generate_fixes: {tool_name} produced "
        f"{len(suggestions)}/{len(target_issues)} suggestions",
    )
    return suggestions
