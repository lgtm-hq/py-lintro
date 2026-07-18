"""PHPStan parser package.

Exposes the PHPStan issue model and the output parser used by the PHPStan
tool plugin to convert ``--error-format=json`` output into lintro issues.
"""

from lintro.parsers.phpstan.phpstan_issue import PhpstanIssue
from lintro.parsers.phpstan.phpstan_parser import parse_phpstan_output

__all__ = ["PhpstanIssue", "parse_phpstan_output"]
