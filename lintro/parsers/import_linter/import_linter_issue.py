"""Import-linter issue model.

This module defines the dataclass representing a single broken import chain
reported by ``lint-imports`` (import-linter), plus the one synthetic issue the
plugin emits when ``lint-imports`` aborts before it evaluates any contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from lintro.enums.severity_level import SeverityLevel
from lintro.parsers.base_issue import BaseIssue

#: ``code`` of the synthetic issue emitted when ``lint-imports`` fails before
#: producing a contract report. Contract names come from user configuration, so
#: this value is chosen to read as a label rather than as a contract.
IMPORT_LINTER_EXECUTION_ERROR_CODE: str = "Execution error"


@dataclass
class ImportLinterIssue(BaseIssue):
    """Represents a broken import chain found by import-linter.

    import-linter checks architectural contracts rather than individual
    source lines, so ``file`` carries the importing module's dotted path and
    ``line`` is always ``0``.

    Attributes:
        DEFAULT_SEVERITY: Defaults to ERROR (a broken contract fails the run).
        code: Name of the broken contract (e.g. ``"Layered architecture"``).
    """

    DEFAULT_SEVERITY: ClassVar[SeverityLevel] = SeverityLevel.ERROR

    code: str = field(default="")

    @classmethod
    def execution_failure(
        cls,
        *,
        config_path: str,
        returncode: int,
        reason: str,
    ) -> ImportLinterIssue:
        """Build the issue reported when ``lint-imports`` aborts early.

        ``lint-imports`` exits non-zero without a contract report when it
        cannot start checking: an unmatched ``ignore_imports`` entry under
        ``unmatched_ignore_imports_alerting = "error"``, a root package that is
        not importable, a malformed configuration. Without an issue the run
        would count zero issues, and because the formatters render only the
        issue table once issues exist, the reason has to travel in the issue
        itself rather than only in ``ToolResult.output`` (#2371).

        The issue is anchored to the configuration file (the thing the user
        must change) and carries :data:`IMPORT_LINTER_EXECUTION_ERROR_CODE` in
        place of a contract name.

        Args:
            config_path: Configuration file ``lint-imports`` was run with.
            returncode: Exit code ``lint-imports`` returned.
            reason: The tool's own diagnostic text, already stripped.

        Returns:
            One ERROR-severity issue describing the failed run.
        """
        return cls(
            file=config_path,
            line=0,
            column=0,
            code=IMPORT_LINTER_EXECUTION_ERROR_CODE,
            message=(
                f"lint-imports exited {returncode} before checking contracts: "
                f"{reason}"
            ),
        )
