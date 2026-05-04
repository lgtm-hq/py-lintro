"""``GhcrVersion`` dataclass."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GhcrVersion:
    """Container version metadata minimal subset.

    Attributes:
        id: Numeric version id.
        tags: List of tags bound to this version.
        created_at: ISO timestamp when version was created.
        name: The manifest digest/name for this version.
    """

    id: int = field(default=0)
    tags: list[str] = field(default_factory=list)
    created_at: str = field(default="")
    name: str = field(default="")
