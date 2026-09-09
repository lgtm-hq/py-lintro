"""Tests for the mutate-then-verify pipeline (#1743).

Covers the three things the verify pass is responsible for: producing one
authoritative residual instead of each mutating tool's private opinion,
catching a residual a later tool re-introduced (cross-tool interference), and
degrading to the documented floor when fingerprints cannot be trusted.
"""

from __future__ import annotations

import ast
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest
from assertpy import assert_that

import lintro.tools as lintro_tools
from lintro.enums.action import Action
from lintro.enums.capability import Cap
from lintro.models.core.claim import Claim
from lintro.models.core.tool_result import ToolResult
from lintro.parsers.base_issue import BaseIssue
from lintro.tools.core import verify_pass
from lintro.tools.core.verify_pass import (
    COARSE_MTIME_REASON,
    UNREADABLE_REASON,
    VERIFY_NOTE_TEMPLATE,
    VerifyBaseline,
    VerifyOutcome,
    VerifyScope,
    capture_verify_baseline,
    fold_verify_results,
    resolve_result_capability,
    resolve_verify_scope,
    run_verify_pass,
    verifying_tools,
)
from lintro.tools.typos.definition import TyposPlugin
from lintro.utils.file_cache import (
    FileFingerprint,
    FingerprintSnapshot,
    snapshot_fingerprints,
)

if TYPE_CHECKING:
    from lintro.tools.core.verify_pass import VerifiableTool


@dataclass
class _FakeDefinition:
    """Minimal stand-in for a ``ToolDefinition`` carrying claims.

    Attributes:
        claims: The claims the fake tool declares.
    """

    claims: list[Claim]


@dataclass
class _FakeTool:
    """Minimal stand-in for a registered plugin.

    Attributes:
        definition: The fake tool's definition.
        result: The result its ``check`` returns.
        seen_files: Files the last ``check`` call received.
    """

    definition: _FakeDefinition
    result: ToolResult | None = None
    seen_files: list[str] | None = None

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Record the paths and return the canned result.

        Args:
            paths: Files handed to the verify pass.
            options: Ignored runtime options.

        Returns:
            ToolResult: The canned result.
        """
        del options
        self.seen_files = list(paths)
        assert self.result is not None
        return self.result


def _register(
    monkeypatch: pytest.MonkeyPatch,
    tools: dict[str, _FakeTool],
) -> None:
    """Point the verify pass's registry lookups at a fake tool table.

    The pass resolves ``tool_manager`` lazily off ``lintro.tools`` (a top-level
    import there would close a cycle with the package that re-exports it), so
    the double is installed on the package rather than on the module.

    Args:
        monkeypatch: pytest monkeypatch fixture.
        tools: Fake tools keyed by registry name.
    """

    class _Manager:
        """Registry stand-in resolving only the fakes this test registered."""

        def get_tool(self, name: str) -> _FakeTool:
            """Resolve a fake tool by name.

            Args:
                name: Registry key.

            Returns:
                _FakeTool: The registered fake.
            """
            return tools[name]

    monkeypatch.setattr(lintro_tools, "tool_manager", _Manager())


def _issue(path: str) -> BaseIssue:
    """Build a minimal issue anchored at a file.

    Args:
        path: File the issue belongs to.

    Returns:
        BaseIssue: The issue.
    """
    return BaseIssue(file=path, line=1, column=1, message="boom")


def test_resolve_result_capability_prefers_fix_over_format(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A FIX+FORMAT tool reports FIX, matching the scheduler's phase order."""
    _register(
        monkeypatch,
        {
            "ruff": _FakeTool(
                definition=_FakeDefinition(
                    claims=[
                        Claim(
                            patterns=["*.py"],
                            capabilities={Cap.FIX, Cap.FORMAT, Cap.CHECK},
                        ),
                    ],
                ),
            ),
        },
    )

    assert_that(
        resolve_result_capability(tool_name="ruff", action=Action.FIX),
    ).is_equal_to(Cap.FIX)
    assert_that(
        resolve_result_capability(tool_name="ruff", action=Action.CHECK),
    ).is_equal_to(Cap.CHECK)


def test_resolve_result_capability_is_none_for_a_check_only_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A CHECK-only tool has no mutating capability to attribute a fix to."""
    _register(
        monkeypatch,
        {
            "mypy": _FakeTool(
                definition=_FakeDefinition(
                    claims=[Claim(patterns=["*.py"], capabilities={Cap.CHECK})],
                ),
            ),
        },
    )

    assert_that(
        resolve_result_capability(tool_name="mypy", action=Action.FIX),
    ).is_none()


def test_verifying_tools_skips_a_format_only_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prettier is FORMAT-only, so it is never asked for a residual."""
    _register(
        monkeypatch,
        {
            "prettier": _FakeTool(
                definition=_FakeDefinition(
                    claims=[Claim(patterns=["*.css"], capabilities={Cap.FORMAT})],
                ),
            ),
            "ruff": _FakeTool(
                definition=_FakeDefinition(
                    claims=[
                        Claim(patterns=["*.py"], capabilities={Cap.FIX, Cap.CHECK}),
                    ],
                ),
            ),
        },
    )

    assert_that(verifying_tools(["prettier", "ruff"])).is_equal_to(["ruff"])


def test_capture_verify_baseline_covers_only_mutating_patterns(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The floor is every file a mutating capability could be handed."""
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "b.md").write_text("# hi\n", encoding="utf-8")
    _register(
        monkeypatch,
        {
            "black": _FakeTool(
                definition=_FakeDefinition(
                    claims=[Claim(patterns=["*.py"], capabilities={Cap.FORMAT})],
                ),
            ),
            "markdownlint": _FakeTool(
                definition=_FakeDefinition(
                    claims=[Claim(patterns=["*.md"], capabilities={Cap.CHECK})],
                ),
            ),
        },
    )

    baseline = capture_verify_baseline(
        tools_to_run=["black", "markdownlint"],
        paths=[str(tmp_path)],
        exclude=None,
        include_venv=False,
    )

    assert_that([Path(p).name for p in baseline.candidates]).is_equal_to(["a.py"])


def test_resolve_verify_scope_narrows_to_files_whose_fingerprint_moved(
    tmp_path: Path,
) -> None:
    """Only the rewritten file is verified; the untouched one is skipped.

    The snapshot is built from explicit fractional mtimes rather than from
    ``os.utime`` plus a re-stat. A filesystem that coerces ``utime`` to
    whole-second resolution — NFS, some Docker bind mounts — would otherwise
    make the baseline unreliable and send this test down the floor path, and
    the narrowing invariant it exists to pin would go unasserted on exactly
    the CI images where it is hardest to notice.
    """
    touched = tmp_path / "touched.py"
    untouched = tmp_path / "untouched.py"
    touched.write_text("x = 1\n", encoding="utf-8")
    untouched.write_text("y = 2\n", encoding="utf-8")
    # The untouched file's fingerprint has to *match* its re-stat, so its mtime
    # is stamped rather than read: reading it back would put a host-supplied
    # (possibly whole-second) value into the snapshot and send the test down
    # the floor path on exactly the filesystems the docstring is about.
    os.utime(untouched, (1_700_000_001.25, 1_700_000_001.25))
    candidates = (str(touched), str(untouched))
    baseline = VerifyBaseline(
        candidates=candidates,
        snapshot=FingerprintSnapshot(
            fingerprints={
                # A stale mtime for the file about to be "rewritten"...
                str(touched): FileFingerprint(
                    path=str(touched),
                    mtime=1_700_000_000.25,
                    size=touched.stat().st_size,
                ),
                # ...and the stamped value for the one that is not.
                str(untouched): FileFingerprint(
                    path=str(untouched),
                    mtime=1_700_000_001.25,
                    size=untouched.stat().st_size,
                ),
            },
        ),
    )
    assert_that(baseline.snapshot.is_reliable).is_true()

    scope = resolve_verify_scope(baseline)

    assert_that(scope.narrowed).is_true()
    assert_that(list(scope.files)).is_equal_to([str(touched)])


def test_resolve_verify_scope_falls_back_to_the_floor_on_coarse_mtimes() -> None:
    """Whole-second mtime resolution hides a same-second rewrite: verify all."""
    baseline = VerifyBaseline(
        candidates=("/a.py", "/b.py"),
        snapshot=FingerprintSnapshot(
            fingerprints={
                "/a.py": FileFingerprint(path="/a.py", mtime=1.0, size=1),
                "/b.py": FileFingerprint(path="/b.py", mtime=2.0, size=2),
            },
        ),
    )

    scope = resolve_verify_scope(baseline)

    assert_that(scope.narrowed).is_false()
    assert_that(scope.floor_reason).is_equal_to(COARSE_MTIME_REASON)
    assert_that(list(scope.files)).is_equal_to(["/a.py", "/b.py"])


def test_resolve_verify_scope_falls_back_when_a_file_cannot_be_fingerprinted(
    tmp_path: Path,
) -> None:
    """A stat that failed means "we do not know", which must widen the scope."""
    real = tmp_path / "real.py"
    real.write_text("x = 1\n", encoding="utf-8")
    missing = str(tmp_path / "gone.py")
    candidates = (str(real), missing)

    snapshot = snapshot_fingerprints(candidates)
    baseline = VerifyBaseline(candidates=candidates, snapshot=snapshot)
    scope = resolve_verify_scope(baseline)

    # A snapshot with a failed stat is unreliable in its own right, not only
    # when the caller happens to compare its length against the candidates.
    assert_that(list(snapshot.unreadable)).is_equal_to([missing])
    assert_that(snapshot.is_reliable).is_false()
    assert_that(scope.narrowed).is_false()
    assert_that(scope.floor_reason).is_equal_to(UNREADABLE_REASON)
    assert_that(list(scope.files)).is_equal_to(list(candidates))


def test_run_verify_pass_runs_check_once_per_verifying_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each verifying tool runs exactly once, over the narrowed scope."""
    ruff = _FakeTool(
        definition=_FakeDefinition(
            claims=[Claim(patterns=["*.py"], capabilities={Cap.FIX, Cap.CHECK})],
        ),
        result=ToolResult(name="ruff", success=False, issues_count=2),
    )
    prettier = _FakeTool(
        definition=_FakeDefinition(
            claims=[Claim(patterns=["*.css"], capabilities={Cap.FORMAT})],
        ),
    )
    _register(monkeypatch, {"ruff": ruff, "prettier": prettier})

    outcomes = run_verify_pass(
        tools_to_run=["ruff", "prettier"],
        scope=VerifyScope(files=("/a.py",), narrowed=True),
        configure=lambda *, tool_name: cast(
            "VerifiableTool",
            {"ruff": ruff, "prettier": prettier}[tool_name],
        ),
    )

    assert_that([o.tool for o in outcomes]).is_equal_to(["ruff"])
    verified = outcomes[0].result
    assert_that(verified).is_not_none()
    assert_that(verified.capability if verified else None).is_equal_to(Cap.CHECK)
    assert_that(ruff.seen_files).is_equal_to(["/a.py"])
    assert_that(prettier.seen_files).is_none()


def test_an_empty_scope_still_reports_an_outcome_per_verifying_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing was rewritten, so no check runs — but the tool is still folded.

    Dropping the outcome would let the fold trust a mutating tool's own
    ``remaining=0``, and a file with an unfixable issue that no formatter
    rewrote would vanish from the run.
    """
    ruff = _FakeTool(
        definition=_FakeDefinition(
            claims=[Claim(patterns=["*.py"], capabilities={Cap.FIX, Cap.CHECK})],
        ),
    )
    _register(monkeypatch, {"ruff": ruff})

    outcomes = run_verify_pass(
        tools_to_run=["ruff"],
        scope=VerifyScope(files=(), narrowed=True),
        configure=lambda *, tool_name: cast("VerifiableTool", None),
    )

    assert_that([o.tool for o in outcomes]).is_equal_to(["ruff"])
    assert_that(outcomes[0].result).is_none()
    assert_that(outcomes[0].ran).is_true()
    assert_that(ruff.seen_files).is_none()


def test_fold_replaces_the_tools_own_residual_without_double_counting() -> None:
    """The verify pass's count wins; the fix pass's count is discarded."""
    mutation = ToolResult(
        name="ruff",
        success=True,
        output="Fixed 8 issue(s)",
        issues_count=2,
        issues=[_issue("/repo/a.py")],
        initial_issues=[_issue("/repo/a.py") for _ in range(10)],
        initial_issues_count=10,
        fixed_issues_count=8,
        remaining_issues_count=2,
        capability=Cap.FIX,
    )
    verify = ToolResult(
        name="ruff",
        success=False,
        issues_count=1,
        issues=[_issue("/repo/a.py")],
        capability=Cap.CHECK,
    )
    results = [mutation]

    fold_verify_results(
        mutation_results=results,
        verify_results=[VerifyOutcome(tool="ruff", result=verify)],
        scope=VerifyScope(files=("/repo/a.py",), narrowed=True),
    )

    folded = results[0]
    assert_that(folded.remaining_issues_count).is_equal_to(1)
    assert_that(folded.issues_count).is_equal_to(1)
    assert_that(folded.fixed_issues_count).is_equal_to(9)
    assert_that(folded.output).contains(
        VERIFY_NOTE_TEMPLATE.format(residual=1, previous=2),
    )


def test_fold_keeps_a_failed_mutation_failed_even_with_no_residual() -> None:
    """A failed fmt command cannot be laundered into success by a clean verify."""
    mutation = ToolResult(
        name="taplo",
        success=False,
        output="taplo fmt: error: could not parse config",
        issues_count=0,
        issues=[],
        initial_issues=[],
        initial_issues_count=0,
        fixed_issues_count=0,
        remaining_issues_count=0,
        capability=Cap.FORMAT,
    )
    verify = ToolResult(
        name="taplo",
        success=True,
        issues_count=0,
        issues=[],
        capability=Cap.CHECK,
    )
    results = [mutation]

    fold_verify_results(
        mutation_results=results,
        verify_results=[VerifyOutcome(tool="taplo", result=verify)],
        scope=VerifyScope(files=("/repo/a.toml",), narrowed=True),
    )

    folded = results[0]
    assert_that(folded.success).is_false()
    assert_that(folded.remaining_issues_count).is_equal_to(0)
    assert_that(folded.output).contains("could not parse config")


def test_fold_keeps_pre_fix_issues_when_the_check_timed_out() -> None:
    """A CHECK that timed out verified nothing: pre-fix findings are carried."""
    mutation = ToolResult(
        name="ruff",
        success=True,
        output="Fixed 1 issue(s)",
        issues_count=1,
        issues=[_issue("/repo/a.py")],
        initial_issues=[_issue("/repo/a.py"), _issue("/repo/a.py")],
        initial_issues_count=2,
        fixed_issues_count=1,
        remaining_issues_count=1,
        capability=Cap.FIX,
    )
    verify = ToolResult(
        name="ruff",
        success=False,
        output="ruff check timed out",
        issues_count=0,
        issues=[],
        timed_out=True,
        capability=Cap.CHECK,
    )
    results = [mutation]

    fold_verify_results(
        mutation_results=results,
        verify_results=[VerifyOutcome(tool="ruff", result=verify)],
        scope=VerifyScope(files=("/repo/a.py",), narrowed=True),
    )

    folded = results[0]
    assert_that(folded.success).is_false()
    assert_that(folded.remaining_issues_count).is_equal_to(2)


def test_fold_catches_a_residual_a_later_tool_reintroduced() -> None:
    """Cross-tool interference: the fix pass saw 0, the verify pass sees 1.

    This is the case no per-plugin self-verify can reach — ruff's own post-fix
    lint has already run by the time black reformats the same file.
    """
    mutation = ToolResult(
        name="ruff",
        success=True,
        output="Fixed 3 issue(s)",
        issues_count=0,
        issues=[],
        initial_issues=[_issue("/repo/a.py") for _ in range(3)],
        initial_issues_count=3,
        fixed_issues_count=3,
        remaining_issues_count=0,
        capability=Cap.FIX,
    )
    verify = ToolResult(
        name="ruff",
        success=False,
        issues_count=1,
        issues=[_issue("/repo/a.py")],
        capability=Cap.CHECK,
    )
    results = [mutation]

    fold_verify_results(
        mutation_results=results,
        verify_results=[VerifyOutcome(tool="ruff", result=verify)],
        scope=VerifyScope(files=("/repo/a.py",), narrowed=True),
    )

    assert_that(results[0].remaining_issues_count).is_equal_to(1)
    assert_that(results[0].success).is_false()


def test_fold_keeps_pre_fix_issues_for_files_the_pass_did_not_verify() -> None:
    """A file nobody rewrote keeps the issues it had before the mutation phase.

    Narrowing must not lose a residual: an untouched file was not re-checked,
    so its pre-fix findings are still exactly its post-fix findings.
    """
    mutation = ToolResult(
        name="ruff",
        success=True,
        issues_count=0,
        issues=[],
        initial_issues=[_issue("/repo/a.py"), _issue("/repo/untouched.py")],
        initial_issues_count=2,
        fixed_issues_count=2,
        remaining_issues_count=0,
        capability=Cap.FIX,
    )
    verify = ToolResult(name="ruff", success=True, issues_count=0, issues=[])
    results = [mutation]

    fold_verify_results(
        mutation_results=results,
        verify_results=[VerifyOutcome(tool="ruff", result=verify)],
        scope=VerifyScope(files=("/repo/a.py",), narrowed=True),
    )

    folded = results[0]
    assert_that(folded.remaining_issues_count).is_equal_to(1)
    assert_that([i.file for i in folded.issues or []]).is_equal_to(
        ["/repo/untouched.py"],
    )
    assert_that(folded.fixed_issues_count).is_equal_to(1)
    # The fix pass called itself a success; a leftover it never re-checked is
    # still a leftover.
    assert_that(folded.success).is_false()


def test_fold_leaves_a_tool_without_a_verify_result_alone() -> None:
    """A FORMAT-only tool keeps its own numbers, unchanged."""
    mutation = ToolResult(
        name="prettier",
        success=True,
        issues_count=0,
        initial_issues_count=4,
        fixed_issues_count=4,
        remaining_issues_count=0,
        capability=Cap.FORMAT,
    )
    results = [mutation]

    fold_verify_results(
        mutation_results=results,
        verify_results=[],
        scope=VerifyScope(files=("/repo/a.css",), narrowed=True),
    )

    assert_that(results[0].fixed_issues_count).is_equal_to(4)
    assert_that(results[0].remaining_issues_count).is_equal_to(0)


def test_fold_skips_a_skipped_or_timed_out_tool() -> None:
    """A tool that never ran, or died on a deadline, keeps its own state."""
    skipped = ToolResult(name="ruff", skipped=True, skip_reason="disabled in config")
    timed_out = ToolResult(
        name="black",
        success=False,
        timed_out=True,
        issues_count=7,
        initial_issues_count=7,
        fixed_issues_count=0,
        remaining_issues_count=7,
    )
    results = [skipped, timed_out]

    fold_verify_results(
        mutation_results=results,
        verify_results=[
            VerifyOutcome(
                tool="ruff",
                result=ToolResult(name="ruff", success=True, issues_count=0),
            ),
            VerifyOutcome(
                tool="black",
                result=ToolResult(name="black", success=True, issues_count=0),
            ),
        ],
        scope=VerifyScope(files=("/repo/a.py",), narrowed=True),
    )

    assert_that(results[0].skipped).is_true()
    assert_that(results[1].remaining_issues_count).is_equal_to(7)


def test_the_floor_hands_tools_the_original_scan_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Falling back must not mean passing thousands of file arguments.

    The floor covers the same files as the candidate list, so handing the
    run's original scan targets over and letting each tool discover its own
    files keeps the fallback from costing more than the run it verifies.
    """
    ruff = _FakeTool(
        definition=_FakeDefinition(
            claims=[Claim(patterns=["*.py"], capabilities={Cap.FIX, Cap.CHECK})],
        ),
        result=ToolResult(name="ruff", success=True, issues_count=0),
    )
    _register(monkeypatch, {"ruff": ruff})
    scope = VerifyScope(
        files=("/repo/a.py", "/repo/b.py"),
        narrowed=False,
        floor_reason=COARSE_MTIME_REASON,
        targets=("/repo",),
    )

    run_verify_pass(
        tools_to_run=["ruff"],
        scope=scope,
        configure=lambda *, tool_name: cast("VerifiableTool", ruff),
    )

    assert_that(ruff.seen_files).is_equal_to(["/repo"])
    assert_that(scope.summary).is_equal_to(
        f"2 file(s) ({COARSE_MTIME_REASON})",
    )


def test_a_check_that_raises_carries_every_pre_fix_issue_and_fails() -> None:
    """A verify we could not run must not read as "everything was fixed".

    ``ran=False`` means the residual is unknown, so the fold falls back to the
    tool's pre-fix findings for every file and refuses to report success.
    """
    mutation = ToolResult(
        name="taplo",
        success=True,
        issues_count=0,
        issues=[],
        initial_issues=[_issue("/repo/a.toml"), _issue("/repo/b.toml")],
        initial_issues_count=2,
        fixed_issues_count=2,
        remaining_issues_count=0,
        capability=Cap.FORMAT,
    )
    results = [mutation]

    fold_verify_results(
        mutation_results=results,
        verify_results=[VerifyOutcome(tool="taplo", result=None, ran=False)],
        scope=VerifyScope(files=("/repo/a.toml", "/repo/b.toml"), narrowed=True),
    )

    assert_that(results[0].remaining_issues_count).is_equal_to(2)
    assert_that(results[0].fixed_issues_count).is_equal_to(0)
    assert_that(results[0].success).is_false()


def test_nothing_rewritten_keeps_the_issues_the_fix_pass_could_not_fix() -> None:
    """An empty scope is a clean answer, not a licence to report zero.

    The mutating tool claimed it fixed both issues; nothing on disk moved, so
    both are still there.
    """
    mutation = ToolResult(
        name="taplo",
        success=True,
        issues_count=0,
        issues=[],
        initial_issues=[_issue("/repo/a.toml")],
        initial_issues_count=1,
        fixed_issues_count=1,
        remaining_issues_count=0,
        capability=Cap.FORMAT,
    )
    results = [mutation]

    fold_verify_results(
        mutation_results=results,
        verify_results=[VerifyOutcome(tool="taplo", result=None, ran=True)],
        scope=VerifyScope(files=(), narrowed=True),
    )

    assert_that(results[0].remaining_issues_count).is_equal_to(1)
    assert_that(results[0].fixed_issues_count).is_equal_to(0)
    assert_that(results[0].success).is_false()


def test_a_relative_issue_path_resolves_against_the_tools_working_directory() -> None:
    """Tool paths are relative; scope files are absolute. The fold joins them.

    ruff, ``run_per_file_fix``, clippy and rustfmt all report paths relative to
    the directory they ran in. If the fold compared those strings to the
    absolute paths it fingerprinted, a file the pass *did* re-check would look
    unverified and its pre-fix findings would be counted a second time.
    """
    mutation = ToolResult(
        name="ruff",
        success=True,
        issues_count=0,
        issues=[],
        initial_issues=[_issue("a.py"), _issue("sub/b.py")],
        initial_issues_count=2,
        fixed_issues_count=2,
        remaining_issues_count=0,
        cwd="/repo",
        capability=Cap.FIX,
    )
    verify = ToolResult(name="ruff", success=True, issues_count=0, issues=[])
    results = [mutation]

    fold_verify_results(
        mutation_results=results,
        verify_results=[VerifyOutcome(tool="ruff", result=verify)],
        # Absolute, the way ``walk_files_with_excludes`` reports them.
        scope=VerifyScope(files=("/repo/a.py",), narrowed=True),
    )

    folded = results[0]
    # a.py was verified and came back clean; sub/b.py never was, so it stands.
    assert_that(folded.remaining_issues_count).is_equal_to(1)
    assert_that([i.file for i in folded.issues or []]).is_equal_to(["sub/b.py"])


def test_a_check_that_raises_a_programming_error_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bug in the verify path is not a tool that could not run.

    The mutation phase re-raises ``TypeError``/``AttributeError`` instead of
    folding them into a failed result; the verify pass matches it.

    Args:
        monkeypatch: pytest monkeypatch fixture.
    """
    ruff = _FakeTool(
        definition=_FakeDefinition(
            claims=[Claim(patterns=["*.py"], capabilities={Cap.FIX, Cap.CHECK})],
        ),
    )
    _register(monkeypatch, {"ruff": ruff})

    def _boom(*, tool_name: str) -> VerifiableTool:
        raise AttributeError(tool_name)

    with pytest.raises(AttributeError):
        run_verify_pass(
            tools_to_run=["ruff"],
            scope=VerifyScope(files=("/a.py",), narrowed=True),
            configure=_boom,
        )


@pytest.mark.parametrize("narrowing", ["incremental", "diff"])
def test_capture_verify_baseline_honours_the_runs_own_narrowing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    narrowing: str,
) -> None:
    """The floor is what *this* run could have touched, not the whole tree.

    ``--incremental`` and ``--diff`` share the rule that stops the floor from
    re-widening to the scan root, so both sides of it are exercised. Without
    it the floor re-checks the whole tree and reports every pre-existing
    diagnostic in it as the run's residual.

    Args:
        monkeypatch: pytest monkeypatch fixture.
        tmp_path: Temporary workspace.
        narrowing: Which flag narrowed the run.
    """
    changed = tmp_path / "changed.py"
    unchanged = tmp_path / "unchanged.py"
    changed.write_text("x = 1\n", encoding="utf-8")
    unchanged.write_text("y = 2\n", encoding="utf-8")
    _register(
        monkeypatch,
        {
            "black": _FakeTool(
                definition=_FakeDefinition(
                    claims=[Claim(patterns=["*.py"], capabilities={Cap.FORMAT})],
                ),
            ),
        },
    )
    walk_kwargs: list[dict[str, Any]] = []

    def _record_walk(**kwargs: Any) -> list[str]:
        """Record the walk arguments and report a single changed file.

        Args:
            **kwargs: Arguments ``capture_verify_baseline`` passed through.

        Returns:
            list[str]: The one file this run could have touched.
        """
        walk_kwargs.append(kwargs)
        return [str(changed)]

    if narrowing == "incremental":
        monkeypatch.setattr(
            verify_pass,
            "_incremental_subset",
            lambda *, tool_name, files: [f for f in files if str(changed) == f],
        )
        incremental = True
        diff_base: str | None = None
    else:
        monkeypatch.setattr(verify_pass, "walk_files_with_excludes", _record_walk)
        incremental = False
        diff_base = "origin/main"

    baseline = capture_verify_baseline(
        tools_to_run=["black"],
        paths=[str(tmp_path)],
        exclude=None,
        include_venv=False,
        incremental=incremental,
        diff_base=diff_base,
    )

    if narrowing == "diff":
        # The scoping arguments have to reach the walk, not merely be accepted
        # by the signature: deleting ``diff_base=diff_base`` must fail here.
        assert_that(walk_kwargs).is_length(1)
        assert_that(walk_kwargs[0]["diff_base"]).is_equal_to("origin/main")
        assert_that(walk_kwargs[0]["file_patterns"]).is_equal_to(["*.py"])
    assert_that(list(baseline.candidates)).is_equal_to([str(changed)])
    # The scan paths cover more than the candidates now, so the floor must
    # name the files rather than re-widening to the tree.
    assert_that(list(baseline.scan_paths)).is_empty()
    assert_that(
        list(
            VerifyScope(
                files=baseline.candidates,
                narrowed=False,
                floor_reason=COARSE_MTIME_REASON,
                targets=baseline.scan_paths,
            ).scan_targets,
        ),
    ).is_equal_to([str(changed)])


def test_an_operational_check_failure_records_ran_false_and_the_run_continues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tool whose check dies is reported as unverified, not skipped.

    ``ran=False`` is what makes the fold carry that tool's pre-fix findings
    instead of believing its ``remaining=0``. One tool failing must not stop
    the tools after it from being verified.

    Args:
        monkeypatch: pytest monkeypatch fixture.
    """
    claims = [Claim(patterns=["*.py"], capabilities={Cap.FIX, Cap.CHECK})]
    broken = _FakeTool(definition=_FakeDefinition(claims=claims))
    healthy = _FakeTool(
        definition=_FakeDefinition(claims=claims),
        result=ToolResult(name="black", success=True, issues_count=0),
    )
    _register(monkeypatch, {"ruff": broken, "black": healthy})

    def _configure(*, tool_name: str) -> VerifiableTool:
        if tool_name == "ruff":
            raise OSError("ruff binary vanished")
        return cast("VerifiableTool", healthy)

    outcomes = run_verify_pass(
        tools_to_run=["ruff", "black"],
        scope=VerifyScope(files=("/a.py",), narrowed=True),
        configure=_configure,
    )

    assert_that([(o.tool, o.ran) for o in outcomes]).is_equal_to(
        [("ruff", False), ("black", True)],
    )
    assert_that(outcomes[0].result).is_none()
    # The tool after the failure still ran.
    assert_that(healthy.seen_files).is_equal_to(["/a.py"])


def test_every_fmt_tool_claims_exactly_what_it_discovers() -> None:
    """Claims and discovery patterns must not drift apart.

    The verify pass's candidate walk keys off ``claims``; a plugin's own
    discovery keys off ``file_patterns``. ``capture_verify_baseline`` deliberately walks the claimed patterns rather
    than calling ``discover_files`` (that path writes the incremental cache,
    which must not happen before the mutation phase). This pins the assumption
    that makes the duplication safe: for every tool that can mutate, the
    patterns it claims are the patterns it discovers.
    """
    from lintro.tools import tool_manager

    mismatched: dict[str, tuple[list[str], list[str]]] = {}
    project_scoped: list[str] = []
    for name in tool_manager.get_fix_tools():
        definition = tool_manager.get_tool(name).definition
        mutating = [
            claim
            for claim in getattr(definition, "claims", None) or ()
            if claim.is_mutating
        ]
        if not mutating:
            continue
        claimed: set[str] = set()
        for claim in mutating:
            claimed.update(claim.patterns)
        if not claimed:
            # A fix tool whose mutating claim is project-scoped is the one
            # shape the pass cannot handle: ``_mutating_patterns_by_tool``
            # drops it, so nothing of its is ever fingerprinted, the scope
            # comes back empty and the fold carries every pre-fix finding —
            # a clean fix would report ``fixed=0`` and fail the run. No tool
            # declares one today; this fails the day one does.
            project_scoped.append(name)
            continue
        discovered = set(definition.file_patterns or ())
        if claimed != discovered:
            mismatched[name] = (sorted(claimed), sorted(discovered))

    assert_that(project_scoped).is_empty()
    assert_that(mismatched).is_equal_to({})


def test_a_whole_project_check_supersedes_findings_outside_the_scope() -> None:
    """A file the CHECK reported on is verified, whatever the scope asked for.

    clippy runs ``cargo clippy`` from the crate root with no file arguments,
    and golangci-lint does the same from the module root, so their verify
    ``CHECK`` reports on files the narrowed scope never named. Counting only
    ``scope.files`` as verified would carry those files' pre-fix findings as
    survivors *and* append the same findings again, inflating ``remaining``,
    deflating ``fixed``, and leaving a residual that can never reach zero.
    """
    mutation = ToolResult(
        name="clippy",
        success=True,
        issues_count=0,
        issues=[],
        initial_issues=[_issue("/repo/src/main.rs"), _issue("/repo/src/lib.rs")],
        initial_issues_count=2,
        fixed_issues_count=2,
        remaining_issues_count=0,
        cwd="/repo",
        capability=Cap.FIX,
    )
    # The scope named only main.rs, but a crate-wide check answers for both.
    verify = ToolResult(
        name="clippy",
        success=False,
        issues_count=1,
        issues=[_issue("src/lib.rs")],
        cwd="/repo",
    )
    results = [mutation]

    fold_verify_results(
        mutation_results=results,
        verify_results=[VerifyOutcome(tool="clippy", result=verify)],
        scope=VerifyScope(files=("/repo/src/main.rs",), narrowed=True),
    )

    folded = results[0]
    # One finding, not two: lib.rs is verified by the check that named it, so
    # its pre-fix finding is replaced rather than added to.
    assert_that(folded.remaining_issues_count).is_equal_to(1)
    assert_that([i.file for i in folded.issues or []]).is_equal_to(["src/lib.rs"])
    assert_that(folded.fixed_issues_count).is_equal_to(1)


def test_a_finding_with_no_file_never_verifies_anything() -> None:
    """A position-less finding must not be read as a verdict about a file.

    golangci-lint parks findings it cannot place under a ``(module)``
    placeholder. Those name no file, so they can neither be matched to a
    fingerprinted path nor supersede one.
    """
    mutation = ToolResult(
        name="golangci_lint",
        success=True,
        issues_count=0,
        issues=[],
        initial_issues=[_issue("/repo/a.go")],
        initial_issues_count=1,
        fixed_issues_count=1,
        remaining_issues_count=0,
        cwd="/repo",
        capability=Cap.FIX,
    )
    verify = ToolResult(
        name="golangci_lint",
        success=False,
        issues_count=1,
        issues=[_issue("")],
        cwd="/repo",
    )
    results = [mutation]

    fold_verify_results(
        mutation_results=results,
        verify_results=[VerifyOutcome(tool="golangci_lint", result=verify)],
        scope=VerifyScope(files=(), narrowed=True),
    )

    # The unplaceable finding plus the pre-fix one it did not answer for.
    assert_that(results[0].remaining_issues_count).is_equal_to(2)


def test_a_tool_the_registry_cannot_resolve_is_reported_unverified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unresolvable name must not silently drop out of the verify pass.

    "Declares no CHECK" (prettier) and "cannot be resolved" used to collapse
    onto the same empty-claims value, and only the first is safe to fold as
    trust-the-mutation-result. The second knows nothing about the tool, so its
    self-reported ``remaining=0`` must not stand.

    Args:
        monkeypatch: pytest monkeypatch fixture.
    """
    healthy = _FakeTool(
        definition=_FakeDefinition(
            claims=[Claim(patterns=["*.py"], capabilities={Cap.FIX, Cap.CHECK})],
        ),
        result=ToolResult(name="ruff", success=True, issues_count=0),
    )
    # ``_register``'s fake registry raises KeyError for anything else.
    _register(monkeypatch, {"ruff": healthy})

    outcomes = run_verify_pass(
        tools_to_run=["ghost", "ruff"],
        scope=VerifyScope(files=("/a.py",), narrowed=True),
        configure=lambda *, tool_name: cast("VerifiableTool", healthy),
    )

    assert_that([(o.tool, o.ran) for o in outcomes]).is_equal_to(
        [("ghost", False), ("ruff", True)],
    )
    assert_that(outcomes[0].result).is_none()


def test_an_empty_candidate_set_needs_no_scope_at_all() -> None:
    """No mutating tool claimed a pattern, so there is nothing to verify."""
    scope = resolve_verify_scope(VerifyBaseline(candidates=()))

    assert_that(list(scope.files)).is_empty()
    assert_that(scope.narrowed).is_true()
    assert_that(scope.floor_reason).is_empty()
    assert_that(scope.summary).is_equal_to("0 changed file(s)")


def test_resolve_result_capability_reports_format_for_a_format_only_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prettier holds ``FORMAT`` alone, so that is what its result represents.

    Args:
        monkeypatch: pytest monkeypatch fixture.
    """
    _register(
        monkeypatch,
        {
            "prettier": _FakeTool(
                definition=_FakeDefinition(
                    claims=[Claim(patterns=["*.css"], capabilities={Cap.FORMAT})],
                ),
            ),
        },
    )

    assert_that(
        resolve_result_capability(tool_name="prettier", action=Action.FIX),
    ).is_equal_to(Cap.FORMAT)


def test_cli_excludes_are_split_and_reach_the_candidate_walk(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """``--exclude`` arrives as one comma-joined string and must be split.

    The executor hands the raw CLI value straight through, so the parsing
    contract lives here rather than at the call site.

    Args:
        monkeypatch: pytest monkeypatch fixture.
        tmp_path: Temporary workspace.
    """
    _register(
        monkeypatch,
        {
            "black": _FakeTool(
                definition=_FakeDefinition(
                    claims=[Claim(patterns=["*.py"], capabilities={Cap.FORMAT})],
                ),
            ),
        },
    )
    walk_kwargs: list[dict[str, Any]] = []

    def _record_walk(**kwargs: Any) -> list[str]:
        """Record the walk arguments and report no files.

        Args:
            **kwargs: Arguments ``capture_verify_baseline`` passed through.

        Returns:
            list[str]: An empty candidate set.
        """
        walk_kwargs.append(kwargs)
        return []

    monkeypatch.setattr(verify_pass, "walk_files_with_excludes", _record_walk)

    capture_verify_baseline(
        tools_to_run=["black"],
        paths=[str(tmp_path)],
        exclude="build, dist ,",
        include_venv=False,
    )

    patterns: list[str] = walk_kwargs[0]["exclude_patterns"]
    assert_that(patterns[:2]).is_equal_to(["build", "dist"])
    # The built-in defaults and .lintro-ignore are appended after them.
    assert_that(patterns).contains(".git")


def test_a_result_without_a_cwd_resolves_relative_paths_against_the_process_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The documented last resort, pinned so it cannot change unnoticed.

    Every tool that reaches the fold stamps ``cwd`` today, but ``_issue_path``
    still has to answer for one that does not. Falling back to the process
    directory is the only thing it can do, and it is only correct when the
    tool happened to run there — which is exactly why this is a footgun worth
    having under test rather than an invisible branch.

    Args:
        monkeypatch: pytest monkeypatch fixture.
        tmp_path: Directory the process is pretending to run in.
    """
    monkeypatch.chdir(tmp_path)
    target = str(tmp_path / "a.py")
    mutation = ToolResult(
        name="ruff",
        success=True,
        issues_count=0,
        issues=[],
        # Relative, with no cwd recorded on the result.
        initial_issues=[_issue("a.py"), _issue("elsewhere/b.py")],
        initial_issues_count=2,
        fixed_issues_count=2,
        remaining_issues_count=0,
        cwd=None,
        capability=Cap.FIX,
    )
    verify = ToolResult(name="ruff", success=True, issues_count=0, issues=[])
    results = [mutation]

    fold_verify_results(
        mutation_results=results,
        verify_results=[VerifyOutcome(tool="ruff", result=verify)],
        scope=VerifyScope(files=(target,), narrowed=True),
    )

    folded = results[0]
    # ``a.py`` resolved to the scoped file through the process directory and
    # is therefore verified; the one outside it survives.
    assert_that(folded.remaining_issues_count).is_equal_to(1)
    assert_that([i.file for i in folded.issues or []]).is_equal_to(["elsewhere/b.py"])


def test_a_verify_result_without_a_cwd_falls_back_to_the_mutations(
    tmp_path: Path,
) -> None:
    """A check that records no directory is read in the fix's, not the process's.

    Check-side results largely do not stamp ``cwd``: clippy's
    ``BatchCheckPolicy`` leaves ``report_cwd`` off and ``execute_ruff_check``
    sets none. A tool's check and its fix run from the same place, so the
    mutation result's directory is the right key — and without it a
    crate-relative ``src/lib.rs`` would resolve under the *process* directory,
    match nothing in the scope, and the whole-project union would silently
    never engage.

    Args:
        tmp_path: Stand-in for the crate root.
    """
    crate = str(tmp_path)
    mutation = ToolResult(
        name="clippy",
        success=True,
        issues_count=0,
        issues=[],
        initial_issues=[_issue("src/lib.rs")],
        initial_issues_count=1,
        fixed_issues_count=1,
        remaining_issues_count=0,
        cwd=crate,
        capability=Cap.FIX,
    )
    # Crate-relative path, no cwd of its own — the shape clippy really returns.
    verify = ToolResult(
        name="clippy",
        success=False,
        issues_count=1,
        issues=[_issue("src/lib.rs")],
        cwd=None,
    )
    results = [mutation]

    fold_verify_results(
        mutation_results=results,
        verify_results=[VerifyOutcome(tool="clippy", result=verify)],
        # The scope never named lib.rs: the crate-wide check answered for it.
        scope=VerifyScope(files=(str(tmp_path / "src" / "main.rs"),), narrowed=True),
    )

    # One finding, not two: the pre-fix one is superseded, not added to.
    assert_that(results[0].remaining_issues_count).is_equal_to(1)


def test_run_verify_pass_reports_a_skipped_check_as_unverified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A version gate that skips the CHECK is not a clean verdict.

    ``verify_tool_version`` returns ``success=True, issues_count=0,
    skipped=True`` when a tool is too old, which is byte-for-byte the shape of
    "nothing wrong here". Reading it as a verdict would mark every file in the
    scope verified and drop the tool's pre-fix findings.

    Args:
        monkeypatch: pytest monkeypatch fixture.
    """
    ruff = _FakeTool(
        definition=_FakeDefinition(
            claims=[Claim(patterns=["*.py"], capabilities={Cap.FIX, Cap.CHECK})],
        ),
        result=ToolResult(
            name="ruff",
            success=True,
            issues_count=0,
            skipped=True,
            skip_reason="ruff 0.1.0 is older than the required 0.5.0",
        ),
    )
    _register(monkeypatch, {"ruff": ruff})

    outcomes = run_verify_pass(
        tools_to_run=["ruff"],
        scope=VerifyScope(files=("/a.py",), narrowed=True),
        configure=lambda *, tool_name: cast("VerifiableTool", ruff),
    )

    assert_that([o.tool for o in outcomes]).is_equal_to(["ruff"])
    assert_that(outcomes[0].ran).is_false()
    assert_that(outcomes[0].result).is_none()


def test_run_verify_pass_treats_a_no_files_check_as_verifying_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tool that discovered none of the scope's files verified none of them.

    The scope is the union over every mutating tool, so a tool that rewrote
    nothing is routinely handed another tool's files and returns
    ``prepare``'s "No files to check." early result. That is not a failure —
    the tool rewrote nothing, so its pre-fix findings simply stand — but it is
    not a clean verdict over the scope either.

    Args:
        monkeypatch: pytest monkeypatch fixture.
    """
    ruff = _FakeTool(
        definition=_FakeDefinition(
            claims=[Claim(patterns=["*.py"], capabilities={Cap.FIX, Cap.CHECK})],
        ),
        result=ToolResult(
            name="ruff",
            success=True,
            output="No .py files found to check.",
            issues_count=0,
            no_files=True,
        ),
    )
    _register(monkeypatch, {"ruff": ruff})

    outcomes = run_verify_pass(
        tools_to_run=["ruff"],
        scope=VerifyScope(files=("/a.css",), narrowed=True),
        configure=lambda *, tool_name: cast("VerifiableTool", ruff),
    )

    assert_that(outcomes[0].ran).is_true()
    assert_that(outcomes[0].result).is_none()


def test_fold_never_reads_a_skipped_check_as_a_clean_verdict() -> None:
    """A skipped CHECK verifies nothing, so the pre-fix findings survive.

    Belt and braces for the fail-open ``run_verify_pass`` already closes: a
    hand-built outcome carrying a skipped result must not clear the scope
    either.
    """
    mutation = ToolResult(
        name="ruff",
        success=True,
        output="Fixed 1 issue(s)",
        issues_count=0,
        issues=[],
        initial_issues=[_issue("/repo/a.py"), _issue("/repo/a.py")],
        initial_issues_count=2,
        fixed_issues_count=2,
        remaining_issues_count=0,
        capability=Cap.FIX,
    )
    verify = ToolResult(
        name="ruff",
        success=True,
        issues_count=0,
        issues=[],
        skipped=True,
        skip_reason="ruff is older than the required version",
    )
    results = [mutation]

    fold_verify_results(
        mutation_results=results,
        verify_results=[VerifyOutcome(tool="ruff", result=verify)],
        scope=VerifyScope(files=("/repo/a.py",), narrowed=True),
    )

    folded = results[0]
    assert_that(folded.remaining_issues_count).is_equal_to(2)
    assert_that(folded.fixed_issues_count).is_equal_to(0)
    assert_that(folded.success).is_false()


def test_the_verify_baseline_reads_the_incremental_cache_without_writing_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """``_incremental_subset`` must never persist the cache it consults.

    It runs *before* the mutation phase, so writing the cache would record
    every candidate as up to date and tell the next incremental run there was
    nothing to format. The property is "this reads and does not write", which
    only an unstubbed call over a real cache directory can pin.

    Args:
        monkeypatch: pytest monkeypatch fixture.
        tmp_path: Temporary directory standing in for the cache root.
    """
    from lintro.utils import file_cache

    unchanged = tmp_path / "unchanged.py"
    changed = tmp_path / "changed.py"
    unchanged.write_text("x = 1\n", encoding="utf-8")
    changed.write_text("y = 2\n", encoding="utf-8")
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    cache_file = cache_dir / "black.json"
    stat = unchanged.stat()
    cache_file.write_text(
        json.dumps(
            {
                "tool_name": "black",
                "fingerprints": {
                    str(unchanged): {
                        "path": str(unchanged),
                        "mtime": stat.st_mtime,
                        "size": stat.st_size,
                    },
                },
            },
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(file_cache, "CACHE_DIR", cache_dir)
    before = cache_file.read_text(encoding="utf-8")

    subset = verify_pass._incremental_subset(
        tool_name="black",
        files=[str(unchanged), str(changed)],
    )

    assert_that(subset).is_equal_to([str(changed)])
    # No new cache file, and the seeded one is byte-identical: a switch to
    # ``walk_files_with_excludes(incremental=True)`` would fail here.
    assert_that(sorted(p.name for p in cache_dir.iterdir())).is_equal_to(
        ["black.json"],
    )
    assert_that(cache_file.read_text(encoding="utf-8")).is_equal_to(before)


def test_run_verify_pass_reports_a_timed_out_check_as_unverified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A partial CHECK is no verdict, even when it parsed findings.

    golangci-lint loops over module roots and returns one aggregated result
    carrying ``issues`` from the roots that finished and ``timed_out=True``
    from the one that did not. Reading that as an answer would mark every file
    in the scope verified and drop the pre-fix findings on the module that was
    never re-checked.

    Args:
        monkeypatch: pytest monkeypatch fixture.
    """
    golangci = _FakeTool(
        definition=_FakeDefinition(
            claims=[Claim(patterns=["*.go"], capabilities={Cap.FIX, Cap.CHECK})],
        ),
        result=ToolResult(
            name="golangci-lint",
            success=False,
            output="golangci-lint timed out",
            issues_count=1,
            issues=[_issue("/repo/a.go")],
            timed_out=True,
        ),
    )
    _register(monkeypatch, {"golangci-lint": golangci})

    outcomes = run_verify_pass(
        tools_to_run=["golangci-lint"],
        scope=VerifyScope(files=("/repo/a.go",), narrowed=True),
        configure=lambda *, tool_name: cast("VerifiableTool", golangci),
    )

    assert_that(outcomes[0].ran).is_false()
    assert_that(outcomes[0].result).is_none()


def test_fold_never_reads_a_timed_out_check_as_a_verdict() -> None:
    """A hand-built timed-out outcome cannot re-open the fail-open either.

    ``run_verify_pass`` already converts a timed-out CHECK to ``result=None``,
    so this pins the guard in ``_fold_one``: a partial result that still
    carries parsed findings must leave every pre-fix finding standing rather
    than count the difference as fixed.
    """
    mutation = ToolResult(
        name="golangci-lint",
        success=True,
        output="Fixed 2 issue(s)",
        issues_count=0,
        issues=[],
        initial_issues=[_issue("/repo/a.go"), _issue("/repo/b.go")],
        initial_issues_count=2,
        fixed_issues_count=2,
        remaining_issues_count=0,
        capability=Cap.FIX,
    )
    verify = ToolResult(
        name="golangci-lint",
        success=False,
        output="golangci-lint timed out",
        issues_count=1,
        issues=[_issue("/repo/a.go")],
        timed_out=True,
        capability=Cap.CHECK,
    )
    results = [mutation]

    fold_verify_results(
        mutation_results=results,
        verify_results=[VerifyOutcome(tool="golangci-lint", result=verify)],
        scope=VerifyScope(files=("/repo/a.go", "/repo/b.go"), narrowed=True),
    )

    folded = results[0]
    # Both pre-fix findings survive: neither file was re-checked to completion.
    assert_that(folded.remaining_issues_count).is_equal_to(2)
    assert_that(folded.fixed_issues_count).is_equal_to(0)
    assert_that(folded.success).is_false()


def _tool_result_calls() -> list[tuple[str, int, str, set[str]]]:
    """Return every literal ``ToolResult(...)`` construction under ``lintro``.

    Returns:
        ``(path, lineno, output_text, keyword_names)`` for each call whose
        ``output`` is a literal string (f-strings contribute their constant
        parts).
    """
    calls: list[tuple[str, int, str, set[str]]] = []
    package_root = Path(verify_pass.__file__).parents[2]
    for source in sorted(package_root.rglob("*.py")):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if getattr(node.func, "id", None) != "ToolResult":
                continue
            keywords = {kw.arg for kw in node.keywords if kw.arg}
            output = next(
                (kw.value for kw in node.keywords if kw.arg == "output"),
                None,
            )
            if isinstance(output, ast.Constant) and isinstance(output.value, str):
                text = output.value
            elif isinstance(output, ast.JoinedStr):
                text = "".join(
                    part.value
                    for part in output.values
                    if isinstance(part, ast.Constant) and isinstance(part.value, str)
                )
            else:
                continue
            calls.append((str(source), node.lineno, text, keywords))
    return calls


def test_every_nothing_examined_result_is_flagged() -> None:
    """A "no files" result must say so structurally, not only in prose.

    ``run_verify_pass`` reads ``no_files`` (and ``skipped``) to tell "I
    examined nothing" apart from "I examined the scope and it is clean". A
    plugin that builds the first shape by hand without the flag hands the pass
    a clean verdict it never earned, and the tool's pre-fix findings are
    dropped as fixed. Missing-configuration results are excluded: they mean
    the tool could not run at all, which the display already renders on its
    own terms.
    """
    unflagged = [
        f"{path}:{lineno} {text!r}"
        for path, lineno, text, keywords in _tool_result_calls()
        if text.startswith("No ")
        and "configuration" not in text
        and not keywords & {"no_files", "skipped"}
    ]

    assert_that(unflagged).is_empty()


def test_typos_reports_an_all_binary_candidate_set_as_no_files() -> None:
    """Typos builds its own no-files result, so it must stamp the flag too.

    The message is ``None`` there, so the display's prose fallback cannot see
    it at all — only the structured flag can.
    """
    result = TyposPlugin()._no_files_result(cwd="/repo")

    assert_that(result.no_files).is_true()
    assert_that(result.success).is_true()
    assert_that(result.issues_count).is_equal_to(0)
