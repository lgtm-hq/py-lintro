"""AI modification audit log."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lintro.ai.models import AIFixSuggestion

AUDIT_DIR = ".lintro-cache/ai"
AUDIT_FILE = "audit.json"


def write_audit_log(
    workspace_root: Path,
    applied: list[AIFixSuggestion],
    rejected_count: int,
    total_cost: float,
) -> None:
    """Write an audit log summarising the AI fix run.

    Args:
        workspace_root: Project root directory.
        applied: List of applied fix suggestions.
        rejected_count: Number of rejected suggestions.
        total_cost: Cumulative cost in USD.
    """
    entries = []
    for s in applied:
        entries.append(
            {
                "file": s.file,
                "line": s.line,
                "code": s.code,
                "tool": s.tool_name,
                "action": "applied",
                "confidence": s.confidence,
                "risk_level": s.risk_level,
                "cost": s.cost_estimate,
            },
        )
    audit = {
        "timestamp": time.time(),
        "applied_count": len(applied),
        "rejected_count": rejected_count,
        "total_cost_usd": round(total_cost, 6),
        "entries": entries,
    }
    audit_dir = workspace_root / AUDIT_DIR
    audit_dir.mkdir(parents=True, exist_ok=True)
    (audit_dir / AUDIT_FILE).write_text(
        json.dumps(audit, indent=2),
        encoding="utf-8",
    )
