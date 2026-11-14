"""Glob-named files are validated through a bounded stdin pool (#3433).

The stdin calls of #3396 run at most ``min(cpu, 8)`` at a time. The first
call runs alone; the merged result is in input order whatever order the
calls finish in; after a timeout no new call starts while the calls already
running finish; and every directory's pin check runs before any call.
"""

from __future__ import annotations

import json
import os
import subprocess  # nosec B404 - TimeoutExpired for a simulated deadline
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import wait as real_wait
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.models.core.tool_outcome import Checked, TimedOut, ToolOutcome
from lintro.models.core.tool_result import ToolResult
from lintro.plugins.execution_preparation import PreparedExecution
from lintro.plugins.subprocess_executor import SubprocessResult
from lintro.tools.core.command_builders import pinned_npm_spec
from lintro.tools.core.node_fallback import npx_runner_options
from lintro.tools.html_validate.definition import HtmlValidatePlugin
from lintro.tools.html_validate.stdin_route import (
    MAX_STDIN_WORKERS,
    run_stdin_calls,
    stdin_workers,
)

_SPEC = pinned_npm_spec("html-validate")
_NPX = ["npx", "--yes", *npx_runner_options(_SPEC), "--package", _SPEC, "html-validate"]
_TIMES_OUT = "<!-- times out -->"
_AFTER_TIMEOUT = "<!-- finishes after the timeout -->"
_DIRTY = "<p><div></p>"


def _delay(ms: int) -> str:
    """Mark a source so the fake answers it after a delay.

    Args:
        ms: The delay in milliseconds.

    Returns:
        The marker.
    """
    return f"<!-- delay {ms} -->"


@dataclass
class _PooledHtmlValidate:
    """Answer stdin calls like html-validate 11.21, from several threads.

    A source holding ``<div>`` gets one finding; ``_delay`` holds the
    answer back; :data:`_TIMES_OUT` raises a timeout once
    :data:`_AFTER_TIMEOUT`'s call is running, and that call answers only
    after the timeout was raised.

    Attributes:
        log: ``(event, detail)`` pairs in the order they happened.
        lock: Guards ``log`` and the concurrency counters.
        running: Calls in flight now.
        peak: The most calls ever in flight at once.
    """

    log: list[tuple[str, str]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    running: int = 0
    peak: int = 0
    _waiting: threading.Event = field(default_factory=threading.Event)
    _raised: threading.Event = field(default_factory=threading.Event)

    def record(self, event: str, detail: str) -> None:
        """Append one event to the shared log.

        Args:
            event: What happened.
            detail: Which file or directory it happened to.
        """
        with self.lock:
            self.log.append((event, detail))

    def __call__(
        self,
        *,
        cmd: list[str],
        timeout: float | None = None,
        cwd: str | None = None,
        stdin: int | None = None,
    ) -> SubprocessResult:
        """Answer one stdin call.

        Args:
            cmd: The argv.
            timeout: The deadline (unused).
            cwd: The working directory.
            stdin: A readable file descriptor.

        Returns:
            html-validate's report for ``/dev/stdin``.

        Raises:
            subprocess.TimeoutExpired: For a source marked to time out.
        """
        del timeout
        assert stdin is not None  # nosec B101 - every call here is a stdin call
        source = os.read(stdin, 65536).decode()
        with self.lock:
            self.running += 1
            self.peak = max(self.peak, self.running)
        self.record("start", source)
        try:
            for token in source.split("<!-- delay ")[1:]:
                time.sleep(int(token.split(" ")[0]) / 1000)
            if _TIMES_OUT in source:
                self._waiting.wait(timeout=5)
                self._raised.set()
                raise subprocess.TimeoutExpired(cmd=cmd, timeout=30)
            if _AFTER_TIMEOUT in source:
                self._waiting.set()
                self._raised.wait(timeout=5)
                # Let the timed-out call's result reach the pool first.
                time.sleep(0.3)
            dirty = "<div>" in source
            messages = (
                [
                    {
                        "ruleId": "close-order",
                        "severity": 2,
                        "message": "m",
                        "line": 1,
                        "column": 5,
                    },
                ]
                if dirty
                else []
            )
            stdout = json.dumps([{"filePath": "/dev/stdin", "messages": messages}])
            self.record("finish", source)
            return SubprocessResult(
                returncode=1 if dirty else 0,
                stdout=stdout,
                stderr="",
                output=stdout,
            )
        finally:
            with self.lock:
                self.running -= 1


def _check(
    tmp_path: Path,
    files: dict[str, str],
    fake: Callable[..., SubprocessResult],
    *,
    workers: int | None,
    command: Sequence[str] = ("/pinned/html-validate",),
) -> ToolResult:
    """Write glob-named files and check them with a pool of ``workers``.

    Args:
        tmp_path: Working directory for the run.
        files: Relative path to content, in the order the run lists them.
        fake: The html-validate stand-in.
        workers: The pool size; None sizes it from the CPU count and config.
        command: The resolved html-validate command.

    Returns:
        The check result.
    """
    for name, content in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(content, encoding="utf-8")
    ctx = PreparedExecution(
        files=[str(tmp_path / name) for name in files],
        rel_files=list(files),
        cwd=str(tmp_path),
        timeout=30,
    )
    plugin = HtmlValidatePlugin()
    with ExitStack() as stack:
        stack.enter_context(patch.object(plugin, "prepare", return_value=ctx))
        stack.enter_context(
            patch.object(plugin, "_get_executable_command", return_value=list(command)),
        )
        stack.enter_context(
            patch.object(plugin, "_run_subprocess_result", side_effect=fake),
        )
        if workers is not None:
            stack.enter_context(
                patch(
                    "lintro.tools.html_validate.definition.stdin_workers",
                    return_value=workers,
                ),
            )
        return plugin.check([str(tmp_path)], {})


def _configured(max_workers: int) -> SimpleNamespace:
    """Stand in for a loaded config with an ``execution.max_workers``.

    Args:
        max_workers: The configured parallelism.

    Returns:
        An object shaped like ``LintroConfig`` for that one setting.
    """
    return SimpleNamespace(execution=SimpleNamespace(max_workers=max_workers))


def _verdict(item: str, *, timed_out: bool = False) -> ToolResult:
    """Build the result one stdin call gives for an item.

    Args:
        item: The item; it becomes the result's output.
        timed_out: Whether the call hit its deadline.

    Returns:
        A checked result, or a timeout.
    """
    execution = TimedOut(item) if timed_out else Checked(success=True)
    return ToolResult.from_outcome(
        ToolOutcome(execution=execution),
        name="html_validate",
        output=item,
    )


@pytest.mark.parametrize(
    ("cpus", "configured", "expected"),
    [
        (None, 32, 1),
        (1, 32, 1),
        (4, 32, 4),
        (8, 32, 8),
        (64, 32, MAX_STDIN_WORKERS),
        (64, 3, 3),
        (64, 1, 1),
    ],
)
def test_the_pool_is_min_of_cpus_eight_and_max_workers(
    cpus: int | None,
    configured: int,
    expected: int,
) -> None:
    """The pool follows the CPU count and ``execution.max_workers``, up to 8.

    Args:
        cpus: What ``os.cpu_count`` reports.
        configured: ``execution.max_workers``.
        expected: The pool size.
    """
    with (
        patch(
            "lintro.tools.html_validate.stdin_route.os.cpu_count",
            return_value=cpus,
        ),
        patch(
            "lintro.tools.html_validate.stdin_route.get_config",
            return_value=_configured(configured),
        ),
    ):
        assert_that(stdin_workers()).is_equal_to(expected)


@pytest.mark.parametrize(("max_workers", "serial"), [(1, True), (8, False)])
def test_max_workers_one_validates_one_file_at_a_time(
    tmp_path: Path,
    max_workers: int,
    serial: bool,
) -> None:
    """``execution.max_workers: 1`` keeps the stdin calls serial.

    Args:
        tmp_path: Working directory for the run.
        max_workers: ``execution.max_workers``.
        serial: Whether at most one call may run at a time.
    """
    fake = _PooledHtmlValidate()
    files = {f"[s{index}].html": _DIRTY + _delay(30) for index in range(6)}
    with (
        patch(
            "lintro.tools.html_validate.stdin_route.os.cpu_count",
            return_value=16,
        ),
        patch(
            "lintro.tools.html_validate.stdin_route.get_config",
            return_value=_configured(max_workers),
        ),
    ):
        result = _check(tmp_path, files, fake, workers=None)

    assert_that(fake.peak == 1).is_equal_to(serial)
    assert_that(result.issues_count).is_equal_to(6)
    assert_that(result.execution_failed).is_false()


def test_a_pool_below_one_worker_still_runs_every_call() -> None:
    """A zero pool is one worker, not a run whose files all go unstarted."""
    results, unstarted = run_stdin_calls(list("abc"), _verdict, workers=0)

    assert_that([result.output for result in results]).is_equal_to(list("abc"))
    assert_that(unstarted).is_empty()


def test_a_group_stays_serial_until_a_call_is_ready() -> None:
    """Each group warms up one call at a time; a ready group pools at once.

    Group ``a`` never yields a ready result, so its calls must never
    overlap; group ``b`` is ready after its first call.
    """
    lock = threading.Lock()
    running = {"a": 0, "b": 0}
    peak = {"a": 0, "b": 0}

    def call(item: str) -> ToolResult:
        with lock:
            running[item[0]] += 1
            peak[item[0]] = max(peak[item[0]], running[item[0]])
        time.sleep(0.02)
        with lock:
            running[item[0]] -= 1
        return _verdict(item)

    items = [f"{group}{index}" for index in range(5) for group in "ab"]
    results, unstarted = run_stdin_calls(
        items,
        call,
        workers=4,
        group=lambda item: item[0],
        ready=lambda result: str(result.output).startswith("b"),
    )

    assert_that(peak["a"]).is_equal_to(1)
    assert_that(peak["b"]).is_greater_than(1)
    assert_that([result.output for result in results]).is_equal_to(items)
    assert_that(unstarted).is_empty()


def test_a_timeout_the_main_thread_has_not_seen_still_stops_refill() -> None:
    """A worker's timeout stops new calls before the main thread reads it.

    The patched ``wait`` reports only the fast call done, after letting the
    slow call time out: the main thread acts on a stale view, as it does
    when it is descheduled between the timeout and its next refill.
    """
    release = threading.Event()
    started: list[str] = []

    def call(item: str) -> ToolResult:
        started.append(item)
        if item == "b":
            release.wait(timeout=5)
            return _verdict(item, timed_out=True)
        return _verdict(item)

    paused: list[bool] = []

    def stale_wait(
        running: dict[Future[ToolResult | None], int],
        return_when: str,
    ) -> tuple[set[Future[ToolResult | None]], set[Future[ToolResult | None]]]:
        done, pending = real_wait(running, return_when=return_when)
        if not paused and any(running[future] == 2 for future in done):
            paused.append(True)
            release.set()
            real_wait([future for future, index in running.items() if index == 1])
        return done, pending

    with patch("lintro.tools.html_validate.stdin_route.wait", side_effect=stale_wait):
        results, unstarted = run_stdin_calls(list("abxc"), call, workers=2)

    assert_that(paused).is_equal_to([True])
    assert_that(sorted(started)).is_equal_to(["a", "b", "x"])
    assert_that([result.output for result in results]).is_equal_to(["a", "b", "x"])
    assert_that(unstarted).is_equal_to(["c"])


def test_a_worker_picking_up_a_call_after_a_timeout_launches_nothing() -> None:
    """A call submitted just before a timeout does not run after it."""
    release = threading.Event()
    started: list[str] = []
    futures: dict[int, Future[object]] = {}

    def call(item: str) -> ToolResult:
        started.append(item)
        if item == "b":
            release.wait(timeout=5)
            return _verdict(item, timed_out=True)
        return _verdict(item)

    class _TimeoutBeforePickup(ThreadPoolExecutor):
        """Let ``b`` time out between submitting ``c`` and a worker taking it."""

        def submit(  # type: ignore[override]
            self,
            fn: Callable[..., object],
            /,
            *args: object,
            **kwargs: object,
        ) -> Future[object]:
            index = args[0]
            if index == 3:
                release.set()
                real_wait([futures[1]])
            future = super().submit(fn, *args, **kwargs)
            futures[int(str(index))] = future
            return future

    with patch(
        "lintro.tools.html_validate.stdin_route.ThreadPoolExecutor",
        _TimeoutBeforePickup,
    ):
        results, unstarted = run_stdin_calls(list("abxc"), call, workers=2)

    assert_that(sorted(started)).is_equal_to(["a", "b", "x"])
    assert_that([result.timed_out for result in results]).is_equal_to(
        [False, True, False],
    )
    assert_that(unstarted).is_equal_to(["c"])


@dataclass
class _ColdNpx:
    """Answer stdin calls like ``npx`` whose package is not yet installed.

    The first call to run installs the package. Any call that starts while
    an install is under way fails the way a racing npx does: no report and
    a module-loader error on stderr.

    Attributes:
        lock: Guards the counters.
        installed: Whether an install has finished.
        installing: Calls installing right now.
        collisions: Calls that started during someone else's install.
        running: Calls in flight now.
        peak: The most calls ever in flight at once.
    """

    lock: threading.Lock = field(default_factory=threading.Lock)
    installed: bool = False
    installing: int = 0
    collisions: int = 0
    running: int = 0
    peak: int = 0

    def __call__(
        self,
        *,
        cmd: list[str],
        timeout: float | None = None,
        cwd: str | None = None,
        stdin: int | None = None,
    ) -> SubprocessResult:
        """Answer one stdin call.

        Args:
            cmd: The argv.
            timeout: The deadline (unused).
            cwd: The working directory (unused).
            stdin: A readable file descriptor.

        Returns:
            A report with one finding per ``<div>``, or a racing failure.
        """
        del cmd, timeout, cwd
        assert stdin is not None  # nosec B101 - every call here is a stdin call
        source = os.read(stdin, 65536).decode()
        with self.lock:
            self.running += 1
            self.peak = max(self.peak, self.running)
            cold = not self.installed
            collided = cold and self.installing > 0
            self.installing += 1 if cold else 0
            self.collisions += 1 if collided else 0
        try:
            time.sleep(0.05 if cold else 0.01)
            if collided:
                error = "node:internal/modules/cjs/loader:1607\n  throw err;\n"
                return SubprocessResult(
                    returncode=1,
                    stdout="",
                    stderr=error,
                    output=error,
                )
            messages = [
                {
                    "ruleId": "close-order",
                    "severity": 2,
                    "message": "m",
                    "line": 1,
                    "column": 5,
                }
                for _ in range(source.count("<div>"))
            ]
            stdout = json.dumps([{"filePath": "/dev/stdin", "messages": messages}])
            return SubprocessResult(
                returncode=1 if messages else 0,
                stdout=stdout,
                stderr="",
                output=stdout,
            )
        finally:
            with self.lock:
                self.running -= 1
                if cold:
                    self.installing -= 1
                    self.installed = self.installed or not collided


def test_npx_warms_up_one_call_at_a_time_when_the_first_file_vanishes(
    tmp_path: Path,
) -> None:
    """A first file that never reaches npx does not open the pool cold.

    The first file disappears after preflight, so its call never launches
    npx. The next calls must still run one at a time until one has really
    installed and run html-validate; then the rest may share the pool.

    Args:
        tmp_path: Working directory for the run.
    """
    fake = _ColdNpx()
    files = {}
    for index in range(17):
        folder = tmp_path / f"d{index:02d}"
        folder.mkdir()
        pin = "11.21.0" if index % 2 else "11.20.0"
        (folder / "package.json").write_text(
            json.dumps({"devDependencies": {"html-validate": pin}}),
            encoding="utf-8",
        )
        files[f"d{index:02d}/[slug].html"] = "<p><div></p><p><div></p><p><div></p>"
    first = tmp_path / "d00" / "[slug].html"
    original = HtmlValidatePlugin._check_stdin

    def vanishing(
        self: HtmlValidatePlugin,
        target: str,
        **kwargs: object,
    ) -> ToolResult:
        if target == str(first):
            first.unlink()
        return original(self, target, **kwargs)  # type: ignore[arg-type]

    with patch.object(HtmlValidatePlugin, "_check_stdin", vanishing):
        result = _check(tmp_path, files, fake, workers=8, command=_NPX)

    assert_that(fake.collisions).is_zero()
    assert_that(fake.peak).is_greater_than(1)
    assert_that(result.issues_count).is_equal_to(48)
    assert_that(result.execution_failed).is_true()
    assert_that(result.output).contains(f"{first}: the file could not be opened")
    assert_that(result.output).does_not_contain("printed no report")


def test_results_come_back_in_input_order_and_the_pool_is_bounded() -> None:
    """Calls finishing in reverse order still merge in input order."""
    items = [f"item-{index:02d}" for index in range(12)]
    fake = _PooledHtmlValidate()

    def call(item: str) -> ToolResult:
        fake.record("start", item)
        with fake.lock:
            fake.running += 1
            fake.peak = max(fake.peak, fake.running)
        time.sleep((len(items) - int(item[-2:])) * 0.01)
        with fake.lock:
            fake.running -= 1
        fake.record("finish", item)
        return _verdict(item)

    results, unstarted = run_stdin_calls(items, call, workers=4)

    finished = [detail for event, detail in fake.log if event == "finish"]
    assert_that(finished).is_not_equal_to(items)
    assert_that([result.output for result in results]).is_equal_to(items)
    assert_that(unstarted).is_empty()
    assert_that(fake.peak).is_greater_than(1).is_less_than_or_equal_to(4)


def test_a_first_call_timeout_starts_nothing_else() -> None:
    """The first call runs alone, so a hung runner costs one deadline."""
    started: list[str] = []

    def call(item: str) -> ToolResult:
        started.append(item)
        return _verdict(item, timed_out=True)

    results, unstarted = run_stdin_calls(["a", "b", "c"], call, workers=8)

    assert_that(started).is_equal_to(["a"])
    assert_that([result.timed_out for result in results]).is_equal_to([True])
    assert_that(unstarted).is_equal_to(["b", "c"])


def test_a_pool_timeout_stops_new_calls_and_keeps_those_in_flight() -> None:
    """After a timeout the running call finishes; the rest never start."""
    in_flight = threading.Event()
    raised = threading.Event()
    started: list[str] = []

    def call(item: str) -> ToolResult:
        started.append(item)
        if item == "b":
            in_flight.wait(timeout=5)
            raised.set()
            return _verdict(item, timed_out=True)
        if item == "c":
            in_flight.set()
            raised.wait(timeout=5)
            time.sleep(0.3)
        return _verdict(item)

    results, unstarted = run_stdin_calls(list("abcde"), call, workers=2)

    assert_that(sorted(started)).is_equal_to(["a", "b", "c"])
    assert_that([result.output for result in results]).is_equal_to(["a", "b", "c"])
    assert_that([result.timed_out for result in results]).is_equal_to(
        [False, True, False],
    )
    assert_that(unstarted).is_equal_to(["d", "e"])


def test_the_pooled_result_is_the_serial_result(tmp_path: Path) -> None:
    """Eight workers give the same findings, order and output as one.

    Args:
        tmp_path: Working directory for the runs.
    """
    files = {
        f"[s{index}].html": (_DIRTY if index % 3 else "<p>a</p>")
        + _delay(10 * (8 - index))
        for index in range(8)
    }
    runs = {}
    for workers in (1, 8):
        fake = _PooledHtmlValidate()
        runs[workers] = (
            _check(tmp_path / f"w{workers}", files, fake, workers=workers),
            fake,
        )
    serial, pooled = runs[1][0], runs[8][0]

    pooled_finish = [detail for event, detail in runs[8][1].log if event == "finish"]
    assert_that(pooled_finish).is_not_equal_to(list(files.values()))
    assert_that(runs[8][1].peak).is_greater_than(1)
    assert_that([Path(issue.file).name for issue in pooled.issues]).is_equal_to(
        [name for name, content in files.items() if "<div>" in content],
    )
    assert_that(
        [Path(issue.file).name for issue in pooled.issues],
    ).is_equal_to([Path(issue.file).name for issue in serial.issues])
    assert_that(
        (pooled.success, pooled.issues_count, pooled.execution_failed),
    ).is_equal_to((serial.success, serial.issues_count, serial.execution_failed))
    assert_that(pooled.output).is_equal_to(
        (serial.output or "").replace(str(tmp_path / "w1"), str(tmp_path / "w8")),
    )


def test_a_timeout_in_the_pool_names_every_file_never_started(tmp_path: Path) -> None:
    """A call in flight keeps its findings; later files are not inspected.

    Args:
        tmp_path: Working directory for the run.
    """
    fake = _PooledHtmlValidate()
    result = _check(
        tmp_path,
        {
            "[a].html": "<p>a</p>",
            "[b].html": f"<p>b</p>{_TIMES_OUT}",
            "[c].html": f"{_DIRTY}{_AFTER_TIMEOUT}",
            "[d].html": _DIRTY,
            "[e].html": _DIRTY,
        },
        fake,
        workers=2,
    )

    started = sorted(detail for event, detail in fake.log if event == "start")
    assert_that(started).is_equal_to(
        sorted(["<p>a</p>", f"<p>b</p>{_TIMES_OUT}", f"{_DIRTY}{_AFTER_TIMEOUT}"]),
    )
    assert_that(result.execution_failed).is_true()
    assert_that(result.timed_out).is_false()
    assert_that(result.success).is_false()
    assert_that([str(issue.file) for issue in result.issues]).is_equal_to(
        [str(tmp_path / "[c].html")],
    )
    assert_that(result.output).contains(
        f"{tmp_path / '[b].html'}: ",
        "timed out",
        "not run on 2 file(s) because an earlier html-validate call timed out: "
        "'[d].html', '[e].html'.",
    )


def test_every_directory_pin_check_runs_before_any_stdin_call(
    tmp_path: Path,
) -> None:
    """No pooled call can start from a directory whose npx pin is unchecked.

    Args:
        tmp_path: Working directory for the run.
    """
    fake = _PooledHtmlValidate()

    def pin_check(*, cwd: Path, package: str, pin: str, command: object) -> None:
        del package, pin, command
        fake.record("pin", str(cwd))

    files = {f"[r{index}]/page.html": _DIRTY + _delay(20) for index in range(6)}
    with patch(
        "lintro.tools.html_validate.stdin_route.npx_pin_refusal",
        side_effect=pin_check,
    ):
        result = _check(tmp_path, files, fake, workers=8, command=_NPX)

    events = [event for event, _ in fake.log]
    pinned = {detail for event, detail in fake.log if event == "pin"}
    assert_that(pinned).is_equal_to({str(tmp_path / f"[r{i}]") for i in range(6)})
    assert_that(events.index("start")).is_greater_than(
        max(index for index, event in enumerate(events) if event == "pin"),
    )
    assert_that(result.issues_count).is_equal_to(6)
    assert_that(result.execution_failed).is_false()
