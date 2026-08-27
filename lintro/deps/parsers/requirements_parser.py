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

        Skips comments, blank lines, and pip options. Follows ``-r`` /
        ``--requirement`` includes. Strips trailing ``--hash`` options.
        Skips non-versioned references (URLs, editable installs).

        Args:
            path: Path to the requirements file.

        Returns:
            list[Dependency]: Parsed dependencies.

        Raises:
            ValueError: When any line is not a valid PEP 508 requirement, or
                an included file is missing. The check fails closed rather
                than silently dropping the line.
        """
        return self._parse_path(path, seen=set())

    def _parse_path(self, path: Path, seen: set[Path]) -> list[Dependency]:
        """Parse one requirements file, following ``-r`` includes.

        Args:
            path: Path to the requirements file.
            seen: Already-visited resolved paths, used to break cycles.

        Returns:
            list[Dependency]: Parsed dependencies from this file and includes.

        Raises:
            ValueError: When a line is not a valid PEP 508 requirement, or an
                included file is missing.
        """
        resolved = path.resolve()
        if resolved in seen:
            return []
        seen.add(resolved)

        file = str(path)
        deps: list[Dependency] = []
        invalid: list[str] = []

        for lineno, raw_line in enumerate(
            path.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            line = line.split(" #", 1)[0].strip()
            include = self._include_target(line)
            if include is not None:
                deps.extend(
                    self._parse_include(
                        path=path,
                        include=include,
                        seen=seen,
                    ),
                )
                continue
            if line.startswith("-"):
                continue
            # Strip trailing pip options (``--hash``, ``--only-binary``, …).
            line = line.split(" --", 1)[0].split(";", 1)[0].strip()
            if not line or "://" in line:
                continue

            try:
                requirement = Requirement(line)
            except InvalidRequirement:
                invalid.append(f"line {lineno}: {line!r}")
                continue
            if requirement.url:
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

    def _parse_include(
        self,
        *,
        path: Path,
        include: str,
        seen: set[Path],
    ) -> list[Dependency]:
        """Parse a ``-r`` / ``--requirement`` include.

        Args:
            path: The file that referenced the include.
            include: Relative or absolute include path.
            seen: Already-visited resolved paths.

        Returns:
            list[Dependency]: Dependencies from the included file.

        Raises:
            ValueError: When the included file does not exist.
        """
        include_path = Path(include)
        if not include_path.is_absolute():
            include_path = path.parent / include_path
        if not include_path.is_file():
            msg = f"included requirements file not found: {include}"
            raise ValueError(msg)
        return self._parse_path(include_path, seen)

    @staticmethod
    def _include_target(line: str) -> str | None:
        """Return an include path from a ``-r`` / ``--requirement`` line.

        Args:
            line: A stripped requirements line.

        Returns:
            str | None: The include path, or ``None`` when the line is not
            an include directive.
        """
        if line.startswith("-r ") or line.startswith("--requirement "):
            return line.split(None, 1)[1].strip()
        if line.startswith("--requirement="):
            return line.split("=", 1)[1].strip()
        return None
