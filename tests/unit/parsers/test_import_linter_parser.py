"""Unit tests for the import-linter output parser."""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.enums.severity_level import SeverityLevel
from lintro.parsers.import_linter import (
    ImportLinterIssue,
    parse_import_linter_output,
    parse_import_linter_summary,
)

ALL_KEPT_OUTPUT = """\
---------
Contracts
---------

Analyzed 4 files, 2 dependencies.
---------------------------------

Layered architecture KEPT

Contracts: 1 kept, 0 broken.
"""

NO_CONTRACTS_OUTPUT = """\
---------
Contracts
---------

Analyzed 4 files, 2 dependencies.
---------------------------------


Contracts: 0 kept, 0 broken.
"""

ONE_BROKEN_OUTPUT = """\
---------
Contracts
---------

Analyzed 6 files, 5 dependencies.
---------------------------------

Layered architecture BROKEN

Contracts: 0 kept, 1 broken.


----------------
Broken contracts
----------------

Layered architecture
--------------------

layered.storage is not allowed to import layered.api:

- layered.storage -> layered.helpers (l.6)
  layered.helpers -> layered.compat (l.3)
  layered.compat -> layered.api (l.3)

"""

TWO_BROKEN_OUTPUT = """\
---------
Contracts
---------

Analyzed 6 files, 6 dependencies.
---------------------------------

Layered architecture BROKEN
Low must not use b BROKEN

Contracts: 0 kept, 2 broken.


----------------
Broken contracts
----------------

Layered architecture
--------------------

mypkg.low is not allowed to import mypkg.high:

- mypkg.low -> mypkg.a (l.1)
  mypkg.a -> mypkg.high (l.1)


mypkg.mid is not allowed to import mypkg.high:

- mypkg.mid -> mypkg.high (l.2)


Low must not use b
------------------

mypkg.mid is not allowed to import mypkg.b:

-   mypkg.mid -> mypkg.b (l.2)

-   mypkg.mid -> mypkg.low (l.1)
    mypkg.low -> mypkg.a (l.1)
    mypkg.a -> mypkg.b (l.1)

"""


@pytest.fixture
def all_kept_output() -> str:
    """Return ``lint-imports`` output where every contract is kept.

    Returns:
        Raw tool output.
    """
    return ALL_KEPT_OUTPUT


@pytest.fixture
def no_contracts_output() -> str:
    """Return ``lint-imports`` output for a project with no contracts.

    Returns:
        Raw tool output.
    """
    return NO_CONTRACTS_OUTPUT


@pytest.fixture
def one_broken_output() -> str:
    """Return output with one broken contract carrying a three-hop chain.

    Returns:
        Raw tool output.
    """
    return ONE_BROKEN_OUTPUT


@pytest.fixture
def two_broken_output() -> str:
    """Return output with two broken contracts and several chains.

    Returns:
        Raw tool output.
    """
    return TWO_BROKEN_OUTPUT


def test_parse_all_kept_returns_no_issues(all_kept_output: str) -> None:
    """A run with every contract kept yields no issues.

    Args:
        all_kept_output: Output where the only contract is kept.
    """
    assert_that(parse_import_linter_output(all_kept_output)).is_empty()


def test_parse_no_contracts_returns_no_issues(no_contracts_output: str) -> None:
    """A project declaring no contracts yields no issues.

    Args:
        no_contracts_output: Output for a contract-free project.
    """
    assert_that(parse_import_linter_output(no_contracts_output)).is_empty()


@pytest.mark.parametrize("output", [None, "", "   \n\n"])
def test_parse_empty_output_returns_no_issues(output: str | None) -> None:
    """Empty or missing output parses to an empty issue list.

    Args:
        output: Empty, blank or missing tool output.
    """
    assert_that(parse_import_linter_output(output)).is_empty()


def test_parse_one_broken_contract_reports_chain(one_broken_output: str) -> None:
    """One broken chain becomes one issue carrying the full chain.

    Args:
        one_broken_output: Output with a single broken three-hop chain.
    """
    issues = parse_import_linter_output(one_broken_output)

    assert_that(issues).is_length(1)
    issue = issues[0]
    assert_that(issue).is_instance_of(ImportLinterIssue)
    assert_that(issue.file).is_equal_to("layered.storage")
    assert_that(issue.line).is_equal_to(0)
    assert_that(issue.column).is_equal_to(0)
    assert_that(issue.code).is_equal_to("Layered architecture")
    assert_that(issue.message).is_equal_to(
        "layered.storage -> layered.helpers -> layered.compat -> layered.api",
    )


def test_parse_two_broken_contracts_keeps_contract_names(
    two_broken_output: str,
) -> None:
    """Chains are attributed to the contract heading they appear under.

    Args:
        two_broken_output: Output with two broken contracts.
    """
    issues = parse_import_linter_output(two_broken_output)

    assert_that(issues).is_length(4)
    assert_that([issue.code for issue in issues]).is_equal_to(
        [
            "Layered architecture",
            "Layered architecture",
            "Low must not use b",
            "Low must not use b",
        ],
    )
    assert_that([issue.message for issue in issues]).is_equal_to(
        [
            "mypkg.low -> mypkg.a -> mypkg.high",
            "mypkg.mid -> mypkg.high",
            "mypkg.mid -> mypkg.b",
            "mypkg.mid -> mypkg.low -> mypkg.a -> mypkg.b",
        ],
    )


def test_issue_severity_defaults_to_error(one_broken_output: str) -> None:
    """A broken contract is reported at ERROR severity.

    Args:
        one_broken_output: Output with a single broken chain.
    """
    issues = parse_import_linter_output(one_broken_output)

    assert_that(issues[0].get_severity()).is_equal_to(SeverityLevel.ERROR)


def test_to_display_row_exposes_contract_and_chain(one_broken_output: str) -> None:
    """The display row carries the contract name and the import chain.

    Args:
        one_broken_output: Output with a single broken chain.
    """
    row = parse_import_linter_output(one_broken_output)[0].to_display_row()

    assert_that(row["file"]).is_equal_to("layered.storage")
    assert_that(row["code"]).is_equal_to("Layered architecture")
    assert_that(row["message"]).contains("layered.compat -> layered.api")


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        (ALL_KEPT_OUTPUT, (1, 0)),
        (NO_CONTRACTS_OUTPUT, (0, 0)),
        (ONE_BROKEN_OUTPUT, (0, 1)),
        (TWO_BROKEN_OUTPUT, (0, 2)),
    ],
)
def test_parse_summary_counts(output: str, expected: tuple[int, int]) -> None:
    """The "Contracts: N kept, M broken" line parses into counts.

    Args:
        output: Raw tool output.
        expected: Expected ``(kept, broken)`` tuple.
    """
    assert_that(parse_import_linter_summary(output)).is_equal_to(expected)


@pytest.mark.parametrize("output", [None, "", "Analyzed 4 files, 2 dependencies."])
def test_parse_summary_missing_returns_none(output: str | None) -> None:
    """Output without a summary line yields None rather than zero counts.

    Args:
        output: Output carrying no summary line.
    """
    assert_that(parse_import_linter_summary(output)).is_none()


@pytest.mark.parametrize(
    "hop",
    [
        "- mypkg.low -> mypkg.high (l.1)",
        "- mypkg.low -> mypkg.high (l.1, l.5)",
        "- mypkg.low -> mypkg.high (l.?)",
        "- mypkg.low -> mypkg.high",
    ],
)
def test_parse_accepts_every_line_number_rendering(hop: str) -> None:
    """import-linter renders line numbers as a list and may not know them.

    Args:
        hop: A bullet line using one of the rendered line-number forms.
    """
    output = f"""\
----------------
Broken contracts
----------------

Layered architecture
--------------------

mypkg.low is not allowed to import mypkg.high:

{hop}
"""

    issues = parse_import_linter_output(output)

    assert_that(issues).is_length(1)
    assert_that(issues[0].message).is_equal_to("mypkg.low -> mypkg.high")


def test_parse_ignores_truncated_chain() -> None:
    """A bullet with no complete hop is discarded rather than half-parsed."""
    truncated = """\
----------------
Broken contracts
----------------

Layered architecture
--------------------

mypkg.low is not allowed to import mypkg.high:

- mypkg.low ->
"""

    assert_that(parse_import_linter_output(truncated)).is_empty()
