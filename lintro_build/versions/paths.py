"""Path resolution for the tool-version generator."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Self


@dataclass(frozen=True, slots=True)
class GeneratorPaths:
    """Filesystem locations the version generator reads and writes.

    Attributes:
        repo_root: Repository root every other path is derived from.
        seed_path: Seed mapping at ``lintro/_tool_packages.py``.
        tool_versions_path: Binary tool pins at ``lintro/_tool_versions.py``.
        package_json_path: npm pins at ``package.json``.
        pyproject_path: pypi pins at ``pyproject.toml``.
        manifest_src_path: Hand-authored manifest source at
            ``lintro/tools/manifest.src.json`` (no tool version keys).
        manifest_path: Rendered manifest output at
            ``lintro/tools/manifest.json``.
        generated_path: Output module at ``lintro/_generated_versions.py``.
    """

    repo_root: Path
    seed_path: Path
    tool_versions_path: Path
    package_json_path: Path
    pyproject_path: Path
    manifest_src_path: Path
    manifest_path: Path
    generated_path: Path

    @classmethod
    def from_repo_root(cls, repo_root: Path) -> Self:
        """Derive the standard generator paths from a repository root.

        Args:
            repo_root: Repository root directory.

        Returns:
            Paths bound to ``repo_root`` following the repository layout.
        """
        return cls(
            repo_root=repo_root,
            seed_path=repo_root / "lintro" / "_tool_packages.py",
            tool_versions_path=repo_root / "lintro" / "_tool_versions.py",
            package_json_path=repo_root / "package.json",
            pyproject_path=repo_root / "pyproject.toml",
            manifest_src_path=repo_root / "lintro" / "tools" / "manifest.src.json",
            manifest_path=repo_root / "lintro" / "tools" / "manifest.json",
            generated_path=repo_root / "lintro" / "_generated_versions.py",
        )
