"""TsconfigInfo dataclass for TypeScript project metadata."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class TsconfigInfo:
    """Parsed tsconfig metadata for a single TypeScript project."""

    path: Path
    """Absolute path to the tsconfig.json file."""

    project_dir: Path
    """Parent directory of the tsconfig (the project root)."""

    include_patterns: list[str] = field(default_factory=list)
    """Resolved ``include`` glob patterns from the extends chain."""

    exclude_patterns: list[str] = field(default_factory=list)
    """Resolved ``exclude`` patterns from the extends chain."""

    files_list: list[str] = field(default_factory=list)
    """Explicit ``files`` entries from the extends chain."""

    references: list[Path] = field(default_factory=list)
    """Resolved ``references`` paths (to other tsconfig files)."""

    is_composite: bool = False
    """Whether ``compilerOptions.composite`` is ``true``."""

    raw_config: dict[str, Any] = field(default_factory=dict)
    """The parsed JSONC content of the tsconfig file itself."""
