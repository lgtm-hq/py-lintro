"""Parameter objects for AI summary generation.

Groups the retry/provider parameters shared by ``generate_summary``
and ``generate_post_fix_summary`` into a single frozen dataclass.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class SummaryGenParams:
    """Immutable parameter bundle for summary generation.

    Attributes:
        max_tokens: Maximum tokens for the response.
        workspace_root: Root for provider-safe path redaction.
        timeout: Request timeout in seconds per API call.
        max_retries: Maximum retry attempts for transient failures.
        base_delay: Initial retry delay in seconds.
        max_delay: Maximum retry delay in seconds.
        backoff_factor: Retry backoff multiplier.
        fallback_models: Ordered fallback model identifiers.
    """

    max_tokens: int = 2048
    workspace_root: Path | None = None
    timeout: float = 60.0
    max_retries: int = 2
    base_delay: float | None = None
    max_delay: float | None = None
    backoff_factor: float | None = None
    fallback_models: list[str] = field(default_factory=list)
