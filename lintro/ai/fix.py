"""AI fix generation service.

Generates fix suggestions for issues that native tools cannot auto-fix.
Reads file contents, asks the AI for a corrected version, and produces
unified diffs. Supports parallel API calls for improved performance.
"""

from __future__ import annotations

import difflib
import json
import threading
from collections import defaultdict
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from lintro.ai.cache import cache_suggestion, get_cached_suggestion
from lintro.ai.models import AIFixSuggestion
from lintro.ai.paths import (
    relative_path,
    resolve_workspace_file,
    resolve_workspace_root,
    to_provider_path,
)
from lintro.ai.prompts import FIX_BATCH_PROMPT_TEMPLATE, FIX_PROMPT_TEMPLATE, FIX_SYSTEM
from lintro.ai.retry import with_retry
from lintro.ai.token_budget import estimate_tokens

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

# Maximum file cache entries to limit memory usage
_MAX_CACHE_ENTRIES = 100

# Only attempt full-file context for files under this many lines
FULL_FILE_THRESHOLD = 500


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
    retrying_call: Callable[..., AIResponse],
    timeout: float = 60.0,
    context_lines: int = CONTEXT_LINES,
    max_prompt_tokens: int = 12000,
    enable_cache: bool = False,
    cache_ttl: int = 3600,
    full_file_threshold: int = FULL_FILE_THRESHOLD,
) -> AIFixSuggestion | None:
    """Generate a fix suggestion for a single issue.

    Thread-safe — uses a lock for the shared file cache.

    For small files (under *full_file_threshold* lines) whose content
    fits within *max_prompt_tokens*, the entire file is sent as context.
    Otherwise the builder progressively reduces ``context_lines``
    (halving each iteration, minimum 3) and rebuilds the prompt until
    it fits.

    Args:
        issue: The issue to fix.
        provider: AI provider instance.
        tool_name: Name of the tool.
        file_cache: Shared file content cache.
        cache_lock: Lock for thread-safe cache access.
        workspace_root: Root directory AI is allowed to edit/read.
        max_tokens: Maximum tokens to request from provider.
        retrying_call: Pre-built retry wrapper around ``_call_provider``.
        timeout: Request timeout in seconds.
        context_lines: Lines of context before/after the issue line.
        max_prompt_tokens: Token budget for the prompt (4 chars ~ 1 token).
        enable_cache: Whether to use the suggestion deduplication cache.
        cache_ttl: Time-to-live in seconds for cached suggestions.
        full_file_threshold: Max lines to attempt full-file context
            (default 500).

    Returns:
        AIFixSuggestion, or None if generation fails.

    Raises:
        KeyboardInterrupt: Re-raised on user interrupt.
        SystemExit: Re-raised on system exit.
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

    # Read file with thread-safe cache (evict oldest when at capacity)
    with cache_lock:
        if issue_file not in file_cache:
            if len(file_cache) >= _MAX_CACHE_ENTRIES:
                oldest_key = next(iter(file_cache))
                del file_cache[oldest_key]
            file_cache[issue_file] = _read_file_safely(issue_file)
        file_content = file_cache[issue_file]

    if file_content is None:
        logger.debug(f"Cannot read file: {issue_file!r}")
        return None

    code = getattr(issue, "code", "") or ""

    # Check suggestion cache before calling the AI provider
    if enable_cache:
        cached = get_cached_suggestion(
            workspace_root,
            file_content,
            code,
            issue.line,
            issue.message,
            ttl=cache_ttl,
        )
        if cached is not None:
            logger.debug(
                f"Cache hit for {issue.file}:{issue.line} ({code})",
            )
            cached_suggestion = AIFixSuggestion(
                file=cached.get("file", issue_file),
                line=cached.get("line", issue.line),
                code=cached.get("code", code),
                original_code=cached.get("original_code", ""),
                suggested_code=cached.get("suggested_code", ""),
                diff=cached.get("diff", ""),
                explanation=cached.get("explanation", ""),
                confidence=cached.get("confidence", "medium"),
                risk_level=cached.get("risk_level", ""),
            )
            cached_suggestion.tool_name = tool_name
            return cached_suggestion

    # Try full-file context for small files that fit within the budget
    total_lines = len(file_content.splitlines())
    use_full_file = False
    if total_lines <= full_file_threshold:
        full_prompt = FIX_PROMPT_TEMPLATE.format(
            tool_name=tool_name,
            code=code,
            file=to_provider_path(issue_file, workspace_root),
            line=issue.line,
            message=issue.message,
            context_start=1,
            context_end=total_lines,
            code_context=file_content,
        )
        if estimate_tokens(full_prompt) <= max_prompt_tokens:
            prompt = full_prompt
            use_full_file = True
            logger.debug(
                f"Using full file context ({total_lines} lines) for "
                f"{issue.file}:{issue.line}",
            )

    # Fall back to windowed context, progressively reducing if over budget
    if not use_full_file:
        effective_context_lines = context_lines
        _min_context = 3
        while True:
            context, context_start, context_end = _extract_context(
                file_content,
                issue.line,
                context_lines=effective_context_lines,
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
            if (
                estimate_tokens(prompt) <= max_prompt_tokens
                or effective_context_lines <= _min_context
            ):
                break
            old_ctx = effective_context_lines
            effective_context_lines = max(
                _min_context,
                effective_context_lines // 2,
            )
            logger.debug(
                f"Fix prompt over budget for {issue.file}:{issue.line} "
                f"reducing context_lines {old_ctx} -> {effective_context_lines}",
            )

    try:
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

            if enable_cache:
                cache_suggestion(
                    workspace_root,
                    file_content,
                    code,
                    issue.line,
                    issue.message,
                    {
                        "file": suggestion.file,
                        "line": suggestion.line,
                        "code": suggestion.code,
                        "original_code": suggestion.original_code,
                        "suggested_code": suggestion.suggested_code,
                        "diff": suggestion.diff,
                        "explanation": suggestion.explanation,
                        "confidence": suggestion.confidence,
                        "risk_level": suggestion.risk_level,
                    },
                )

            return suggestion

    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.debug(
            f"AI fix generation failed for {issue.file}:{issue.line} "
            f"({type(exc).__name__}: {exc})",
            exc_info=True,
        )

    return None


def _parse_batch_response(
    content: str,
    file_path: str,
) -> list[AIFixSuggestion]:
    """Parse a batch AI response into a list of AIFixSuggestions.

    Args:
        content: Raw AI response content (expected JSON array).
        file_path: Path to the file.

    Returns:
        List of parsed AIFixSuggestions (may be empty on parse failure).
    """
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        logger.debug(f"Failed to parse batch AI response for {file_path}")
        return []

    if not isinstance(data, list):
        logger.debug(f"Batch response is not an array for {file_path}")
        return []

    results: list[AIFixSuggestion] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        original = item.get("original_code", "")
        suggested = item.get("suggested_code", "")
        if not original or not suggested or original == suggested:
            continue
        line = item.get("line", 0)
        code = item.get("code", "")
        diff = _generate_diff(file_path, original, suggested)
        results.append(
            AIFixSuggestion(
                file=file_path,
                line=line,
                code=code,
                original_code=original,
                suggested_code=suggested,
                diff=diff,
                explanation=item.get("explanation", ""),
                confidence=item.get("confidence", "medium"),
                risk_level=item.get("risk_level", ""),
            ),
        )
    return results


def _generate_batch_fixes(
    file_path: str,
    file_issues: list[BaseIssue],
    provider: BaseAIProvider,
    tool_name: str,
    file_content: str,
    workspace_root: Path,
    max_tokens: int,
    retrying_call: Callable[..., AIResponse],
    timeout: float,
    max_prompt_tokens: int,
) -> list[AIFixSuggestion] | None:
    """Generate fixes for multiple issues in one file via a batch prompt.

    Returns a list of suggestions on success, or None if the batch prompt
    does not fit within the token budget or the response cannot be parsed
    (signalling the caller to fall back to single-issue mode).

    Args:
        file_path: Resolved absolute file path.
        file_issues: Issues in this file (must have len >= 2).
        provider: AI provider instance.
        tool_name: Name of the tool.
        file_content: Full file content string.
        workspace_root: Root directory for workspace-relative paths.
        max_tokens: Maximum tokens to request from provider.
        retrying_call: Pre-built retry wrapper around ``_call_provider``.
        timeout: Request timeout in seconds.
        max_prompt_tokens: Token budget for the prompt.

    Returns:
        List of AIFixSuggestions, or None on failure (fall back to single).

    Raises:
        KeyboardInterrupt: Re-raised immediately.
        SystemExit: Re-raised immediately.
    """
    issues_list_parts: list[str] = []
    for idx, issue in enumerate(file_issues, 1):
        code = getattr(issue, "code", "") or ""
        issues_list_parts.append(
            f"{idx}. Line {issue.line} [{code}]: {issue.message}",
        )
    issues_list = "\n".join(issues_list_parts)

    prompt = FIX_BATCH_PROMPT_TEMPLATE.format(
        tool_name=tool_name,
        file=to_provider_path(file_path, workspace_root),
        issues_list=issues_list,
        file_content=file_content,
    )

    if estimate_tokens(prompt) > max_prompt_tokens:
        logger.debug(
            f"Batch prompt over budget for {file_path} "
            f"({len(file_issues)} issues), falling back to single-issue mode",
        )
        return None

    try:
        response = retrying_call(provider, prompt, FIX_SYSTEM, max_tokens, timeout)
        suggestions = _parse_batch_response(response.content, file_path)
        if not suggestions:
            logger.debug(
                f"Batch response parse returned no suggestions for {file_path}, "
                f"falling back to single-issue mode",
            )
            return None

        for suggestion in suggestions:
            suggestion.tool_name = tool_name
            suggestion.input_tokens = response.input_tokens
            suggestion.output_tokens = response.output_tokens
            suggestion.cost_estimate = response.cost_estimate

        logger.debug(
            f"Batch fix generated {len(suggestions)} suggestions "
            f"for {file_path} ({len(file_issues)} issues)",
        )
        return suggestions

    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.debug(
            f"Batch AI fix generation failed for {file_path} "
            f"({type(exc).__name__}: {exc}), falling back to single-issue mode",
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
    context_lines: int = CONTEXT_LINES,
    max_prompt_tokens: int = 12000,
    base_delay: float | None = None,
    max_delay: float | None = None,
    backoff_factor: float | None = None,
    enable_cache: bool = False,
    cache_ttl: int = 3600,
    progress_callback: Callable[[int, int], None] | None = None,
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
        context_lines: Lines of context before/after the issue line.
        max_prompt_tokens: Token budget for the prompt before context trimming.
        base_delay: Initial retry delay in seconds (None = use default).
        max_delay: Maximum retry delay in seconds (None = use default).
        backoff_factor: Retry backoff multiplier (None = use default).
        enable_cache: Whether to use the suggestion deduplication cache.
        cache_ttl: Time-to-live in seconds for cached suggestions.
        progress_callback: Optional callback invoked after each fix
            completes with (completed_count, total_count).

    Returns:
        List of fix suggestions.

    Raises:
        KeyboardInterrupt: Re-raised on user interrupt.
        SystemExit: Re-raised on system exit.
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

    # Shared file cache with thread safety (capped to limit memory usage)
    file_cache: dict[str, str | None] = {}
    cache_lock = threading.Lock()

    # Build the retry wrapper once and share across all calls.
    retrying_call = with_retry(
        max_retries=max_retries,
        base_delay=base_delay if base_delay is not None else 1.0,
        max_delay=max_delay if max_delay is not None else 30.0,
        backoff_factor=backoff_factor if backoff_factor is not None else 2.0,
    )(_call_provider)

    suggestions: list[AIFixSuggestion] = []
    completed_count = 0
    total_count = len(target_issues)

    # --- Multi-issue batching per file ---
    # Group issues by resolved file path; files with 2+ issues are
    # candidates for a single batch prompt.
    file_groups: dict[str, list[BaseIssue]] = defaultdict(list)
    for issue in target_issues:
        if not issue.file or not issue.line:
            continue
        resolved = resolve_workspace_file(issue.file, root)
        if resolved is None:
            continue
        file_groups[str(resolved)].append(issue)

    single_issues: list[BaseIssue] = []

    for resolved_path, group in file_groups.items():
        if len(group) < 2:
            single_issues.extend(group)
            continue

        # Read the file for the batch prompt
        content = _read_file_safely(resolved_path)
        if content is None:
            single_issues.extend(group)
            continue

        # Populate file_cache so single-fix fallback doesn't re-read
        with cache_lock:
            file_cache[resolved_path] = content

        batch_result = _generate_batch_fixes(
            resolved_path,
            group,
            provider,
            tool_name,
            content,
            root,
            max_tokens,
            retrying_call,
            timeout,
            max_prompt_tokens,
        )
        if batch_result is not None:
            suggestions.extend(batch_result)
            completed_count += len(group)
            if progress_callback is not None:
                progress_callback(completed_count, total_count)
        else:
            # Fall back to single-issue mode for this file
            single_issues.extend(group)

    # Include issues that had no file/line (skipped by grouping) —
    # _generate_single_fix will skip them gracefully.
    for issue in target_issues:
        if not issue.file or not issue.line:
            single_issues.append(issue)

    workers = min(len(single_issues), max_workers) if single_issues else 0

    if workers <= 1:
        for issue in single_issues:
            result = _generate_single_fix(
                issue,
                provider,
                tool_name,
                file_cache,
                cache_lock,
                root,
                max_tokens,
                retrying_call,
                timeout,
                context_lines,
                enable_cache=enable_cache,
                cache_ttl=cache_ttl,
            )
            if result:
                suggestions.append(result)
            completed_count += 1
            if progress_callback is not None:
                progress_callback(completed_count, total_count)
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
                    retrying_call,
                    timeout,
                    context_lines,
                    enable_cache=enable_cache,
                    cache_ttl=cache_ttl,
                )
                for issue in single_issues
            ]
            for future in as_completed(futures):
                try:
                    result = future.result()
                except (KeyboardInterrupt, SystemExit):
                    raise
                except Exception as exc:
                    logger.debug(
                        f"AI fix worker failed ({type(exc).__name__}: {exc})",
                        exc_info=True,
                    )
                    completed_count += 1
                    if progress_callback is not None:
                        progress_callback(completed_count, total_count)
                    continue
                if result:
                    suggestions.append(result)
                completed_count += 1
                if progress_callback is not None:
                    progress_callback(completed_count, total_count)

    # Sort by (file, line) for deterministic ordering regardless of
    # thread completion order from as_completed().
    suggestions.sort(key=lambda s: (s.file, s.line))

    logger.debug(
        f"generate_fixes: {tool_name} produced "
        f"{len(suggestions)}/{len(target_issues)} suggestions",
    )
    return suggestions
