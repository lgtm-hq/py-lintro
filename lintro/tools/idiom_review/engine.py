"""AI-calling engine for the ``idiom-review`` tool.

Keeps the tool plugin thin: the plugin discovers files and builds an
engine; the engine performs the provider calls (through the shared
``call_ai`` abstraction, so retry/fallback/budget all apply) and delegates
response parsing to :class:`IdiomReviewParser`. An optional content-hash
cache short-circuits unchanged inputs so repeat runs cost nothing.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from lintro.ai.invoke import call_ai
from lintro.parsers.idiom_review.idiom_review_parser import IdiomReviewParser
from lintro.tools.idiom_review.prompts import (
    build_duplication_prompt,
    build_file_review_prompt,
)
from lintro.tools.idiom_review.signatures import (
    Signature,
    render_signature_map,
)

if TYPE_CHECKING:
    from lintro.ai.budget import CostBudget
    from lintro.ai.config import AIConfig
    from lintro.ai.providers.base import BaseAIProvider
    from lintro.parsers.idiom_review.idiom_review_issue import IdiomReviewIssue

_CACHE_SUBDIR = ".lintro-cache/idiom"


class IdiomReviewMode(StrEnum):
    """Which review modes the tool should run."""

    PER_FILE = "per-file"
    DUPLICATION = "duplication"
    BOTH = "both"


def _cache_key(namespace: str, payload: str) -> str:
    """Return a short SHA-256 cache key for ``payload`` under ``namespace``.

    Args:
        namespace: Logical grouping (e.g. ``idiom:python``).
        payload: Content whose hash forms the key.

    Returns:
        A 16-character hex digest.
    """
    return hashlib.sha256(f"{namespace}:{payload}".encode()).hexdigest()[:16]


@dataclass
class IdiomReviewEngine:
    """Run idiom-review AI calls and parse their responses.

    Attributes:
        provider: Configured AI provider instance.
        ai_config: AI configuration (retry, timeout, token caps).
        budget: Optional session cost budget shared with ``--fix``.
        workspace_root: Project root; when set, raw responses are cached
            under ``.lintro-cache/idiom`` keyed by a content hash.
        cache_ttl: Cache time-to-live in seconds.
        parser: Response parser (injectable for testing).
    """

    provider: BaseAIProvider
    ai_config: AIConfig
    budget: CostBudget | None = None
    workspace_root: Path | None = None
    cache_ttl: int = 86_400
    parser: IdiomReviewParser = field(default_factory=IdiomReviewParser)

    # -- Caching -----------------------------------------------------------

    def _cache_file(self, key: str) -> Path | None:
        if self.workspace_root is None:
            return None
        return self.workspace_root / _CACHE_SUBDIR / f"{key}.json"

    def _cache_get(self, key: str) -> str | None:
        cache_file = self._cache_file(key)
        if cache_file is None or not cache_file.exists():
            return None
        try:
            data = json.loads(cache_file.read_text())
        except (json.JSONDecodeError, OSError):
            cache_file.unlink(missing_ok=True)
            return None
        timestamp = data.get("timestamp")
        if not isinstance(timestamp, (int, float)):
            return None
        if time.time() - timestamp > self.cache_ttl:
            cache_file.unlink(missing_ok=True)
            return None
        content = data.get("content")
        return content if isinstance(content, str) else None

    def _cache_put(self, key: str, content: str) -> None:
        cache_file = self._cache_file(key)
        if cache_file is None:
            return
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(
                json.dumps({"timestamp": time.time(), "content": content}),
            )
        except OSError as exc:
            logger.debug("[idiom-review] Failed to write cache: {}", exc)

    def _complete(self, *, system: str, user: str, cache_key: str) -> str:
        """Return the model response for a prompt, using the cache if set."""
        cached = self._cache_get(cache_key)
        if cached is not None:
            logger.debug("[idiom-review] Cache hit for {}", cache_key)
            return cached

        response = call_ai(
            provider=self.provider,
            ai_config=self.ai_config,
            user_prompt=user,
            system_prompt=system,
            budget=self.budget,
        )
        self._cache_put(cache_key, response.content)
        return response.content

    # -- Public API --------------------------------------------------------

    def review_file(
        self,
        *,
        file_path: str,
        source: str,
        language: str = "python",
    ) -> list[IdiomReviewIssue]:
        """Review a single file for idiomatic misses (Mode 1).

        Args:
            file_path: Path of the file being reviewed.
            source: Raw file content.
            language: Target language (only ``python`` today).

        Returns:
            Parsed idiom-review issues (empty when none / on parse failure).
        """
        if not source.strip():
            return []
        system, user = build_file_review_prompt(
            file_path=file_path,
            source=source,
            language=language,
        )
        key = _cache_key(f"idiom:{language}", source)
        content = self._complete(system=system, user=user, cache_key=key)
        return self.parser.parse_file_review(content, file_path)

    def review_duplication(
        self,
        signatures: list[Signature],
    ) -> list[IdiomReviewIssue]:
        """Detect cross-file duplicate logic (Mode 2).

        Args:
            signatures: Signatures collected across the scoped files.

        Returns:
            Parsed duplication issues (empty when none / on parse failure).
        """
        signature_map = render_signature_map(signatures)
        if not signature_map.strip():
            return []
        system, user = build_duplication_prompt(signature_map)
        key = _cache_key("idiom:duplication", signature_map)
        content = self._complete(system=system, user=user, cache_key=key)
        return self.parser.parse_duplication_review(content)
