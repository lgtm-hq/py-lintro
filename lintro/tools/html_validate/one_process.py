"""Validate many glob-named files in one Node process (#3433).

The stdin route (:mod:`lintro.tools.html_validate.stdin_route`) starts one
html-validate process per glob-named file, and each start costs about 225 ms
of Node startup. When the resolved command is an html-validate binary whose
package lintro can locate and verify, a one-shot script loads that same
package's API and validates every file in one process instead.

The script does what the html-validate binary does for one file: it builds
``new CLI({})`` (the binary's options when it is given none), gets the JSON
formatter and the validator, runs ``validateFile`` on the real path, and
merges the report with ``Reporter.merge``. ``cli.isIgnored`` is asked first,
as the binary's own file expansion does. File names travel on the script's
stdin as JSON, never on a command line.

The package is used only when every check below holds; otherwise the stdin
pool runs as before (:func:`locate_package` returns None):

- the command is a single absolute path, never ``bunx`` or ``npx``;
- it resolves, through symlinks, to the ``html-validate`` ``bin`` entry of a
  package named ``html-validate``;
- that bin file is exactly html-validate's own entry: a ``#!/usr/bin/env
  node`` shebang and one ``import "../dist/esm/html-validate.js";``, so it
  runs the CLI from the same files the script loads, on the same ``node``;
- the package exports ``./dist/esm/index.js`` as its Node ESM entry;
- ``node`` is on ``PATH``.

The script reports the version it loaded before validating anything, and the
run is used only when that version is the package's own.

The selection of files does not change: a file reaches this route only after
the stdin allowlist accepted it. Any file the script reports no verdict for
(an exception, a crash, a malformed line) goes to the stdin pool, so a failure
there is reported exactly as before. A file that takes longer than the
per-call timeout stops the run: earlier files keep their results, that file is
named as timed out, and the rest are named as never started.
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess  # nosec B404 - argv list, shell disabled, names on stdin
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, cast

#: The html-validate binary's own entry, line by line (html-validate 11).
_BIN_LINES: tuple[str, ...] = (
    "#!/usr/bin/env node",
    'import "../dist/esm/html-validate.js";',
)

#: The Node ESM entry the script imports; it re-exports the CLI chunk the
#: binary's entry imports.
_API_ENTRY: str = "./dist/esm/index.js"

#: Reads ``{"entry": <path>, "files": [<path>, ...]}`` from stdin and writes
#: one JSON line per event: ``{"version"}`` once, then per file, in order,
#: ``{"index", "ignored": true}``, ``{"index", "valid", "report"}`` or
#: ``{"index", "error"}``.
SCRIPT: str = r"""
import fs from "node:fs";
import { pathToFileURL } from "node:url";
const request = JSON.parse(fs.readFileSync(0, "utf8"));
const write = (line) => fs.writeSync(1, JSON.stringify(line) + "\n");
if (!("resolve" in import.meta)) {
  write({ error: "import.meta.resolve is not available" });
  process.exit(3);
}
const { CLI, Reporter, version } = await import(pathToFileURL(request.entry).href);
const cli = new CLI({});
const formatter = await cli.getFormatter("json");
const htmlvalidate = await cli.getValidator();
write({ version });
for (const [index, filename] of request.files.entries()) {
  try {
    if (await cli.isIgnored(filename)) {
      write({ index, ignored: true });
      continue;
    }
    const merged = Reporter.merge([await htmlvalidate.validateFile(filename)]);
    write({ index, valid: merged.valid, report: formatter(merged) });
  } catch (err) {
    write({ index, error: String(err?.stack ?? err) });
  }
}
"""


@dataclass(frozen=True)
class HtmlValidatePackage:
    """An html-validate package the one-process script may load.

    Attributes:
        directory: The package's real directory.
        version: ``version`` from its ``package.json``.
        entry: Absolute path of its Node ESM entry.
    """

    directory: Path
    version: str
    entry: Path


@dataclass(frozen=True)
class OneProcessRuntime:
    """What the one-process script runs on.

    Attributes:
        node: The ``node`` executable the binary's shebang would find.
        package: The verified html-validate package.
    """

    node: str
    package: HtmlValidatePackage

    def argv(self) -> list[str]:
        """Build the argv that runs the script.

        Returns:
            ``node`` evaluating :data:`SCRIPT` as a module; no file name.
        """
        return [self.node, "--input-type=module", "--eval", SCRIPT]


def _manifest(directory: Path) -> dict[str, object] | None:
    """Read a package's ``package.json``.

    Args:
        directory: The package directory.

    Returns:
        The manifest object, or None when it cannot be read as one.
    """
    try:
        data = json.loads((directory / "package.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _node_import(manifest: dict[str, object]) -> object:
    """Find the Node ESM entry in a manifest's ``exports``.

    Args:
        manifest: A ``package.json`` object.

    Returns:
        ``exports["."]["node"]["import"]``, or None when absent.
    """
    exports = manifest.get("exports")
    root = exports.get(".") if isinstance(exports, dict) else None
    node = root.get("node") if isinstance(root, dict) else None
    return node.get("import") if isinstance(node, dict) else None


def _bin_entry(manifest: dict[str, object]) -> object:
    """Find the ``html-validate`` bin entry in a manifest.

    Args:
        manifest: A ``package.json`` object.

    Returns:
        The entry's relative path, or None when absent.
    """
    bins = manifest.get("bin")
    if isinstance(bins, str):
        return bins
    return bins.get("html-validate") if isinstance(bins, dict) else None


def _is_own_bin(path: Path) -> bool:
    """Report whether a file is html-validate's unmodified bin entry.

    Args:
        path: The resolved executable.

    Returns:
        True when its non-blank lines are exactly :data:`_BIN_LINES`.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    lines = tuple(line.strip() for line in text.splitlines() if line.strip())
    return lines == _BIN_LINES


def locate_package(command: Sequence[str]) -> HtmlValidatePackage | None:
    """Find the html-validate package a resolved command runs.

    Args:
        command: The resolved html-validate command, as the version gate
            checked it.

    Returns:
        The package, or None unless every check in the module docstring
        holds.
    """
    if len(command) != 1 or not os.path.isabs(command[0]):
        return None
    executable = Path(os.path.realpath(command[0]))
    if not executable.is_file() or not _is_own_bin(executable):
        return None
    directory = executable.parent.parent
    manifest = _manifest(directory)
    if manifest is None or manifest.get("name") != "html-validate":
        return None
    bin_entry = _bin_entry(manifest)
    if (
        not isinstance(bin_entry, str)
        or Path(
            os.path.realpath(directory / bin_entry),
        )
        != executable
    ):
        return None
    version = manifest.get("version")
    if _node_import(manifest) != _API_ENTRY or not isinstance(version, str):
        return None
    entry = directory / _API_ENTRY
    if not entry.is_file():
        return None
    return HtmlValidatePackage(directory=directory, version=version, entry=entry)


def one_process_runtime(command: Sequence[str]) -> OneProcessRuntime | None:
    """Decide whether glob-named files can be validated in one process.

    Args:
        command: The resolved html-validate command.

    Returns:
        The runtime, or None to keep the stdin pool.
    """
    package = locate_package(command)
    node = shutil.which("node")
    if package is None or node is None:
        return None
    return OneProcessRuntime(node=node, package=package)


@dataclass(frozen=True)
class FileVerdict:
    """The script's verdict on one file.

    Attributes:
        ignored: html-validate's ignore configuration excludes the file.
        valid: The report is valid, as the binary's exit status would say.
        report: The JSON formatter's output for the file.
    """

    ignored: bool = False
    valid: bool = True
    report: str = ""


@dataclass
class OneProcessRun:
    """What one script run settled.

    Attributes:
        version: The html-validate version the script loaded, or None.
        verdicts: Verdicts by file index, for every file it validated.
        errors: Why the script gave no verdict, by file index.
        reached: How many files the script reported on, in order.
        timed_out_at: The file the script was on when it hit the deadline.
        failure: Why the run stopped early, when it did.
    """

    version: str | None = None
    verdicts: dict[int, FileVerdict] = field(default_factory=dict)
    errors: dict[int, str] = field(default_factory=dict)
    reached: int = 0
    timed_out_at: int | None = None
    failure: str | None = None


def _verdict(line: dict[str, object]) -> FileVerdict | None:
    """Turn one file line into a verdict.

    Args:
        line: A decoded line that names an index.

    Returns:
        The verdict, or None for an error or a malformed line.
    """
    if line.get("ignored") is True:
        return FileVerdict(ignored=True)
    valid, report = line.get("valid"), line.get("report")
    if isinstance(valid, bool) and isinstance(report, str):
        return FileVerdict(valid=valid, report=report)
    return None


def _pump(stream: IO[bytes], lines: queue.Queue[str | None]) -> None:
    """Move a stream's lines into a queue, then None at its end.

    Args:
        stream: The child's stdout.
        lines: Where each line goes, decoded as UTF-8.
    """
    for line in stream:
        lines.put(line.decode("utf-8", errors="replace"))
    lines.put(None)


def _feed(stream: IO[bytes], payload: bytes) -> None:
    """Write the request to the child's stdin and close it.

    Args:
        stream: The child's stdin.
        payload: The JSON request.
    """
    try:
        stream.write(payload)
        stream.close()
    except (BrokenPipeError, OSError, ValueError):
        pass  # the child exited early; its exit is reported instead


@dataclass
class _Reader:
    """Read the script's lines against a per-file deadline.

    Attributes:
        run: What has been settled so far.
        total: How many files were sent.
        expected: The package version the script must report.
    """

    run: OneProcessRun
    total: int
    expected: str

    def accept(self, raw: str) -> bool:
        """Record one line.

        Args:
            raw: A line of the script's stdout.

        Returns:
            True to keep reading; False when the line ends the run.
        """
        try:
            line = json.loads(raw)
        except ValueError:
            self.run.failure = f"html-validate printed a malformed line: {raw[:200]!r}"
            return False
        if not isinstance(line, dict):
            self.run.failure = "html-validate printed something other than an object"
            return False
        if "index" not in line:
            return self._header(line)
        if self.run.version is None or line["index"] != self.run.reached:
            self.run.failure = f"html-validate reported out of order: {raw[:200]!r}"
            return False
        verdict = _verdict(line)
        if verdict is None:
            error = line.get("error")
            self.run.errors[self.run.reached] = (
                error if isinstance(error, str) else f"malformed line {raw[:200]!r}"
            )
        else:
            self.run.verdicts[self.run.reached] = verdict
        self.run.reached += 1
        return self.run.reached < self.total

    def _header(self, line: dict[str, object]) -> bool:
        """Check the version line.

        Args:
            line: A decoded line without an index.

        Returns:
            True when the loaded version is the package's own.
        """
        if self.run.version is not None or line.get("version") != self.expected:
            expected = self.expected
            self.run.failure = f"html-validate reported {line!r}, not {expected}"
            return False
        self.run.version = self.expected
        return True


def _stop(process: subprocess.Popen[bytes]) -> None:
    """Kill the child and reap it.

    Args:
        process: The script's process.
    """
    if process.poll() is None:
        process.kill()
    process.wait()


def run_one_process(
    argv: Sequence[str],
    *,
    entry: Path,
    expected_version: str,
    files: Sequence[str],
    cwd: str,
    timeout: float,
) -> OneProcessRun:
    """Validate files in one script run, each against its own deadline.

    The first file must finish within ``timeout`` of the start, as one stdin
    call must, and each later file within ``timeout`` of the one before.

    Args:
        argv: The command that runs :data:`SCRIPT`.
        entry: The package entry the script imports.
        expected_version: The version the script must report loading.
        files: Absolute paths, in input order.
        cwd: Directory to run from: the run's working directory, as for the
            positional call.
        timeout: The per-call deadline, in seconds.

    Returns:
        What the run settled. A file it reached without a verdict is in
        ``errors``; a file it never reached is in neither map. An
        ``OSError`` from starting the script propagates.
    """
    run = OneProcessRun()
    process = subprocess.Popen(  # nosec B603 - argv list, shell disabled
        list(argv),
        cwd=cwd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    # Both pipes exist: they were requested above.
    stdin, stdout = cast(IO[bytes], process.stdin), cast(IO[bytes], process.stdout)
    lines: queue.Queue[str | None] = queue.Queue()
    payload = json.dumps({"entry": str(entry), "files": list(files)}).encode()
    pump = threading.Thread(target=_pump, args=(stdout, lines), daemon=True)
    threading.Thread(target=_feed, args=(stdin, payload), daemon=True).start()
    pump.start()
    reader = _Reader(run=run, total=len(files), expected=expected_version)
    deadline = time.monotonic() + timeout
    try:
        while files:
            try:
                raw = lines.get(timeout=max(0.0, deadline - time.monotonic()))
            except queue.Empty:
                run.timed_out_at = run.reached
                break
            if raw is None:
                run.failure = "html-validate exited early"
                break
            reached = run.reached
            if not reader.accept(raw):
                break
            if run.reached > reached:
                deadline = time.monotonic() + timeout
    finally:
        _stop(process)
        pump.join(timeout=5)
        if not pump.is_alive():
            stdout.close()
    return run
