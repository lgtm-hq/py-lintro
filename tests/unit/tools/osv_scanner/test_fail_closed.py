"""OSV-Scanner's standard error is read fail-closed (#3352).

lintro runs osv-scanner at ``--verbosity error``, where a scan that went well
prints nothing. A run is complete only when everything printed is an
allow-listed resolution note or an error for an excluded file that names no
other path, stdout is exactly one JSON report, and "No package sources found"
is no files only as the exact whole line. These tests mix the notes
osv-scanner 2.4.0 and 2.7.1 really print with other errors and with arbitrary
text, in every order and at every exit status, and check that nothing else can
leave a run complete. The oracle is the lists below, written out here rather
than taken from the reader.
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
from lintro.utils.meaningful_run import baseline_is_eligible

_EMPTY_REPORT = '{"results": [], "experimental_config": {}}'

#: Manifests the messages below name, created under the scan root.
_FILES: dict[str, str] = {
    "private/requirements.txt": "six==1.16.0\nzz-private==1.2.3\n",
    "a/requirements.txt": "zz-a==1\n",
    "b/requirements.txt": "zz-b==1\n",
    "pom/pom.xml": "<project/>\n",
    "impossible/requirements.txt": "flask=====\n",
    "broken/package-lock.json": "not json{\n",
    "two/a/requirements.txt": "flask==99999.0.0\n",
    "two/b/requirements.txt": "flask==99999.0.0\n",
    "conflict/requirements.txt": "flask==2.0.0\nwerkzeug==1.0.0\n",
    "excluded/package-lock.json": "not json{\n",
}

#: The pattern every run below excludes; only ``excluded/`` matches it.
_EXCLUDED_DIR = "excluded"

_TRANSITIVE = "Error during extraction: (extracting as transitivedependency/"

#: The harmless messages, as osv-scanner 2.4.0 and 2.7.1 print them at
#: ``--verbosity error``, with ``{root}`` for the scanned directory.
_ALLOWED: tuple[str, ...] = (
    f"{_TRANSITIVE}requirements) failed resolution for "
    "{root}/private/requirements.txt: package System(7):zz-private: not found",
    f"{_TRANSITIVE}requirements) failed resolution for "
    "{root}/a/requirements.txt: package System(7):zz-a: not found\n"
    "failed resolution for {root}/b/requirements.txt: package "
    "System(7):zz-b: not found",
    f"{_TRANSITIVE}pomxml) failed resolution for {{root}}/pom/pom.xml: "
    "failed resolving {Maven:com.example:demo[Concrete:1.0] {}}: version "
    "Maven:com.zzprivate:zz-internal[Concrete:1.0.0]: not found",
    f"{_TRANSITIVE}requirements) failed resolution for "
    "{root}/impossible/requirements.txt: resolution impossible:\n"
    'no candidates at all for: flask "====="',
    # Two unsatisfiable manifests: the resolver's error ends in a newline, so
    # a blank line separates the parts (2.4.0 and 2.7.1 alike).
    f"{_TRANSITIVE}requirements) failed resolution for "
    "{root}/two/a/requirements.txt: resolution impossible:\n"
    'no candidates at all for: flask "==99999.0.0"\n'
    "\n"
    "failed resolution for {root}/two/b/requirements.txt: resolution impossible:\n"
    'no candidates at all for: flask "==99999.0.0"\n',
    f"{_TRANSITIVE}requirements) failed resolution for "
    "{root}/conflict/requirements.txt: resolution impossible:\n"
    'requirements conflict: werkzeug: "==1.0.0,>=2.0"\n',
)

#: Errors for the excluded file alone, naming no other path: dropped.
_EXCLUDED_ONLY: tuple[str, ...] = (
    "Error during extraction: (extracting as javascript/packagelockjson) "
    "{root}/excluded/package-lock.json: could not extract: invalid character "
    "'o' in literal null (expecting 'u')",
    "Error during extraction: (extracting as javascript/packagelockjson) \n"
    "\t{root}/excluded/package-lock.json: could not extract: invalid character "
    "'.' looking for beginning of value",
)

#: Standard output that is exactly one report: complete at exit 0.
_REPORTS: tuple[str, ...] = (
    '{"results": []}',
    '  {"results": [], "experimental_config": {}}\n',
    '{"results": [], "experimental_config": {"licenses": {"summary": false}}, '
    '"experimental_generic_findings": [], "image_metadata": null, '
    '"license_summary": []}',
)

#: Standard output that is not exactly one report: incomplete at any exit.
_NOT_REPORTS: tuple[str, ...] = (
    'garbage {"results": []} trailing rpc error',
    '{"results": []} {"error":"failed"}',
    '{"error": "failed", "results": []}',
    '{"results": [], "experimental_config": {}, "errors": ["x"]}',
    '{"results": []}\nScanning dir .',
    'Scanning dir .\n{"results": []}',
    '{"results": []}{"results": []}',
    '{"results": {}}',
    '{"results": null}',
    "[]",
    "null",
    '{"results": [}',
)

#: The whole line osv-scanner 2.4.0 to 2.7.1 prints when it found nothing.
_NO_SOURCES = "No package sources found, --help for usage information."

#: Errors osv-scanner prints, or could print, none of them harmless.
_ERRORS: tuple[str, ...] = (
    # Real messages, 2.4.0 and 2.7.1.
    "Error during extraction: (extracting as javascript/packagelockjson) "
    "{root}/broken/package-lock.json: could not extract: invalid character "
    "'o' in literal null (expecting 'u')",
    "Error during extraction: (extracting as javascript/packagelockjson) \n"
    "\t{root}/broken/package-lock.json: could not extract: x\n"
    "\t{root}/private/requirements.txt: could not extract: y",
    f"{_TRANSITIVE}requirements) failed resolution for "
    "{root}/private/requirements.txt: max retries exhausted: failed after 4 "
    "attempts: rpc error: code = Unavailable desc = connection refused",
    f"{_TRANSITIVE}requirements) failed resolution for "
    "{root}/private/requirements.txt: rpc error: code = Unavailable desc = x",
    "Error during extraction: (extracting as vulnmatch/osvdev) max retries "
    'exceeded: attempt 4: request failed: Post "https://api.osv.dev/v1/querybatch"',
    "error when retrieving vulns: max retries exceeded: attempt 4\n"
    "max retries exceeded: attempt 4: request failed",
    "Failed to read config file: toml: line 1: expected '.' or '='\n"
    "toml: line 1: expected '.' or '='",
    "Ignored invalid config file at /{root}/osv-scanner.toml because: toml: x",
    "Failed to run code analysis (govulncheck) on 'go.mod' because exit status 1\n"
    "(the Go toolchain is required)",
    # Never seen; each must still leave the run incomplete.
    "context deadline exceeded",
    "x509: certificate signed by unknown authority",
    "Scanning dir .",
    "Filtered 2 vulnerabilities from output",
    f"{_TRANSITIVE}requirements) failed resolution for "
    "{root}/private/requirements.txt: package System(7):zz: not found (404)",
    f"{_TRANSITIVE}requirements) failed resolution for "
    "{root}/missing/requirements.txt: package System(7):zz: not found",
    f"{_TRANSITIVE}javaarchive) failed resolution for "
    "{root}/pom/pom.xml: package System(7):zz: not found",
    f"{_TRANSITIVE}requirements) client factories not configured",
    f"{_TRANSITIVE}requirements) ",
    "failed resolution for {root}/private/requirements.txt: "
    "rpc error: code = Unavailable desc = x",
    "\t{root}/private/requirements.txt: could not extract",
    'requirements conflict: flask: "==1" (after a timeout)',
    # A blank line ends a resolution-impossible block; after it only a
    # further "failed resolution for" continues the message.
    f"{_TRANSITIVE}requirements) failed resolution for "
    "{root}/two/a/requirements.txt: resolution impossible:\n"
    'no candidates at all for: flask "==99999.0.0"\n'
    "\n"
    "rpc error: code = Unavailable desc = x",
    f"{_TRANSITIVE}requirements) failed resolution for "
    "{root}/two/a/requirements.txt: resolution impossible:\n"
    'no candidates at all for: flask "==99999.0.0"\n'
    "\n"
    'no candidates at all for: flask "==1"',
    f"{_TRANSITIVE}requirements) failed resolution for "
    "{root}/two/a/requirements.txt: resolution impossible:\n"
    "\n"
    'no candidates at all for: flask "==99999.0.0"',
    f"{_TRANSITIVE}requirements) failed resolution for "
    "{root}/two/a/requirements.txt: resolution impossible:\n"
    'no candidates at all for: flask "==99999.0.0"\n'
    "\n"
    "\n"
    "failed resolution for {root}/two/b/requirements.txt: resolution impossible:\n"
    'no candidates at all for: flask "==99999.0.0"',
    # Causes carry a requirement specifier, nothing else.
    f"{_TRANSITIVE}requirements) failed resolution for "
    "{root}/conflict/requirements.txt: resolution impossible:\n"
    'requirements conflict: werkzeug: "rpc error: code = Unavailable"',
    f"{_TRANSITIVE}requirements) failed resolution for "
    "{root}/conflict/requirements.txt: resolution impossible:\n"
    'no candidates at all for: flask "==1 after a timeout"',
    f"{_TRANSITIVE}requirements) failed resolution for "
    "{root}/conflict/requirements.txt: resolution impossible:\n"
    'requirements conflict: werkzeug: ""',
    # An error for the excluded file that names another path is kept.
    "Error during extraction: (extracting as javascript/packagelockjson) "
    "{root}/excluded/package-lock.json: failed reading "
    "{root}/broken/package-lock.json: rpc error: unavailable",
    "Error during extraction: (extracting as javascript/packagelockjson) \n"
    "\t{root}/excluded/package-lock.json: failed reading "
    "{root}/broken/package-lock.json: rpc error: unavailable",
    "Error during extraction: (extracting as javascript/packagelockjson) "
    "{root}/excluded/package-lock.json: could not extract: see two",
    "Error during extraction: (extracting as javascript/packagelockjson) "
    "{root}/excluded/package-lock.json: open C:\\x: denied",
    # "No package sources found" is no files only as the exact whole line.
    f"{_NO_SOURCES} rpc error: unavailable",
    f"rpc error: unavailable {_NO_SOURCES}",
    "No package sources found",
    "No package sources found, --help for usage information",
)

#: Exit statuses tried; 1 comes with a finding in the report.
_EXITS: tuple[int, ...] = (0, 1, 2, 127, 128, 130)

_T = TypeVar("_T")


class _Draws:
    """A seeded, repeatable source of choices, so a failure can be replayed."""

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

    def choice(self, items: Sequence[_T]) -> _T:
        """Return the next draw from a sequence.

        Args:
            items: The candidates.

        Returns:
            One of them.
        """
        return items[self.below(len(items))]


@pytest.fixture
def scan_root(tmp_path: Path) -> Path:
    """Create every manifest the messages name.

    Args:
        tmp_path: Pytest fixture providing a temporary directory.

    Returns:
        The directory standing in for ``{root}``.
    """
    for name, content in _FILES.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    return tmp_path


def _finding_report(root: Path) -> str:
    """Return a report with one vulnerability, as an exit 1 carries.

    Args:
        root: The scanned directory.

    Returns:
        The JSON report.
    """
    package = {"name": "six", "version": "1.16.0", "ecosystem": "PyPI"}
    source = {"path": str(root / "private/requirements.txt"), "type": "lockfile"}
    vulnerable = {
        "package": package,
        "vulnerabilities": [{"id": "GHSA-0000-0000-0000"}],
        "groups": [{"ids": ["GHSA-0000-0000-0000"]}],
    }
    return json.dumps({"results": [{"source": source, "packages": [vulnerable]}]})


def _run(
    root: Path,
    messages: Sequence[str],
    returncode: int,
    stdout: str | None = None,
) -> ToolResult:
    """Run the plugin, excluding ``excluded/``, against one osv-scanner process.

    Args:
        root: The scanned directory.
        messages: What osv-scanner printed, with ``{root}``, in order.
        returncode: Its exit status.
        stdout: Its standard output; a report fitting the exit when omitted.

    Returns:
        The plugin's result.
    """
    stderr = "".join(f"{m}\n" for m in messages).replace(
        "{root}",
        str(root).lstrip("/"),
    )
    if stdout is None:
        stdout = _finding_report(root) if returncode == 1 else _EMPTY_REPORT
    proc = SubprocessResult(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        output=stdout + stderr,
    )
    plugin = OsvScannerPlugin()
    plugin.set_options(exclude_patterns=[_EXCLUDED_DIR])
    with patch.object(plugin, "_run_subprocess_result", return_value=proc):
        return plugin.check([str(root)], {"check_suppressions": False})


def _assert_incomplete(result: ToolResult, quoted: str, root: Path) -> None:
    """Assert a run is incomplete, unusable for a baseline, and quotes the error.

    Args:
        result: The plugin's result.
        quoted: An error message the output must quote verbatim.
        root: The scanned directory.
    """
    assert_that(result.execution_failed).is_true()
    assert_that(result.findings_complete).is_false()
    assert_that(result.inspected_files).is_none()
    assert_that(result.no_files).is_false()
    assert_that(
        baseline_is_eligible(
            action=Action.CHECK,
            dry_run_preview=False,
            tool_results=[result],
        ),
    ).is_false()
    for line in quoted.replace("{root}", str(root).lstrip("/")).splitlines():
        if line.strip():
            assert_that(result.output).contains(line)


@pytest.mark.parametrize("error", _ERRORS)
@pytest.mark.parametrize("returncode", _EXITS)
def test_any_error_makes_the_run_incomplete_at_every_exit(
    scan_root: Path,
    error: str,
    returncode: int,
) -> None:
    """One error, alone or between harmless notes, is never a verdict."""
    for messages in ([error], [_ALLOWED[0], error, _ALLOWED[2]]):
        result = _run(scan_root, messages, returncode)
        _assert_incomplete(result, error, scan_root)
        if returncode == 1:
            assert_that(result.issues_count).is_equal_to(1)


@pytest.mark.parametrize("returncode", [1, 127])
def test_allowed_notes_in_every_order_are_a_complete_scan(
    scan_root: Path,
    returncode: int,
) -> None:
    """Every order of the harmless notes, at exit 127 or 1, is complete."""
    for order in itertools.permutations(_ALLOWED):
        result = _run(scan_root, order, returncode)
        assert_that(result.execution_failed).is_false()
        assert_that(result.inspected_files).is_true()
        assert_that(result.issues_count).is_equal_to(1 if returncode == 1 else 0)


def test_one_error_in_any_position_among_the_notes_is_incomplete(
    scan_root: Path,
) -> None:
    """Moving an error through every position never hides it."""
    for error in _ERRORS:
        for position in range(len(_ALLOWED) + 1):
            messages = [*_ALLOWED[:position], error, *_ALLOWED[position:]]
            for returncode in (1, 127):
                _assert_incomplete(
                    _run(scan_root, messages, returncode),
                    error,
                    scan_root,
                )


def test_arbitrary_unseen_text_anywhere_makes_the_run_incomplete(
    scan_root: Path,
) -> None:
    """Random text, repeated notes and random order: incomplete if any text."""
    rng = _Draws(seed=3352)
    alphabet = "abcdefghijklmnopqrstuvwxyz :/.\"'=-_()[]{}0123456789\t"
    harmless = (*_ALLOWED, *_EXCLUDED_ONLY)
    for _ in range(300):
        messages = [rng.choice(harmless) for _ in range(rng.below(4))]
        unseen = "".join(rng.choice(alphabet) for _ in range(1 + rng.below(60)))
        text = rng.choice(["", unseen, f"x{unseen}"])
        if text.strip():
            messages.insert(rng.below(len(messages) + 1), text)
        returncode = rng.choice(_EXITS)
        result = _run(scan_root, messages, returncode)
        if text.strip():
            _assert_incomplete(result, text.strip(), scan_root)
        else:
            # Notes alone, or nothing at all: complete only at an exit
            # osv-scanner gives such a run.
            complete = returncode in (0, 1) or (returncode == 127 and messages)
            assert_that(result.execution_failed).is_equal_to(not complete)


@pytest.mark.parametrize("returncode", [2, 127, 128, 130])
def test_failed_exit_with_nothing_printed_is_incomplete(
    scan_root: Path,
    returncode: int,
) -> None:
    """An error exit with empty standard error is not a verdict either."""
    result = _run(scan_root, [], returncode)

    assert_that(result.execution_failed).is_true()
    assert_that(result.output).contains(f"exit code {returncode}")


def test_no_sources_line_beside_anything_else_is_incomplete(
    scan_root: Path,
) -> None:
    """The "No package sources found" line is no files only when printed alone."""
    alone = _run(scan_root, [_NO_SOURCES], 128)
    assert_that(alone.no_files).is_true()
    for other in (_ALLOWED[0], _ERRORS[0], "unexpected"):
        _assert_incomplete(_run(scan_root, [other, _NO_SOURCES], 128), other, scan_root)


@pytest.mark.parametrize("returncode", [1, 127])
def test_errors_for_the_excluded_file_alone_are_dropped_in_every_order(
    scan_root: Path,
    returncode: int,
) -> None:
    """Excluded-file errors naming no other path drop out among the notes."""
    for excluded in _EXCLUDED_ONLY:
        for position in range(len(_ALLOWED) + 1):
            messages = [*_ALLOWED[:position], excluded, *_ALLOWED[position:]]
            result = _run(scan_root, messages, returncode)
            assert_that(result.execution_failed).is_false()
            assert_that(result.inspected_files).is_true()
            assert_that(result.output or "").does_not_contain("excluded/")


@pytest.mark.parametrize("stdout", _REPORTS)
def test_exactly_one_report_on_stdout_is_a_complete_scan(
    scan_root: Path,
    stdout: str,
) -> None:
    """Each key osv-scanner's report can carry, and whitespace around it."""
    result = _run(scan_root, [], 0, stdout=stdout)

    assert_that(result.execution_failed).is_false()
    assert_that(result.inspected_files).is_true()


@pytest.mark.parametrize("stdout", _NOT_REPORTS)
@pytest.mark.parametrize("returncode", [0, 1, 127])
def test_stdout_that_is_not_exactly_one_report_is_incomplete(
    scan_root: Path,
    stdout: str,
    returncode: int,
) -> None:
    """Text around the object, a second object or an extra key: incomplete."""
    messages = [_ALLOWED[0]] if returncode == 127 else []
    result = _run(scan_root, messages, returncode, stdout=stdout)

    _assert_incomplete(result, "OSV-Scanner's report could not be read", scan_root)
