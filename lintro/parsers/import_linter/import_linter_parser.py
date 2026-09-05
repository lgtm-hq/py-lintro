"""Parser for import-linter (``lint-imports``) text output.

``lint-imports`` prints a human-readable report. A run with broken contracts
looks like this::

    ---------
    Contracts
    ---------

    Analyzed 6 files, 6 dependencies.
    ---------------------------------

    Layered architecture BROKEN

    Contracts: 0 kept, 1 broken.

    ----------------
    Broken contracts
    ----------------

    Layered architecture
    --------------------

    mypkg.low is not allowed to import mypkg.high:

    - mypkg.low -> mypkg.a (l.1)
      mypkg.a -> mypkg.high (l.1)

Each bullet introduces one import chain: the direct import on the bullet line
plus any indented continuation hops. One :class:`ImportLinterIssue` is emitted
per chain, keyed on the contract it broke.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from loguru import logger

from lintro.parsers.base_parser import strip_ansi_codes
from lintro.parsers.import_linter.import_linter_issue import ImportLinterIssue

# Heading that introduces the per-contract breakdown. Everything before it is
# the summary block, which carries no chain detail.
BROKEN_CONTRACTS_HEADING: str = "Broken contracts"

# "Contracts: 3 kept, 2 broken."
IMPORT_LINTER_SUMMARY_PATTERN = re.compile(
    r"^Contracts:\s*(?P<kept>\d+)\s+kept,\s*(?P<broken>\d+)\s+broken\.?$",
)

# "mypkg.low -> mypkg.a (l.1)". The trailing parenthetical is optional (not every
# contract type reports one) and import-linter renders it as a comma-separated
# list, using "l.?" when the line number is unknown — e.g. "(l.1, l.5)", "(l.?)".
IMPORT_LINTER_HOP_PATTERN = re.compile(
    r"^(?P<importer>[A-Za-z_][\w.]*)\s*->\s*(?P<imported>[A-Za-z_][\w.]*)"
    r"(?:\s*\(l\.[^)]*\))?$",
)

# A heading underline such as "--------------------".
IMPORT_LINTER_UNDERLINE_PATTERN = re.compile(r"^-{3,}$")


@dataclass
class _Chain:
    """Mutable accumulator for one import chain under a contract.

    Attributes:
        contract: Name of the contract the chain breaks.
        modules: Dotted module names in chain order (importer first).
    """

    contract: str = field(default="")
    modules: list[str] = field(default_factory=list)


def _is_underline(line: str) -> bool:
    """Report whether a line is a Markdown-style heading underline.

    Args:
        line: Raw output line.

    Returns:
        True when the line consists only of three or more dashes.
    """
    return bool(IMPORT_LINTER_UNDERLINE_PATTERN.match(line.strip()))


def _find_broken_section_start(lines: list[str]) -> int | None:
    """Locate the first line after the "Broken contracts" heading.

    Args:
        lines: Output split into lines.

    Returns:
        Index of the line after the heading's underline, or None when the
        report contains no broken-contracts section.
    """
    for index, line in enumerate(lines):
        if line.strip() != BROKEN_CONTRACTS_HEADING:
            continue
        if index + 1 < len(lines) and _is_underline(lines[index + 1]):
            return index + 2
    return None


def _is_contract_heading(lines: list[str], index: int) -> bool:
    """Report whether ``lines[index]`` is a contract-name heading.

    Contract names are printed flush left and underlined with dashes.

    Args:
        lines: Output split into lines.
        index: Index of the candidate heading line.

    Returns:
        True when the line names a contract.
    """
    line = lines[index]
    if not line.strip() or line != line.lstrip() or _is_underline(line):
        return False
    return index + 1 < len(lines) and _is_underline(lines[index + 1])


def _flush(chain: _Chain, issues: list[ImportLinterIssue]) -> _Chain:
    """Append the accumulated chain as an issue and start a fresh accumulator.

    Args:
        chain: Chain accumulated so far.
        issues: Issue list to append to.

    Returns:
        A fresh, empty chain accumulator.
    """
    # _append_hop always adds importer and imported together, so a chain either
    # holds nothing or at least two modules; there is no half-built state.
    if chain.modules:
        issues.append(
            ImportLinterIssue(
                file=chain.modules[0],
                line=0,
                column=0,
                code=chain.contract,
                message=" -> ".join(chain.modules),
            ),
        )
    return _Chain(contract=chain.contract)


def _append_hop(chain: _Chain, hop: re.Match[str]) -> None:
    """Extend a chain with one importer -> imported hop.

    Args:
        chain: Chain accumulator to extend.
        hop: Match produced by :data:`IMPORT_LINTER_HOP_PATTERN`.
    """
    importer = hop.group("importer")
    imported = hop.group("imported")
    if not chain.modules:
        chain.modules.append(importer)
    chain.modules.append(imported)


def parse_import_linter_summary(output: str | None) -> tuple[int, int] | None:
    """Parse the "Contracts: N kept, M broken." summary line.

    Args:
        output: Raw text output from ``lint-imports``, or None.

    Returns:
        Tuple of ``(kept, broken)`` counts, or None when no summary line is
        present (e.g. the tool failed before running any contract).
    """
    if not output:
        return None
    for line in strip_ansi_codes(output).splitlines():
        match = IMPORT_LINTER_SUMMARY_PATTERN.match(line.strip())
        if match:
            return int(match.group("kept")), int(match.group("broken"))
    return None


def parse_import_linter_output(output: str | None) -> list[ImportLinterIssue]:
    """Parse ``lint-imports`` text output into issue objects.

    Args:
        output: The raw text output from ``lint-imports``, or None.

    Returns:
        List of :class:`ImportLinterIssue` objects, one per broken import
        chain. Empty when every contract is kept or the output is empty.
    """
    issues: list[ImportLinterIssue] = []
    if output is None or not output.strip():
        return issues

    lines = strip_ansi_codes(output).splitlines()
    start = _find_broken_section_start(lines)
    if start is None:
        logger.debug("No 'Broken contracts' section in import-linter output")
        return issues

    chain = _Chain()
    index = start
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        if _is_contract_heading(lines, index):
            chain = _flush(chain, issues)
            chain.contract = stripped
            index += 2  # skip the heading's underline
            continue

        if not stripped or _is_underline(stripped) or stripped.endswith(":"):
            chain = _flush(chain, issues)
            index += 1
            continue

        starts_chain = stripped.startswith("-")
        hop_text = stripped.lstrip("-").strip() if starts_chain else stripped
        hop = IMPORT_LINTER_HOP_PATTERN.match(hop_text)
        if hop is None:
            logger.debug(f"Line did not match import-linter chain pattern: {line}")
            chain = _flush(chain, issues)
            index += 1
            continue

        if starts_chain:
            chain = _flush(chain, issues)
        _append_hop(chain, hop)
        index += 1

    _flush(chain, issues)
    return issues
