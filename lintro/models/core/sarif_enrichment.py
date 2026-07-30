"""Core-owned value object for optional AI enrichment of SARIF output.

The core SARIF renderer accepts ``ai_suggestions``/``ai_summary`` keywords but
must not know how to build them, because reconstructing them from tool
metadata requires :mod:`lintro.ai.models`. Core therefore passes this value
straight through, typed as ``Any`` on both members so no AI type is named
outside the AI layer (issue #724).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AISarifEnrichment:
    """Optional AI objects to fold into a SARIF render.

    Attributes:
        suggestions: Reconstructed AI fix suggestions, empty when AI is off.
        summary: Reconstructed AI run summary, or None when absent.
    """

    suggestions: list[Any] = field(default_factory=list)
    summary: Any | None = None
