"""OSV-Scanner's error log is read fail-closed (#3352).

A run is a complete scan only when every error osv-scanner printed is one of
the allow-listed harmless shapes. These tests feed the reader real standard
error from osv-scanner 2.4.0 and 2.7.1 (:mod:`._captured_stderr`), shuffled,
duplicated and mixed with text it has never seen, and check that nothing
outside the allow-list can ever make a run complete.
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Sequence
from pathlib import Path
from typing import TypeVar
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.enums.action import Action
from lintro.models.core.subprocess_result import SubprocessResult
from lintro.models.core.tool_result import ToolResult
from lintro.tools.osv_scanner.definition import OsvScannerPlugin
from lintro.tools.osv_scanner.extraction_log import (
    INFO_PREFIXES,
    read_extraction_log,
    sort_extraction_errors,
)
from lintro.utils.meaningful_run import baseline_is_eligible
from tests.unit.tools.osv_scanner._captured_stderr import (
    CAPTURES,
    CASE_FILES,
    Capture,
)

_EMPTY_REPORT = '{"results": [], "experimental_config": {}}'

#: Captures whose every error is a registry answer, so the scan is complete.
_HARMLESS = tuple(c for c in CAPTURES if c.network == "ok")

#: Captures where the resolution service could not be reached.
_OUTAGES = tuple(
    c for c in CAPTURES if c.network == "resolverdown" and "rpc error" in c.stderr
)

#: Error text osv-scanner never printed in the captures, every one of which
#: must leave a run incomplete wherever it appears.
_UNSEEN_ERRORS: tuple[str, ...] = (
    "error when retrieving vulns: connection refused",
    "context deadline exceeded",
    "x509: certificate signed by unknown authority",
    "dial tcp: lookup api.deps.dev: no such host",
    "HTTP 503 Service Unavailable",
    "Failed to load the local database",
    "failed resolution for {root}/private/requirements.txt: context deadline exceeded",
    "failed resolution for {root}/private/requirements.txt: "
    "max retries exhausted: failed after 4 attempts: package System(7):zz: not found",
    "failed resolution for {root}/private/requirements.txt: "
    "package System(7):zz: not found (status 404)",
    "failed resolution for {root}/private/requirements.txt: "
    "rpc error: code = DeadlineExceeded desc = timeout: not found",
    "failed resolution for {root}/private/requirements.txt: resolution impossible:",
    "failed resolution for {root}/missing/requirements.txt: "
    "package System(7):zz: not found",
    "Error during extraction: (extracting as transitivedependency/requirements) "
    "failed resolution for {root}/private/requirements.txt: "
    "rpc error: code = Unavailable desc = timeout",
    "Error during extraction: (extracting as vulnmatch/osvdev) max retries exceeded",
    "Error during extraction: (extracting as transitivedependency/javaarchive) "
    "failed resolution for {root}/pom/pom.xml: package System(7):zz: not found",
    "\tprivate/requirements.txt: could not extract",
    'requirements conflict: flask: "==1" (after a timeout)',
)


_T = TypeVar("_T")


class _Draws:
    """A seeded, repeatable source of choices for the shuffled inputs.

    A fixed 64-bit linear congruential sequence, so every run feeds the
    reader the same inputs and a failure can be replayed.
    """

    def __init__(self, seed: int) -> None:
        """Start the sequence.

        Args:
            seed: Where the sequence starts.
        """
        self._state = seed

    def below(self, bound: int) -> int:
        """Return the next draw in ``range(bound)``.

        Args:
            bound: One past the largest value.

        Returns:
            The draw.
        """
        self._state = (self._state * 6364136223846793005 + 1442695040888963407) % (
            2**64
        )
        return (self._state >> 33) % bound

    def between(self, low: int, high: int) -> int:
        """Return the next draw from ``low`` to ``high`` inclusive.

        Args:
            low: The smallest value.
            high: The largest value.

        Returns:
            The draw.
        """
        return low + self.below(high - low + 1)

    def choice(self, items: Sequence[_T]) -> _T:
        """Return the next draw from a sequence.

        Args:
            items: The candidates.

        Returns:
            One of them.
        """
        return items[self.below(len(items))]


def _real_lines(captures: tuple[Capture, ...]) -> list[str]:
    """Return every distinct line of some captures, in first-seen order.

    Args:
        captures: The captures.

    Returns:
        The lines, with ``{root}`` left in place.
    """
    return list(
        dict.fromkeys(line for c in captures for line in c.stderr.splitlines()),
    )


def _random_unseen(rng: _Draws) -> str:
    """Return a line of arbitrary text no allow-list shape can match.

    Args:
        rng: Seeded source of draws.

    Returns:
        A non-blank line that is not progress output.
    """
    alphabet = "abcdefghijklmnopqrstuvwxyz :/.\"'=-_()[]{}0123456789"
    body = "".join(rng.choice(alphabet) for _ in range(rng.between(1, 60)))
    return f"#{body}"


@pytest.fixture
def scan_root(tmp_path: Path) -> Path:
    """Create every manifest the captured runs scanned.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        The directory standing in for ``{root}``.
    """
    for name, content in CASE_FILES.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    return tmp_path


def _fill(lines: list[str], root: Path) -> str:
    """Join stderr lines and put the scanned root in place.

    Args:
        lines: The lines, with ``{root}``.
        root: The scanned directory.

    Returns:
        The standard error text.
    """
    return "\n".join(lines).replace("{root}", str(root).lstrip("/")) + "\n"


def _complete(stderr: str, root: Path) -> bool:
    """Return whether the reader would let exit 127 stand as a verdict.

    Args:
        stderr: What osv-scanner printed.
        root: The scanned directory.

    Returns:
        True when no source was skipped and every error is accounted for.
    """
    errors = sort_extraction_errors(stderr, root, is_excluded=lambda _: False)
    return errors.explains_failed_exit and not errors.skipped


def _is_error_line(line: str) -> bool:
    """Return whether a line is more than progress output or a blank.

    Args:
        line: One standard-error line.

    Returns:
        True when the reader must account for it.
    """
    return bool(line.strip()) and not line.startswith(INFO_PREFIXES)


def test_every_real_harmless_run_is_complete(scan_root: Path) -> None:
    """Each healthy-network capture with an error is a complete scan."""
    with_errors = [c for c in _HARMLESS if "Error during extraction" in c.stderr]
    assert_that(with_errors).is_not_empty()

    for capture in with_errors:
        stderr = _fill(capture.stderr.splitlines(), scan_root)
        assert_that(_complete(stderr, scan_root)).described_as(
            f"{capture.version} {capture.case}",
        ).is_true()


def test_every_real_outage_is_incomplete(scan_root: Path) -> None:
    """Each capture with the resolution service down is incomplete."""
    assert_that(_OUTAGES).is_length(16)

    for capture in _OUTAGES:
        stderr = _fill(capture.stderr.splitlines(), scan_root)
        assert_that(_complete(stderr, scan_root)).described_as(
            f"{capture.version} {capture.case}",
        ).is_false()


def _harmless_units() -> list[list[str]]:
    """Return the error lines of each harmless capture as one unit.

    Returns:
        One list of lines per capture that printed an error.
    """
    units = []
    for capture in _HARMLESS:
        unit = [line for line in capture.stderr.splitlines() if _is_error_line(line)]
        if unit:
            units.append(unit)
    return units


def test_harmless_runs_in_every_order_and_repeated_are_complete(
    scan_root: Path,
) -> None:
    """Order and repetition of harmless output never change the verdict."""
    units = list({tuple(u): u for u in _harmless_units()}.values())
    sample = units[:6]
    for order in itertools.permutations(sample):
        lines = [line for unit in order for line in unit]
        assert_that(_complete(_fill(lines, scan_root), scan_root)).is_true()
    rng = _Draws(3352)
    for _ in range(200):
        chosen = [rng.choice(units) for _ in range(rng.between(1, 8))]
        lines = [line for unit in chosen for line in unit]
        assert_that(_complete(_fill(lines, scan_root), scan_root)).is_true()


def test_any_unrecognised_line_anywhere_makes_the_run_incomplete(
    scan_root: Path,
) -> None:
    """One error outside the allow-list, at any position, fails closed."""
    rng = _Draws(1352)
    bad = [
        *(line for line in _real_lines(_OUTAGES) if "rpc error" in line),
        *_UNSEEN_ERRORS,
        *(_random_unseen(rng) for _ in range(40)),
    ]
    units = _harmless_units()
    for line in bad:
        chosen = [rng.choice(units) for _ in range(rng.between(1, 4))]
        lines = [entry for unit in chosen for entry in unit]
        for position in range(len(lines) + 1):
            mixed = [*lines[:position], line, *lines[position:]]
            assert_that(_complete(_fill(mixed, scan_root), scan_root)).described_as(
                f"{line!r} at {position}",
            ).is_false()


def test_shuffled_real_lines_are_complete_only_without_a_bad_line(
    scan_root: Path,
) -> None:
    """Any order and repetition of real lines: complete implies all harmless.

    The lines are drawn from every capture of both versions plus unseen
    text, then shuffled and repeated, so headers, warnings and cause lines
    land in orders the real tool never prints.
    """
    rng = _Draws(2352)
    harmless = set(_real_lines(_HARMLESS))
    pool = [
        *_real_lines(CAPTURES),
        *_UNSEEN_ERRORS,
        *(_random_unseen(rng) for _ in range(20)),
    ]
    seen_complete = 0
    for _ in range(3000):
        lines = [rng.choice(pool) for _ in range(rng.between(1, 12))]
        if _complete(_fill(lines, scan_root), scan_root):
            seen_complete += 1
            unknown = [ln for ln in lines if _is_error_line(ln) and ln not in harmless]
            assert_that(unknown).described_as(repr(lines)).is_empty()
    # The draw must reach complete runs too, or the implication says nothing.
    assert_that(seen_complete).is_greater_than(0)


def test_every_line_has_exactly_one_owner(scan_root: Path) -> None:
    """Each error line belongs to one entry, echo or the unexplained set."""
    rng = _Draws(4352)
    pool = [*_real_lines(CAPTURES), *_UNSEEN_ERRORS]
    for _ in range(500):
        lines = [rng.choice(pool) for _ in range(rng.between(1, 12))]
        log = read_extraction_log(_fill(lines, scan_root), scan_root)
        owned = [
            index for entry in (*log.entries, *log.echoes) for index in entry.lines
        ]
        owners = [*owned, *log.unexplained]
        assert_that(len(owners)).is_equal_to(len(set(owners)))
        errors = {i for i, line in enumerate(log.lines) if _is_error_line(line)}
        assert_that(errors - set(owners)).is_empty()


def _check(
    plugin: OsvScannerPlugin,
    root: Path,
    *,
    returncode: int,
    stderr: str,
    stdout: str = _EMPTY_REPORT,
) -> ToolResult:
    """Run the plugin's check against one recorded osv-scanner process.

    Args:
        plugin: Plugin under test.
        root: The scanned directory.
        returncode: Exit status of osv-scanner.
        stderr: Its standard error, ``{root}`` already filled in.
        stdout: Its standard output.

    Returns:
        The plugin's result.
    """
    proc = SubprocessResult(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        output=stdout + stderr,
    )
    with patch.object(plugin, "_run_subprocess_result", return_value=proc):
        return plugin.check([str(root)], {"check_suppressions": False})


def _assert_incomplete(result: ToolResult) -> None:
    """Assert a result is an incomplete run a baseline may not use.

    Args:
        result: The plugin's result.
    """
    assert_that(result.success).is_false()
    assert_that(result.execution_failed).is_true()
    assert_that(result.findings_complete).is_false()
    assert_that(result.inspected_files).is_none()
    assert_that(
        baseline_is_eligible(
            action=Action.CHECK,
            dry_run_preview=False,
            tool_results=[result],
        ),
    ).is_false()


def _flask_report(source: Path) -> str:
    """Return an osv-scanner report naming one vulnerable flask pin.

    Args:
        source: The manifest the finding is reported for.

    Returns:
        The JSON report.
    """
    package = {"name": "flask", "version": "3.0.0", "ecosystem": "PyPI"}
    return json.dumps(
        {
            "results": [
                {
                    "source": {"path": str(source), "type": "lockfile"},
                    "packages": [
                        {
                            "package": package,
                            "vulnerabilities": [{"id": "GHSA-68rp-wp8r-4726"}],
                            "groups": [{"ids": ["GHSA-68rp-wp8r-4726"]}],
                        },
                    ],
                },
            ],
        },
    )


@pytest.mark.parametrize(
    "capture",
    [c for c in _OUTAGES if c.case in {"private", "clean", "privvuln"}],
    ids=lambda c: f"{c.version}-{c.case}",
)
def test_resolution_service_outage_is_an_incomplete_run(
    osv_scanner_plugin: OsvScannerPlugin,
    scan_root: Path,
    capture: Capture,
) -> None:
    """deps.dev unreachable, vulnerability lookups fine: never a clean scan.

    Reproduces the dead ``HTTPS_PROXY`` with ``NO_PROXY`` set for the OSV
    API: osv-scanner exits 127 (or 1 beside a finding) with only a failed
    resolution logged.
    """
    root = scan_root / capture.case
    stdout = (
        _flask_report(root / "requirements.txt")
        if capture.case == "privvuln"
        else _EMPTY_REPORT
    )
    stderr = _fill(capture.stderr.splitlines(), scan_root)

    result = _check(
        osv_scanner_plugin,
        root,
        returncode=capture.returncode,
        stderr=stderr,
        stdout=stdout,
    )

    _assert_incomplete(result)
    assert_that(result.output).contains(
        "requirements.txt: ",
    ).contains("rpc error: code = Unavailable")
    assert_that(result.metadata).does_not_contain_key("notes")


def test_repeated_error_line_is_never_absorbed_by_a_resolution_block(
    osv_scanner_plugin: OsvScannerPlugin,
    scan_root: Path,
) -> None:
    """The same error before and after a harmless block is still an error."""
    lookup = "error when retrieving vulns: connection refused"
    private = next(c for c in _HARMLESS if c.case == "private" and c.version == "2.7.1")
    lines = [lookup, *private.stderr.splitlines(), lookup]
    root = scan_root / "private"

    result = _check(
        osv_scanner_plugin,
        root,
        returncode=127,
        stderr=_fill(lines, scan_root),
    )

    _assert_incomplete(result)
    assert_that(result.output).contains(lookup)
