"""Parser for pip ``requirements.txt`` files."""

from __future__ import annotations

from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement

from lintro.deps.models import Dependency, Ecosystem
from lintro.deps.parsers._base import build_dependency

__all__ = ["RequirementsParser"]


class RequirementsParser:
    """Parse ``requirements.txt``-style dependency lists."""

    def parse(self, path: Path) -> list[Dependency]:
        """Parse dependencies from a requirements file.

        Skips comments, blank lines, options (``-r``, ``--hash``), and
        non-versioned references (URLs, editable installs).

        Args:
            path: Path to the requirements file.

        Returns:
            list[Dependency]: Parsed dependencies.
        """
        file = str(path)
        deps: list[Dependency] = []

        for lineno, raw_line in enumerate(
            path.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            line = raw_line.strip()
            if not line or line.startswith(("#", "-")):
                continue
            # Strip inline comments and environment markers.
            line = line.split(" #", 1)[0].split(";", 1)[0].strip()
            if not line or "://" in line:
                continue

            dep = self._parse_line(line, file, lineno)
            if dep is not None:
                deps.append(dep)

        return deps

    @staticmethod
    def _parse_line(line: str, file: str, lineno: int) -> Dependency | None:
        """Parse a single requirement line.

        Args:
            line: Cleaned requirement text.
            file: Manifest path string.
            lineno: 1-based line number.

        Returns:
            Dependency | None: Parsed dependency, or ``None`` when invalid.
        """
        try:
            requirement = Requirement(line)
        except InvalidRequirement:
            return None
        return build_dependency(
            name=requirement.name,
            version_spec=str(requirement.specifier),
            ecosystem=Ecosystem.PYTHON,
            file=file,
            line=lineno,
        )
