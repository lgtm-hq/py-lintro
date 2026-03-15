"""Context building and file reading for AI fix generation.

Provides utilities for reading source files, extracting code context
windows, validating issues, checking the suggestion cache, and
constructing fix prompts with appropriate context sizing.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from lintro.ai.cache import get_cached_suggestion
from lintro.ai.enums.sanitize_mode import SanitizeMode
from lintro.ai.paths import resolve_workspace_file, to_provider_path
from lintro.ai.prompts import FIX_PROMPT_TEMPLATE
from lintro.ai.sanitize import (
    detect_injection_patterns,
    make_boundary_marker,
    sanitize_code_content,
)
from lintro.ai.secrets import redact_secrets
from lintro.ai.token_budget import estimate_tokens

if TYPE_CHECKING:
    from lintro.ai.models import AIFixSuggestion
    from lintro.parsers.base_issue import BaseIssue

# Context window around the issue line (lines before/after)
CONTEXT_LINES = 15

# Maximum file cache entries to limit memory usage
_MAX_CACHE_ENTRIES = 100

# Only attempt full-file context for files under this many lines
FULL_FILE_THRESHOLD = 500


def read_file_safely(file_path: str) -> str | None:
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


def extract_context(
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


def validate_and_read_file(
    issue: BaseIssue,
    file_cache: dict[str, str | None],
    cache_lock: threading.Lock,
    workspace_root: Path,
) -> tuple[str, str] | None:
    """Validate the issue and read its file content.

    Returns (issue_file, file_content) or None if validation fails.
    Thread-safe — uses a lock for the shared file cache.
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

    with cache_lock:
        if issue_file not in file_cache:
            if len(file_cache) >= _MAX_CACHE_ENTRIES:
                oldest_key = next(iter(file_cache))
                del file_cache[oldest_key]
            file_cache[issue_file] = read_file_safely(issue_file)
        file_content = file_cache[issue_file]

    if file_content is None:
        logger.debug(f"Cannot read file: {issue_file!r}")
        return None

    return issue_file, file_content


def check_cache(
    workspace_root: Path,
    file_content: str,
    code: str,
    issue: BaseIssue,
    tool_name: str,
    cache_ttl: int,
) -> AIFixSuggestion | None:
    """Check the suggestion dedup cache and return a hit if found."""
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
        cached.tool_name = tool_name
        cached.input_tokens = 0
        cached.output_tokens = 0
        cached.cost_estimate = 0.0
        return cached
    return None


def build_fix_context(
    issue: BaseIssue,
    issue_file: str,
    file_content: str,
    tool_name: str,
    code: str,
    workspace_root: Path,
    context_lines: int,
    max_prompt_tokens: int,
    full_file_threshold: int,
    sanitize_mode: SanitizeMode = SanitizeMode.WARN,
) -> str | None:
    """Sanitize content and build the fix prompt with appropriate context.

    Tries full-file context for small files, falls back to windowed
    context that progressively shrinks to fit the token budget.
    """
    sanitized_content = redact_secrets(sanitize_code_content(file_content))
    if sanitize_mode != SanitizeMode.OFF:
        injections = detect_injection_patterns(file_content)
        if injections:
            if sanitize_mode == SanitizeMode.BLOCK:
                logger.warning(
                    f"Blocking fix for {issue.file}: prompt injection "
                    f"patterns detected: {', '.join(injections)}",
                )
                return None
            # SanitizeMode.WARN (default)
            logger.warning(
                f"Potential prompt injection patterns detected in "
                f"{issue.file}: {', '.join(injections)}",
            )

    total_lines = len(file_content.splitlines())
    if total_lines <= full_file_threshold:
        boundary = make_boundary_marker()
        full_prompt = FIX_PROMPT_TEMPLATE.format(
            tool_name=tool_name,
            code=code,
            file=to_provider_path(issue_file, workspace_root),
            line=issue.line,
            message=issue.message,
            context_start=1,
            context_end=total_lines,
            code_context=sanitized_content,
            boundary=boundary,
        )
        if estimate_tokens(full_prompt) <= max_prompt_tokens:
            logger.debug(
                f"Using full file context ({total_lines} lines) for "
                f"{issue.file}:{issue.line}",
            )
            return full_prompt

    effective_context_lines = context_lines
    _min_context = 3
    while True:
        context, context_start, context_end = extract_context(
            file_content,
            issue.line,
            context_lines=effective_context_lines,
        )
        boundary = make_boundary_marker()
        sanitized_context = redact_secrets(sanitize_code_content(context))
        prompt = FIX_PROMPT_TEMPLATE.format(
            tool_name=tool_name,
            code=code,
            file=to_provider_path(issue_file, workspace_root),
            line=issue.line,
            message=issue.message,
            context_start=context_start,
            context_end=context_end,
            code_context=sanitized_context,
            boundary=boundary,
        )
        if (
            estimate_tokens(prompt) <= max_prompt_tokens
            or effective_context_lines <= _min_context
        ):
            return prompt
        old_ctx = effective_context_lines
        effective_context_lines = max(
            _min_context,
            effective_context_lines // 2,
        )
        logger.debug(
            f"Fix prompt over budget for {issue.file}:{issue.line} "
            f"reducing context_lines {old_ctx} -> {effective_context_lines}",
        )
