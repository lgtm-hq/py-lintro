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

The ``acyclic_siblings`` contract type prints its dependencies relative to the
package it checked instead::

    Acyclic siblings
    ----------------

    No cycles are allowed in mypkg.
    It could be made acyclic by removing 1 dependency:

    - .b -> .a (1 import)

Those hops are resolved against the package named on the preceding
``No cycles are allowed in`` line, so the chain above is ``mypkg.b -> mypkg.a``.
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

# First line of an uncaught Python exception report. After the summary line it
# means the run crashed part-way through its report; before the summary it is
# only text printed ahead of a report that then completed (a warning, say).
TRACEBACK_HEADER: str = "Traceback (most recent call last):"

# "mypkg.low -> mypkg.a (l.1)". The trailing parenthetical is optional (not every
# contract type reports one) and import-linter renders it as a comma-separated
# list, using "l.?" when the line number is unknown — e.g. "(l.1, l.5)", "(l.?)".
IMPORT_LINTER_HOP_PATTERN = re.compile(
    r"^(?P<importer>[A-Za-z_][\w.]*)\s*->\s*(?P<imported>[A-Za-z_][\w.]*)"
    r"(?:\s*\(l\.[^)]*\))?$",
)

# "No cycles are allowed in mypkg." names the package an acyclic_siblings block
# checked; the relative hops beneath it are resolved against that package.
IMPORT_LINTER_CYCLE_PACKAGE_PATTERN = re.compile(
    r"^No cycles are allowed in (?P<package>[A-Za-z_][\w.]*)\.$",
)

# ".b -> .a (1 import)": an acyclic_siblings dependency between two children of
# the package named above it. The import count is optional.
IMPORT_LINTER_RELATIVE_HOP_PATTERN = re.compile(
    r"^\.(?P<importer>[A-Za-z_]\w*)\s*->\s*\.(?P<imported>[A-Za-z_]\w*)"
    r"(?:\s*\(\d+ imports?\))?$",
)

# A heading underline such as "--------------------". import-linter underlines a
# heading with exactly ``len(title)`` dashes (``print_heading`` in
# ``importlinter.application.output``), so a contract named "IO" is underlined
# with two dashes and one named "A" with a single dash. Any run of dashes must
# therefore count, or short contract names would never be recognised as
# headings and their chains would be filed under an empty contract name.
IMPORT_LINTER_UNDERLINE_PATTERN = re.compile(r"^-+$")


@dataclass
class _Chain:
    """Mutable accumulator for one import chain under a contract.

    Attributes:
        contract: Name of the contract the chain breaks.
        package: Package an ``acyclic_siblings`` block is about, used to
            resolve its relative hops; empty elsewhere.
        modules: Dotted module names in chain order (importer first).
    """

    contract: str = field(default="")
    package: str = field(default="")
    modules: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ImportLinterReport:
    """The chains in one ``lint-imports`` output, plus the report's structure.

    Attributes:
        issues: One issue per parsed import chain.
        broken: Broken-contract count from the summary line, or None when the
            output has no summary line.
        sections: Contract sections under the ``Broken contracts`` heading,
            counted by heading, so two contracts sharing a name count twice.
            None when the heading is missing.
        unparsed_sections: Sections under that heading that yielded no chain.
        crashed: Whether a Python traceback follows the summary line.
    """

    issues: list[ImportLinterIssue] = field(default_factory=list)
    broken: int | None = None
    sections: int | None = None
    unparsed_sections: int = 0
    crashed: bool = False

    @property
    def is_complete_broken_report(self) -> bool:
        """Whether a failing run printed a complete broken-contracts report.

        ``lint-imports`` exits non-zero both when contracts are broken and when
        it cannot check them (bad configuration, unimportable package, a crash
        part-way through). Only the first is a lint verdict. It is recognised
        from the report's structure:

        * a summary line with a broken count above zero;
        * the underlined ``Broken contracts`` heading;
        * at least that many contract sections beneath it;
        * no traceback after the summary line.

        Whether each section's chains could be parsed does not matter here: an
        unparsed section is a gap in this parser (``unparsed_sections``), not a
        sign that the tool stopped early.

        Returns:
            True when the output is a complete broken-contracts report.
        """
        return (
            self.broken is not None
            and self.broken > 0
            and self.sections is not None
            and self.sections >= self.broken
            and not self.crashed
        )


def _is_underline(line: str) -> bool:
    """Report whether a line is a Markdown-style heading underline.

    Args:
        line: Raw output line.

    Returns:
        True when the line consists only of dashes.
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
        A fresh, empty accumulator for the same contract and package.
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
    return _Chain(contract=chain.contract, package=chain.package)


def _append_hop(chain: _Chain, importer: str, imported: str) -> None:
    """Extend a chain with one importer -> imported hop.

    Args:
        chain: Chain accumulator to extend.
        importer: Dotted name of the importing module.
        imported: Dotted name of the imported module.
    """
    if not chain.modules:
        chain.modules.append(importer)
    chain.modules.append(imported)


def _match_hop(chain: _Chain, hop_text: str) -> tuple[str, str] | None:
    """Resolve one hop line to absolute ``(importer, imported)`` names.

    Args:
        chain: Current accumulator; its ``package`` resolves relative hops.
        hop_text: The hop with any leading bullet removed.

    Returns:
        The two module names, or None when the line is not a hop.
    """
    hop = IMPORT_LINTER_HOP_PATTERN.match(hop_text)
    if hop is not None:
        return hop.group("importer"), hop.group("imported")
    relative = IMPORT_LINTER_RELATIVE_HOP_PATTERN.match(hop_text)
    if relative is not None and chain.package:
        return (
            f"{chain.package}.{relative.group('importer')}",
            f"{chain.package}.{relative.group('imported')}",
        )
    return None


def has_uncaught_exception(text: str | None) -> bool:
    """Report whether text contains a Python traceback block.

    ``lint-imports`` catches errors raised while it reads configuration and
    builds the graph, and prints them on stdout itself. It does not catch errors
    raised while it prints the report, so those reach Python's default handler,
    which writes the traceback to stderr and exits 1, the same status as broken
    contracts. A traceback on stderr is therefore the only sign that a report
    which looks complete was cut short.

    Args:
        text: Captured stream, or None.

    Returns:
        True when a line starts a traceback block.
    """
    if not text:
        return False
    return any(
        line.startswith(TRACEBACK_HEADER)
        for line in strip_ansi_codes(text).splitlines()
    )


def _find_summary(lines: list[str]) -> tuple[int, int] | None:
    """Locate the summary line.

    Args:
        lines: Output split into lines, ANSI codes removed.

    Returns:
        The summary's line index and its broken count, or None when there is
        no summary line.
    """
    for index, line in enumerate(lines):
        match = IMPORT_LINTER_SUMMARY_PATTERN.match(line.strip())
        if match:
            return index, int(match.group("broken"))
    return None


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


def _parse_sections(
    lines: list[str],
    start: int,
) -> tuple[list[ImportLinterIssue], list[int]]:
    """Parse the chains under the ``Broken contracts`` heading.

    Args:
        lines: Output split into lines, ANSI codes removed.
        start: Index of the first line after the heading's underline.

    Returns:
        The parsed issues, and how many issues each contract section produced,
        in report order.
    """
    issues: list[ImportLinterIssue] = []
    section_starts: list[int] = []
    chain = _Chain()
    index = start
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        if _is_contract_heading(lines, index):
            _flush(chain, issues)
            section_starts.append(len(issues))
            chain = _Chain(contract=stripped)
            index += 2  # skip the heading's underline
            continue

        cycle = IMPORT_LINTER_CYCLE_PACKAGE_PATTERN.match(stripped)
        if cycle is not None:
            chain = _flush(chain, issues)
            chain.package = cycle.group("package")
            index += 1
            continue

        if not stripped or _is_underline(stripped) or stripped.endswith(":"):
            chain = _flush(chain, issues)
            index += 1
            continue

        starts_chain = stripped.startswith("-")
        hop_text = stripped.lstrip("-").strip() if starts_chain else stripped
        hop = _match_hop(chain, hop_text)
        if hop is None:
            logger.debug(f"Line did not match import-linter chain pattern: {line}")
            chain = _flush(chain, issues)
            index += 1
            continue

        if starts_chain:
            chain = _flush(chain, issues)
        _append_hop(chain, *hop)
        index += 1

    _flush(chain, issues)
    ends = [*section_starts[1:], len(issues)] if section_starts else []
    counts = [end - begin for begin, end in zip(section_starts, ends, strict=True)]
    return issues, counts


def parse_import_linter_report(output: str | None) -> ImportLinterReport:
    """Parse ``lint-imports`` output into its chains and report structure.

    Args:
        output: The raw text output from ``lint-imports``, or None.

    Returns:
        The parsed report; empty (no summary, no sections) for empty output.
    """
    if output is None or not output.strip():
        return ImportLinterReport()

    lines = strip_ansi_codes(output).splitlines()
    summary = _find_summary(lines)
    broken = summary[1] if summary is not None else None
    crashed = summary is not None and any(
        line.startswith(TRACEBACK_HEADER) for line in lines[summary[0] + 1 :]
    )
    start = _find_broken_section_start(lines)
    if start is None:
        logger.debug("No 'Broken contracts' section in import-linter output")
        return ImportLinterReport(broken=broken, crashed=crashed)

    issues, counts = _parse_sections(lines, start)
    return ImportLinterReport(
        issues=issues,
        broken=broken,
        sections=len(counts),
        unparsed_sections=sum(1 for count in counts if count == 0),
        crashed=crashed,
    )


def parse_import_linter_output(output: str | None) -> list[ImportLinterIssue]:
    """Parse ``lint-imports`` text output into issue objects.

    Args:
        output: The raw text output from ``lint-imports``, or None.

    Returns:
        List of :class:`ImportLinterIssue` objects, one per broken import
        chain. Empty when every contract is kept or the output is empty.
    """
    return parse_import_linter_report(output).issues
