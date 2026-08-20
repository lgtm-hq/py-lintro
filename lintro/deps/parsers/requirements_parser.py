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

        Raises:
            ValueError: When any line is not a valid PEP 508 requirement. The
                check fails closed rather than silently dropping the line.
        """
        file = str(path)
        deps: list[Dependency] = []
        invalid: list[str] = []

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

            try:
                requirement = Requirement(line)
            except InvalidRequirement:
                invalid.append(f"line {lineno}: {line!r}")
                continue
            deps.append(
                build_dependency(
                    name=requirement.name,
                    version_spec=str(requirement.specifier),
                    ecosystem=Ecosystem.PYTHON,
                    file=file,
                    line=lineno,
                ),
            )

        if invalid:
            joined = "; ".join(invalid)
            msg = f"invalid requirement specification(s): {joined}"
            raise ValueError(msg)
        return deps
