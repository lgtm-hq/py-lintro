"""Files are recorded as checked only after the tool has run (#3578).

Every test drives a real plugin through the real executor (sequential and
parallel) twice, and asserts which files the second run was handed.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from assertpy import assert_that

import lintro.utils.tool_executor as te
from lintro.config.lintro_config import LintroConfig
from lintro.enums.action import Action
from lintro.enums.capability import Cap
from lintro.enums.execution_class import ExecutionClass
from lintro.enums.tool_type import ToolType
from lintro.models.core.claim import Claim
from lintro.models.core.run_artifact import RunArtifact
from lintro.models.core.tool_outcome import (
    Checked,
    TimedOut,
    ToolOutcome,
    Unavailable,
)
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.base_issue import BaseIssue
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.execution_preparation import PreparedExecution
from lintro.plugins.protocol import ToolDefinition
from lintro.tools import tool_manager
from lintro.utils.execution import parallel_executor
from lintro.utils.execution.run_context import RunContext
from lintro.utils.execution.tool_configuration import ToolsToRunResult
from lintro.utils.file_cache import (
    FileFingerprint,
    FingerprintSnapshot,
    snapshot_fingerprints,
)
from lintro.utils.incremental_record import (
    PendingCheck,
    _can_open,
    _merged,
    checked_files,
    finish_incremental_run,
    incremental_note,
)
from tests.unit.conftest import FakeLogger

#: What the fake tool does once it has been handed its files.
Behaviour = Callable[[PreparedExecution], ToolResult]


def _clean(_ctx: PreparedExecution) -> ToolResult:
    return ToolResult.from_outcome(
        ToolOutcome(execution=Checked(success=True, inspected_files=True)),
        name="fake",
    )


def _finding_in_bad(*, complete: bool) -> Behaviour:
    def behave(_ctx: PreparedExecution) -> ToolResult:
        return ToolResult.from_outcome(
            ToolOutcome(
                execution=Checked(success=False, inspected_files=True),
                issues_count=1,
            ),
            name="fake",
            issues=[BaseIssue(file="bad.txt", line=1, message="finding")],
            findings_complete=complete,
        )

    return behave


def _failed(_ctx: PreparedExecution) -> ToolResult:
    return ToolResult(name="fake", success=False, execution_failed=True)


def _timed_out(_ctx: PreparedExecution) -> ToolResult:
    return ToolResult.from_outcome(
        ToolOutcome(execution=TimedOut("timed out after 1s")),
        name="fake",
    )


def _raises(error: BaseException) -> Behaviour:
    def behave(_ctx: PreparedExecution) -> ToolResult:
        raise error

    return behave


@dataclass
class _FakeTool(BaseToolPlugin):
    """A plugin using lintro's real preparation, with a scripted verdict."""

    behaviour: Behaviour = _clean
    handed: list[list[str]] | None = None
    cacheable: bool = True
    #: Which prepared files the plugin states it handed to the tool; None
    #: states nothing (a project-level tool, or a plugin not yet audited).
    passes: Callable[[list[str]], list[str]] | None = list

    @property
    def definition(self) -> ToolDefinition:
        """Return the fake tool's definition.

        Returns:
            A ``*.txt`` checker without a version command.
        """
        return ToolDefinition(
            name="fake",
            description="fake checker",
            can_fix=True,
            file_patterns=["*.txt"],
            claims=[Claim(patterns=["*.txt"], capabilities={Cap.CHECK})],
            partitionable=True,
            incremental_cacheable=self.cacheable,
        )

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Prepare through lintro, note the handed files, then behave.

        Args:
            paths: Paths to check.
            options: Runtime options.

        Returns:
            The scripted result, or preparation's early result.
        """
        prepared = self.prepare(paths, options)
        if isinstance(prepared, ToolResult):
            return prepared
        assert self.handed is not None
        self.handed.append(sorted(os.path.basename(f) for f in prepared.files))
        if self.passes is not None:
            self._record_checked(self.passes(prepared.files))
        return self.behaviour(prepared)

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Format the same way the check runs, reporting nothing left.

        Args:
            paths: Paths to format.
            options: Runtime options.

        Returns:
            A clean fix result, or preparation's early result.
        """
        checked = self.check(paths, options)
        if checked.no_files or checked.unavailable:
            return checked
        return ToolResult(
            name="fake",
            success=True,
            inspected_files=True,
            initial_issues_count=0,
            fixed_issues_count=0,
            remaining_issues_count=0,
        )


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a project with one clean and one flagged file, and a cache.

    The version gate passes unless a test replaces it: the fake tool is not
    in the registry the gate looks it up in.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Points the incremental cache at ``tmp_path``.

    Returns:
        The project directory.
    """
    monkeypatch.setattr("lintro.utils.file_cache.CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(
        "lintro.plugins.execution_preparation.verify_tool_version",
        lambda *_args, **_kwargs: None,
    )
    root = tmp_path / "project"
    root.mkdir()
    (root / "good.txt").write_text("good\n")
    (root / "bad.txt").write_text("bad\n")
    monkeypatch.chdir(root)
    return root


def _run(
    monkeypatch: pytest.MonkeyPatch,
    project: Path,
    *,
    parallel: bool,
    action: Action = Action.CHECK,
    incremental: bool = True,
    **fake: Any,
) -> RunArtifact:
    """Run the fake tool once through ``execute_run``.

    Args:
        monkeypatch: Isolates the executor's tool boundaries.
        project: Project directory to check.
        parallel: Whether to use the parallel dispatcher.
        action: ``CHECK`` or ``FIX``.
        incremental: False for a full (non-incremental) check.
        **fake: ``_FakeTool`` fields: ``behaviour`` (what the tool does once
            handed its files), ``handed`` (collects the files each run was
            handed), ``cacheable`` and ``passes``.

    Returns:
        The run artifact.
    """
    tool = _FakeTool(**fake)

    def configure(*, tool: Any, **_kwargs: Any) -> Any:
        clone = tool.copy_for_execution()
        clone.set_options(incremental=incremental)
        return clone

    monkeypatch.setattr(tool_manager, "get_tool", lambda _name: tool)
    monkeypatch.setattr(
        tool_manager,
        "get_parallel_batches",
        lambda names, **_kwargs: [list(names)],
    )
    monkeypatch.setattr(te, "configure_tool_for_execution", configure)
    monkeypatch.setattr(parallel_executor, "configure_tool_for_execution", configure)
    monkeypatch.setattr(
        te,
        "get_tools_to_run",
        lambda *_args, **_kwargs: ToolsToRunResult(to_run=["fake"]),
    )
    config = LintroConfig()
    config.execution.parallel = parallel
    config.execution.max_workers = 2
    config.execution.auto_install_deps = False
    ctx = RunContext(
        action=action,
        selection_action=action,
        dry_run_preview=False,
        output_manager=SimpleNamespace(run_dir=project, base_dir=project),
        logger=FakeLogger(),
        lintro_config=config,
        clean_stdout_output=True,
    )
    return te.execute_run(
        ctx=ctx,
        paths=[str(project)],
        tools="fake",
        tool_options=None,
        exclude=None,
        include_venv=False,
        group_by="auto",
        output_format="json",
        verbose=False,
        run_gates=False,
        incremental=incremental,
    )


PARALLEL = pytest.mark.parametrize(
    "parallel",
    [False, True],
    ids=["sequential", "parallel"],
)
BOTH = ["bad.txt", "good.txt"]


@PARALLEL
@pytest.mark.parametrize(
    "refusal",
    [
        Unavailable("fake is not installed"),
        Unavailable("fake did not run: below minimum", strict=True),
    ],
    ids=["unavailable", "refused-version"],
)
def test_files_are_checked_once_the_tool_runs_after_an_early_result(
    monkeypatch: pytest.MonkeyPatch,
    project: Path,
    parallel: bool,
    refusal: Unavailable,
) -> None:
    """A tool the version gate stopped records nothing.

    Args:
        monkeypatch: Isolates the executor.
        project: Project directory.
        parallel: Whether to use the parallel dispatcher.
        refusal: What the version gate reports.
    """
    handed: list[list[str]] = []
    early = ToolResult.from_outcome(ToolOutcome(refusal), name="fake")
    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=early,
    ):
        first = _run(
            monkeypatch,
            project,
            behaviour=_clean,
            parallel=parallel,
            handed=handed,
        )

    _run(monkeypatch, project, behaviour=_clean, parallel=parallel, handed=handed)

    assert_that(first.tool_results[0].unavailable_reason).is_equal_to(refusal.reason)
    assert_that(handed).is_equal_to([BOTH])


@PARALLEL
@pytest.mark.parametrize(
    "behaviour",
    [_failed, _timed_out, _finding_in_bad(complete=False)],
    ids=["failed", "timed-out", "finding-unproven-report"],
)
def test_an_unproven_run_records_nothing(
    monkeypatch: pytest.MonkeyPatch,
    project: Path,
    parallel: bool,
    behaviour: Behaviour,
) -> None:
    """A failed, timed-out or unproven run leaves every file to the next run.

    Args:
        monkeypatch: Isolates the executor.
        project: Project directory.
        parallel: Whether to use the parallel dispatcher.
        behaviour: How the first run ends.
    """
    handed: list[list[str]] = []
    _run(monkeypatch, project, behaviour=behaviour, parallel=parallel, handed=handed)

    _run(monkeypatch, project, behaviour=_clean, parallel=parallel, handed=handed)

    assert_that(handed).is_equal_to([BOTH, BOTH])


@PARALLEL
def test_an_exception_mid_run_records_nothing(
    monkeypatch: pytest.MonkeyPatch,
    project: Path,
    parallel: bool,
) -> None:
    """A tool that raises is reported as failed and records nothing.

    Args:
        monkeypatch: Isolates the executor.
        project: Project directory.
        parallel: Whether to use the parallel dispatcher.
    """
    handed: list[list[str]] = []
    first = _run(
        monkeypatch,
        project,
        behaviour=_raises(RuntimeError("boom")),
        parallel=parallel,
        handed=handed,
    )

    _run(monkeypatch, project, behaviour=_clean, parallel=parallel, handed=handed)

    assert_that(first.tool_results[0].success).is_false()
    assert_that(handed).is_equal_to([BOTH, BOTH])


def test_an_interrupted_run_records_nothing(
    monkeypatch: pytest.MonkeyPatch,
    project: Path,
) -> None:
    """Ctrl-C while the tool runs leaves every file to the next run.

    Args:
        monkeypatch: Isolates the executor.
        project: Project directory.
    """
    handed: list[list[str]] = []
    with pytest.raises(KeyboardInterrupt):
        _run(
            monkeypatch,
            project,
            behaviour=_raises(KeyboardInterrupt()),
            parallel=False,
            handed=handed,
        )

    _run(monkeypatch, project, behaviour=_clean, parallel=False, handed=handed)

    assert_that(handed).is_equal_to([BOTH, BOTH])


@PARALLEL
def test_a_clean_run_is_not_repeated_while_files_are_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    project: Path,
    parallel: bool,
) -> None:
    """Clean files are recorded; an edited one is checked again.

    Args:
        monkeypatch: Isolates the executor.
        project: Project directory.
        parallel: Whether to use the parallel dispatcher.
    """
    handed: list[list[str]] = []
    _run(monkeypatch, project, behaviour=_clean, parallel=parallel, handed=handed)
    second = _run(
        monkeypatch,
        project,
        behaviour=_clean,
        parallel=parallel,
        handed=handed,
    )
    (project / "good.txt").write_text("good, and longer\n")
    _run(monkeypatch, project, behaviour=_clean, parallel=parallel, handed=handed)

    assert_that(second.tool_results[0].no_files).is_true()
    assert_that(second.exit_code).is_equal_to(0)
    assert_that(handed).is_equal_to([BOTH, ["good.txt"]])


@PARALLEL
def test_an_unchanged_file_with_a_finding_is_reported_again(
    monkeypatch: pytest.MonkeyPatch,
    project: Path,
    parallel: bool,
) -> None:
    """A finding keeps its file out of the record; clean files are recorded.

    Args:
        monkeypatch: Isolates the executor.
        project: Project directory.
        parallel: Whether to use the parallel dispatcher.
    """
    handed: list[list[str]] = []
    finding = _finding_in_bad(complete=True)
    _run(monkeypatch, project, behaviour=finding, parallel=parallel, handed=handed)

    again = _run(
        monkeypatch,
        project,
        behaviour=finding,
        parallel=parallel,
        handed=handed,
    )

    assert_that(handed).is_equal_to([BOTH, ["bad.txt"]])
    assert_that(again.tool_results[0].issues_count).is_equal_to(1)
    assert_that(again.exit_code).is_equal_to(1)


@PARALLEL
def test_a_tool_that_is_not_incremental_cacheable_checks_everything(
    monkeypatch: pytest.MonkeyPatch,
    project: Path,
    parallel: bool,
) -> None:
    """A never-cache tool is handed every file and says why.

    Args:
        monkeypatch: Isolates the executor.
        project: Project directory.
        parallel: Whether to use the parallel dispatcher.
    """
    handed: list[list[str]] = []
    runs = [
        _run(
            monkeypatch,
            project,
            behaviour=_clean,
            parallel=parallel,
            handed=handed,
            cacheable=False,
        )
        for _ in range(2)
    ]

    assert_that(handed).is_equal_to([BOTH, BOTH])
    assert_that(runs[1].tool_results[0].output).is_equal_to(incremental_note("fake"))


@PARALLEL
def test_a_cache_from_an_earlier_lintro_is_discarded(
    monkeypatch: pytest.MonkeyPatch,
    project: Path,
    parallel: bool,
) -> None:
    """An unversioned cache listing current fingerprints is not trusted.

    Args:
        monkeypatch: Isolates the executor.
        project: Project directory.
        parallel: Whether to use the parallel dispatcher.
    """
    cache_dir = project.parent / "cache"
    cache_dir.mkdir()
    fingerprints = {}
    for name in BOTH:
        path = str(project / name)
        stat = os.stat(path)
        fingerprints[path] = {
            "path": path,
            "mtime": stat.st_mtime,
            "size": stat.st_size,
        }
    (cache_dir / "fake.json").write_text(
        json.dumps({"tool_name": "fake", "fingerprints": fingerprints}),
    )
    handed: list[list[str]] = []

    _run(monkeypatch, project, behaviour=_clean, parallel=parallel, handed=handed)

    assert_that(handed).is_equal_to([BOTH])


@pytest.mark.parametrize(
    "name",
    ["cargo_audit", "cargo_deny", "commitlint", "osv_scanner", "pip_audit", "semgrep"],
)
def test_scanners_on_outside_data_say_incremental_does_not_apply(name: str) -> None:
    """Each declared never-cache tool carries the note under ``--incremental``.

    Args:
        name: Registered tool name.
    """
    tool = tool_manager.get_tool(name).copy_for_execution()
    tool.set_options(incremental=True)

    result = finish_incremental_run(
        tool,
        ToolResult(name=name, success=True),
        record=True,
    )

    assert_that(result.output).is_equal_to(incremental_note(name))


def test_only_tools_on_outside_data_are_declared_never_cache() -> None:
    """The never-cache list is exactly the scanners whose verdict moves alone."""
    never = sorted(
        name
        for name in tool_manager.get_all_tools()
        if not tool_manager.get_tool(name).definition.incremental_cacheable
    )

    assert_that(never).is_equal_to(
        [
            "cargo_audit",
            "cargo_deny",
            "commitlint",
            "osv_scanner",
            "pip_audit",
            "semgrep",
        ],
    )


@PARALLEL
def test_a_format_run_records_nothing_for_a_later_check(
    monkeypatch: pytest.MonkeyPatch,
    project: Path,
    parallel: bool,
) -> None:
    """A clean ``fmt`` run leaves every file to the next incremental check.

    Args:
        monkeypatch: Isolates the executor.
        project: Project directory.
        parallel: Whether to use the parallel dispatcher.
    """
    handed: list[list[str]] = []
    _run(
        monkeypatch,
        project,
        behaviour=_clean,
        parallel=parallel,
        handed=handed,
        action=Action.FIX,
    )

    _run(monkeypatch, project, behaviour=_clean, parallel=parallel, handed=handed)

    assert_that(handed).is_equal_to([BOTH, BOTH])


@PARALLEL
@pytest.mark.parametrize(
    ("passes", "rechecked"),
    [
        (lambda files: [f for f in files if f.endswith("good.txt")], ["bad.txt"]),
        (None, BOTH),
    ],
    ids=["narrowed-to-one-file", "states-nothing"],
)
def test_only_files_passed_to_the_tool_are_recorded(
    monkeypatch: pytest.MonkeyPatch,
    project: Path,
    parallel: bool,
    passes: Callable[[list[str]], list[str]] | None,
    rechecked: list[str],
) -> None:
    """A plugin that narrows after ``prepare`` records only what it passed.

    A plugin that states nothing, however clean its result, records nothing.

    Args:
        monkeypatch: Isolates the executor.
        project: Project directory.
        parallel: Whether to use the parallel dispatcher.
        passes: Which prepared files the plugin passes to its command.
        rechecked: Files the next run must be handed.
    """
    handed: list[list[str]] = []
    _run(
        monkeypatch,
        project,
        behaviour=_clean,
        parallel=parallel,
        handed=handed,
        passes=passes,
    )

    _run(monkeypatch, project, behaviour=_clean, parallel=parallel, handed=handed)

    assert_that(handed).is_equal_to([BOTH, rechecked])


def test_a_symlink_and_its_target_are_recorded_separately(tmp_path: Path) -> None:
    """Only the path that was handed is recorded, never its real file.

    Args:
        tmp_path: Directory holding the target and a link to it.
    """
    target = tmp_path / "target.txt"
    target.write_text("x\n")
    link = tmp_path / "link.txt"
    link.symlink_to(target)
    snapshot = snapshot_fingerprints([str(link), str(target)])
    pending = PendingCheck("fake", str(tmp_path), snapshot)
    clean = ToolResult.from_outcome(
        ToolOutcome(execution=Checked(success=True, inspected_files=True)),
        name="fake",
    )

    recorded = checked_files(pending, clean, {str(link)})

    assert_that(sorted(recorded)).is_equal_to([str(link)])


def test_a_file_one_discovery_could_not_read_is_never_recorded() -> None:
    """An unreadable path in a later discovery drops an earlier fingerprint."""
    seen = FileFingerprint(path="/p/a.txt", mtime=1.5, size=1)
    first = PendingCheck("fake", "/p", FingerprintSnapshot({"/p/a.txt": seen}))
    second = PendingCheck("fake", "/p", FingerprintSnapshot({}, ("/p/a.txt",)))

    merged = _merged([first, second])

    assert merged is not None
    assert_that(merged.snapshot.fingerprints).is_empty()
    assert_that(merged.snapshot.unreadable).is_equal_to(("/p/a.txt",))


def test_incremental_cacheable_does_not_shift_positional_definition_fields() -> None:
    """External plugins passing fields positionally keep their meaning."""
    definition = ToolDefinition(
        "ext",
        "external tool",
        False,
        ToolType.LINTER,
        ExecutionClass.DETERMINISTIC,
        ["*.ext"],
        [],
        True,
        True,
        ["ext.toml"],
    )

    assert_that(definition.native_configs).is_equal_to(["ext.toml"])
    assert_that(definition.incremental_cacheable).is_true()


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="named pipes need POSIX")
def test_a_named_pipe_is_refused_without_waiting_for_a_writer(
    tmp_path: Path,
) -> None:
    """A path replaced by a FIFO is not a checked file, and never blocks.

    Opening a FIFO for reading waits for a writer; the readability test must
    answer at once, and answer no.

    Args:
        tmp_path: Directory for the pipe and a regular file.
    """
    pipe = tmp_path / "script.sh"
    os.mkfifo(pipe)
    regular = tmp_path / "other.sh"
    regular.write_text("echo ok\n")
    answers: list[bool] = []
    worker = threading.Thread(
        target=lambda: answers.append(_can_open(str(pipe))),
        daemon=True,
    )

    worker.start()
    worker.join(timeout=5)

    assert_that(worker.is_alive()).is_false()
    assert_that(answers).is_equal_to([False])
    assert_that(_can_open(str(regular))).is_true()
    assert_that(_can_open(str(tmp_path))).is_false()


def _finding_in_named(name: str) -> Behaviour:
    """Report one fully parsed finding against ``name``.

    Args:
        name: The file the finding names, relative to the project.

    Returns:
        The behaviour.
    """

    def behave(_ctx: PreparedExecution) -> ToolResult:
        return ToolResult.from_outcome(
            ToolOutcome(
                execution=Checked(success=False, inspected_files=True),
                issues_count=1,
            ),
            name="fake",
            issues=[BaseIssue(file=name, line=1, message="finding")],
            findings_complete=True,
        )

    return behave


@PARALLEL
def test_a_full_check_removes_the_clean_entry_of_a_file_it_flags(
    monkeypatch: pytest.MonkeyPatch,
    project: Path,
    parallel: bool,
) -> None:
    """Clean incremental, full run with a finding, then incremental reports it.

    Args:
        monkeypatch: Isolates the executor.
        project: Project directory.
        parallel: Whether to use the parallel dispatcher.
    """
    handed: list[list[str]] = []
    finding = _finding_in_named("good.txt")
    _run(monkeypatch, project, behaviour=_clean, parallel=parallel, handed=handed)
    full = _run(
        monkeypatch,
        project,
        behaviour=finding,
        parallel=parallel,
        handed=handed,
        incremental=False,
    )
    last = _run(
        monkeypatch,
        project,
        behaviour=finding,
        parallel=parallel,
        handed=handed,
    )

    assert_that(full.exit_code).is_equal_to(1)
    assert_that(handed).is_equal_to([BOTH, BOTH, ["good.txt"]])
    assert_that(last.exit_code).is_equal_to(1)


@PARALLEL
def test_a_finding_in_a_file_not_handed_removes_its_entry(
    monkeypatch: pytest.MonkeyPatch,
    project: Path,
    parallel: bool,
) -> None:
    """A cross-file finding drops the clean entry of the file it names.

    Args:
        monkeypatch: Isolates the executor.
        project: Project directory.
        parallel: Whether to use the parallel dispatcher.
    """
    handed: list[list[str]] = []
    _run(monkeypatch, project, behaviour=_clean, parallel=parallel, handed=handed)
    (project / "good.txt").write_text("good, edited\n")
    _run(
        monkeypatch,
        project,
        behaviour=_finding_in_named("bad.txt"),
        parallel=parallel,
        handed=handed,
    )
    _run(monkeypatch, project, behaviour=_clean, parallel=parallel, handed=handed)

    assert_that(handed).is_equal_to([BOTH, ["good.txt"], BOTH])


def test_a_clean_full_check_writes_no_cache(
    monkeypatch: pytest.MonkeyPatch,
    project: Path,
) -> None:
    """A non-incremental run never creates a cache: it only removes entries.

    Args:
        monkeypatch: Isolates the executor.
        project: Project directory.
    """
    handed: list[list[str]] = []
    _run(
        monkeypatch,
        project,
        behaviour=_clean,
        parallel=False,
        handed=handed,
        incremental=False,
    )

    assert_that(list((project.parent / "cache").glob("*.json"))).is_empty()


@PARALLEL
@pytest.mark.parametrize(
    "outcome",
    ["unavailable", "failed", "interrupted"],
)
def test_a_full_check_that_proved_nothing_keeps_existing_entries(
    monkeypatch: pytest.MonkeyPatch,
    project: Path,
    parallel: bool,
    outcome: str,
) -> None:
    """An entry claims a completed clean check of that content; it still holds.

    Args:
        monkeypatch: Isolates the executor.
        project: Project directory.
        parallel: Whether to use the parallel dispatcher.
        outcome: How the full run ended without proving anything.
    """
    handed: list[list[str]] = []
    _run(monkeypatch, project, behaviour=_clean, parallel=parallel, handed=handed)
    if outcome == "unavailable":
        early = ToolResult.from_outcome(
            ToolOutcome(Unavailable("fake is not installed")),
            name="fake",
        )
        with patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=early,
        ):
            _run(
                monkeypatch,
                project,
                behaviour=_clean,
                parallel=parallel,
                handed=handed,
                incremental=False,
            )
    elif outcome == "failed":
        _run(
            monkeypatch,
            project,
            behaviour=_failed,
            parallel=parallel,
            handed=handed,
            incremental=False,
        )
    elif not parallel:
        with pytest.raises(KeyboardInterrupt):
            _run(
                monkeypatch,
                project,
                behaviour=_raises(KeyboardInterrupt()),
                parallel=False,
                handed=handed,
                incremental=False,
            )
    else:
        pytest.skip("KeyboardInterrupt is exercised on the sequential path")
    before = len(handed)

    _run(monkeypatch, project, behaviour=_clean, parallel=parallel, handed=handed)

    assert_that(len(handed)).is_equal_to(before)
