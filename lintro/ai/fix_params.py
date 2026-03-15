"""Parameter objects for AI fix generation.

Groups the many parameters passed through the fix pipeline into
a single frozen dataclass, reducing argument-list bloat and making
the call signatures easier to read and maintain.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from lintro.ai.enums.sanitize_mode import SanitizeMode
from lintro.ai.fix_context import CONTEXT_LINES


@dataclass(frozen=True)
class FixGenParams:
    """Immutable parameter bundle for fix generation.

    Passed through ``generate_fixes`` → ``_generate_single_fix`` →
    ``build_fix_context`` so that adding a new parameter only requires
    one change site instead of threading it through every function.

    Attributes:
        tool_name: Name of the tool that produced these issues.
        workspace_root: Root directory limiting AI file access.
        max_tokens: Maximum tokens requested per fix generation call.
        max_retries: Maximum retry attempts for transient API failures.
        timeout: Request timeout in seconds per API call.
        context_lines: Lines of context before/after the issue line.
        max_prompt_tokens: Token budget for the prompt.
        base_delay: Initial retry delay in seconds.
        max_delay: Maximum retry delay in seconds.
        backoff_factor: Retry backoff multiplier.
        enable_cache: Whether to use suggestion deduplication cache.
        cache_ttl: Time-to-live in seconds for cached suggestions.
        max_issues: Maximum number of issues to process.
        max_workers: Maximum concurrent API calls.
        fallback_models: Ordered fallback model identifiers.
        sanitize_mode: How to handle prompt injection patterns.
        progress_callback: Optional callback after each fix completes.
    """

    tool_name: str = ""
    workspace_root: Path = field(default_factory=Path)
    max_tokens: int = 2048
    max_retries: int = 2
    timeout: float = 60.0
    context_lines: int = CONTEXT_LINES
    max_prompt_tokens: int = 12000
    base_delay: float = 1.0
    max_delay: float = 30.0
    backoff_factor: float = 2.0
    enable_cache: bool = False
    cache_ttl: int = 3600
    max_issues: int = 20
    max_workers: int = 5
    fallback_models: list[str] = field(default_factory=list)
    sanitize_mode: SanitizeMode = SanitizeMode.WARN
    progress_callback: Callable[[int, int], None] | None = None
