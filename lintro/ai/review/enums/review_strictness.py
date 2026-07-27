"""Review strictness / sensitivity presets.

Compatibility re-export: the enum now lives in :mod:`lintro.enums` so that
``lintro.config.review_config`` can reference it without importing
``lintro.ai`` (issue #724).
"""

from __future__ import annotations

from lintro.enums.review_strictness import ReviewStrictness

__all__ = ["ReviewStrictness"]
