"""Tests for BaseToolPlugin.copy_for_execution() — issue #1065 thread safety.

These tests verify that option handling is safe under parallel/thread
execution: each logical invocation configures and runs its own private copy
of a registry singleton, so concurrent runs with different options never
clobber one another's option state.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from assertpy import assert_that

from lintro.models.core.tool_result import ToolResult
from lintro.plugins.base import BaseToolPlugin
from lintro.plugins.protocol import ToolDefinition

if TYPE_CHECKING:
    from tests.unit.plugins.conftest import FakeToolPlugin


@dataclass
class _ObservingPlugin(BaseToolPlugin):
    """Plugin whose check() records the options it observes on self.

    The check() implementation deliberately reads its option marker, yields
    the GIL via a short sleep, then reads it again. If two concurrent
    invocations shared one instance, the second read could observe the
    other invocation's marker.
    """

    _definition: ToolDefinition = field(
        default_factory=lambda: ToolDefinition(
            name="observing-tool",
            description="Observes options during check",
            file_patterns=["*.py"],
            can_fix=True,
            default_timeout=30,
        ),
    )

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool definition.

        Returns:
            The tool definition.
        """
        return self._definition

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Read the marker option before and after yielding the GIL.

        Args:
            paths: Unused input paths.
            options: Runtime options merged into self.options.

        Returns:
            ToolResult whose output is the observed marker, and issues_count
            is 1 if the marker changed mid-call (a clobber), else 0.
        """
        if options:
            self.options.update(options)
        before = self.options.get("marker")
        time.sleep(0.02)
        after = self.options.get("marker")
        clobbered = 0 if before == after else 1
        return ToolResult(
            name=self.definition.name,
            success=clobbered == 0,
            output=str(after),
            issues_count=clobbered,
        )


def test_copy_for_execution_isolates_option_state() -> None:
    """Mutating a per-invocation copy must not affect the template."""
    template = _ObservingPlugin()
    template.set_options(marker="template", exclude_patterns=["template_*"])

    clone = template.copy_for_execution()
    clone.set_options(marker="clone", exclude_patterns=["clone_*"])

    assert_that(clone.options["marker"]).is_equal_to("clone")
    assert_that(template.options["marker"]).is_equal_to("template")
    assert_that(clone.exclude_patterns).contains("clone_*")
    assert_that(template.exclude_patterns).does_not_contain("clone_*")
    assert_that(clone.options).is_not_same_as(template.options)
    assert_that(clone.exclude_patterns).is_not_same_as(template.exclude_patterns)


def test_concurrent_invocations_do_not_clobber_options() -> None:
    """Two concurrent runs with different options each see only their own.

    Simulates the executor pattern: a shared singleton template is copied
    per invocation, each copy is configured with distinct options, and both
    run check() concurrently in a real ThreadPoolExecutor.
    """
    template = _ObservingPlugin()

    def run(marker: str) -> ToolResult:
        tool = template.copy_for_execution()
        tool.set_options(marker=marker)
        return tool.check(paths=["x.py"], options={})

    markers = [f"marker-{i}" for i in range(8)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(run, markers))

    # No invocation observed a clobbered (interleaved) marker.
    assert_that([r.issues_count for r in results]).does_not_contain(1)
    # Each invocation observed exactly its own marker.
    assert_that(sorted(r.output for r in results)).is_equal_to(sorted(markers))


def test_copy_for_execution_shares_readonly_caches() -> None:
    """Non-option attributes are shallow-copied (shared) for efficiency."""
    from lintro.tools.definitions.ruff import RuffPlugin

    template = RuffPlugin()
    clone = template.copy_for_execution()

    # The rule-name cache is a read-mostly cache and remains shared.
    assert_that(clone._rule_name_cache).is_same_as(template._rule_name_cache)
    # Option state is independent, however.
    assert_that(clone.options).is_not_same_as(template.options)


def test_reset_options_still_works_on_copy(
    fake_tool_plugin: FakeToolPlugin,
) -> None:
    """A copy supports the existing sequential set/reset contract."""
    clone = fake_tool_plugin.copy_for_execution()
    clone.set_options(exclude_patterns=["temp_*"])
    assert_that(clone.exclude_patterns).contains("temp_*")

    clone.reset_options()
    assert_that(clone.exclude_patterns).does_not_contain("temp_*")
    # Template untouched throughout.
    assert_that(fake_tool_plugin.exclude_patterns).does_not_contain("temp_*")
