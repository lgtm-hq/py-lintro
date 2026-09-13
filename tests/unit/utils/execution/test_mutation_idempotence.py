"""Running the mutation phase twice changes nothing the second time (#2606).

Idempotence is the observable form of "no write was lost". Two tools that
rewrite one file in sequence converge; the same two racing on it do not,
because whichever write lands second silently discards the other. This drives
the real ``run_tools_parallel`` over a real temporary tree with the real
scheduler deciding the batches — only the two tools are doubles, because the
point is what happens to the bytes on disk.
"""

from __future__ import annotations

import hashlib
import threading
import time
from typing import TYPE_CHECKING, Any, cast

from assertpy import assert_that

import lintro.utils.execution.parallel_executor as parallel_module
from lintro.enums.action import Action
from lintro.models.core.tool_result import ToolResult
from lintro.tools import tool_manager
from lintro.utils.execution.parallel_executor import run_tools_parallel
from lintro.utils.unified_config import UnifiedConfigManager

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    import pytest

#: Long enough that a concurrent dispatch would interleave the read and the
#: write of the two doubles, and short enough to stay a unit test.
_WRITE_WINDOW_SECONDS: float = 0.05


class _RewritingTool:
    """Tool double that rewrites a file the way a real formatter would.

    It reads the whole file, holds the contents for a beat, then writes the
    transformed text back. That read-hold-write shape is what makes a lost
    write observable: under a concurrent dispatch the second writer overwrites
    the first one's edit instead of building on it.
    """

    def __init__(
        self,
        *,
        name: str,
        target: Path,
        transform: Callable[[str], str],
        live: list[str],
        lock: threading.Lock,
    ) -> None:
        """Store the double's identity and its rewrite.

        The real tool's definition is carried through unchanged: the
        scheduler reads its claims to decide the batches, and this test is
        about what those batches do to the file.

        Args:
            name: Registry key this double stands in for.
            target: File the double rewrites.
            transform: Pure text transformation to apply.
            live: Shared log of tool names seen in flight together.
            lock: Guards ``live``.
        """
        self.name = name
        self.definition = tool_manager.get_tool(name).definition
        self._target = target
        self._transform = transform
        self._live = live
        self._lock = lock

    def fix(self, _paths: list[str], _options: dict[str, Any]) -> ToolResult:
        """Rewrite the target file, recording anyone else in flight.

        Args:
            _paths: Ignored paths.
            _options: Ignored options.

        Returns:
            ToolResult: A clean result for this tool.
        """
        with self._lock:
            self._live.append(self.name)
            concurrent = len(self._live) > 1
        before = self._target.read_text(encoding="utf-8")
        time.sleep(_WRITE_WINDOW_SECONDS)
        self._target.write_text(self._transform(before), encoding="utf-8")
        with self._lock:
            self._live.remove(self.name)
            if concurrent:
                self._live.append("overlapped")
        return ToolResult(
            name=self.name,
            success=True,
            output="ok",
            issues_count=0,
            initial_issues_count=0,
            fixed_issues_count=0,
            remaining_issues_count=0,
        )


def _digest(path: Path) -> str:
    """Hash a file's contents.

    Args:
        path: File to hash.

    Returns:
        Hex digest of the file's bytes.
    """
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run_mutation_phase(
    *,
    monkeypatch: pytest.MonkeyPatch,
    tree: Path,
    target: Path,
    live: list[str],
) -> None:
    """Run ruff and typos over the tree as a mutating action.

    The batching is the real scheduler's, so this exercises the rule under
    test rather than a fixed batch list.

    Args:
        monkeypatch: pytest monkeypatch fixture.
        tree: Scan root handed to the run.
        target: File both doubles rewrite.
        live: Shared log of tools in flight.
    """
    lock = threading.Lock()
    doubles = {
        "ruff": _RewritingTool(
            name="ruff",
            target=target,
            transform=lambda text: "".join(
                sorted(line + "\n" for line in text.splitlines()),
            ),
            live=live,
            lock=lock,
        ),
        "typos": _RewritingTool(
            name="typos",
            target=target,
            transform=lambda text: text.upper(),
            live=live,
            lock=lock,
        ),
    }
    monkeypatch.setattr(tool_manager, "get_tool", lambda name: doubles[name])
    monkeypatch.setattr(
        parallel_module,
        "configure_tool_for_execution",
        lambda **kwargs: kwargs["tool"],
    )

    results = run_tools_parallel(
        tools_to_run=["ruff", "typos"],
        paths=[str(tree)],
        action=Action.FIX,
        config_manager=cast(UnifiedConfigManager, object()),
        tool_option_dict={},
        exclude=None,
        include_venv=False,
        selected_tools={"ruff", "typos"},
        max_workers=4,
    )

    assert_that([result.name for result in results]).contains("ruff", "typos")


def test_the_mutation_phase_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A second mutation run over the same tree changes no file.

    Args:
        monkeypatch: pytest monkeypatch fixture.
        tmp_path: pytest temporary directory.
    """
    target = tmp_path / "module.py"
    target.write_text("beta\nalpha\n", encoding="utf-8")
    live: list[str] = []

    _run_mutation_phase(
        monkeypatch=monkeypatch,
        tree=tmp_path,
        target=target,
        live=live,
    )
    after_first = _digest(target)
    _run_mutation_phase(
        monkeypatch=monkeypatch,
        tree=tmp_path,
        target=target,
        live=live,
    )

    assert_that(_digest(target)).is_equal_to(after_first)
    # Both rewrites survived: sorting then upper-casing, not one of the two.
    assert_that(target.read_text(encoding="utf-8")).is_equal_to("ALPHA\nBETA\n")
    # And they survived because the scheduler never had them in flight at once.
    assert_that(live).does_not_contain("overlapped")
