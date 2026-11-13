"""Child process for the ``lintro review`` post-tail signal tests (#3426).

Run as ``python review_post_tail_child.py <scenario> <marker>``. It drives the
real ``_render_post_and_exit`` and the real JSON ``_emit_output`` over a
scripted poster, so a SIGTERM the parent sends can only kill this process,
never the pytest worker. The scenario's progress is written to ``marker`` so
the parent knows when to signal.

Scenarios:

- ``post-sigterm``: the post blocks until the parent signals it, then
  rendering blocks long enough for the parent to signal again.
- ``render-sigterm``: the post succeeds, then rendering blocks long enough
  for the parent to signal it.
- ``post-timeout``: the post raises a raw ``TimeoutError``.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from lintro.ai.config import AIConfig
from lintro.ai.review import github as review_github
from lintro.ai.review.models.review_finding import ReviewFinding, Severity
from lintro.ai.review.models.review_metadata import ReviewMetadata
from lintro.ai.review.models.review_result import ReviewResult
from lintro.ai.review.models.review_state import ReviewState
from lintro.ai.review.output import (
    render_review_output as real_render_review_output,
)
from lintro.cli_utils.commands import review as review_command
from lintro.enums.confidence_level import ConfidenceLevel

#: How long a blocked step waits for the parent's signal before giving up.
_BLOCK_SECONDS = 20.0
#: How long rendering keeps going after the parent signalled it.
_RENDER_SECONDS = 1.0


def _finding(*, title: str, confidence: str) -> ReviewFinding:
    """Build a P2 finding on a diff line.

    Args:
        title: Finding title, used by the parent to read the JSON.
        confidence: Confidence the posting policy gates on.

    Returns:
        The constructed finding.
    """
    return ReviewFinding(
        severity=Severity.P2,
        category="logic-bug",
        file="src/app.py",
        line=12,
        title=title,
        description="The branch is never taken.",
        cause="Off by one.",
        fix="Compare with >=.",
        confidence=confidence,
    )


def _result() -> ReviewResult:
    """Build a completed review with one thread and one note.

    Returns:
        The review result the run completed with.
    """
    return ReviewResult(
        metadata=ReviewMetadata(
            model="m",
            provider="p",
            context_window=1000,
            depth=1,
            chunks_total=1,
            chunks_current=1,
            files_reviewed=1,
            files_total=1,
            checklist_items=0,
        ),
        summary="s",
        findings=(
            _finding(title="Thread", confidence="high"),
            _finding(title="Note", confidence="medium"),
        ),
    )


def _wait_for_signal(*, marker: Path, stage: str, seconds: float) -> None:
    """Announce *stage* to the parent, then sleep through its signals.

    ``time.sleep`` resumes after a handler that returns (PEP 475), so a
    deferred SIGTERM leaves the sleep running; one the handler turns into
    an exception ends it.

    Args:
        marker: File the parent polls.
        stage: Stage name to write.
        seconds: How long to sleep.
    """
    marker.write_text(stage, encoding="utf-8")
    time.sleep(seconds)


def _install(*, scenario: str, marker: Path) -> None:
    """Script the poster and the renderer for *scenario*.

    Args:
        scenario: One of the module's scenarios.
        marker: File the parent polls.
    """
    # Never undone: the patches live exactly as long as this child process.
    patch = pytest.MonkeyPatch()
    patch.setattr(
        review_command,
        "validate_result_suggested_patches",
        lambda *, result, context: result,
    )
    patch.setattr(review_command, "build_prompt_question_map", lambda *, items: {})
    patch.setattr(review_command, "_execute_advisory", lambda **kwargs: [])
    patch.setattr(review_command, "persist_review_state", lambda **kwargs: None)

    def post_review(**kwargs: Any) -> bool:
        """Post per the scenario, filling the delivery sink as GitHub would.

        Args:
            **kwargs: The poster's keyword arguments.

        Returns:
            bool: True once the inline review was accepted.

        Raises:
            TimeoutError: In the ``post-timeout`` scenario.
        """
        delivery = kwargs["options"].inline_delivery
        delivery.attempted = True
        if scenario == "post-sigterm":
            _wait_for_signal(marker=marker, stage="posting", seconds=_BLOCK_SECONDS)
        if scenario == "post-timeout":
            raise TimeoutError("stub getresponse() timeout")
        delivery.delivered.extend(
            f for f in kwargs["result"].findings if f.posted_inline
        )
        return True

    patch.setattr(review_github, "post_review_to_github", post_review)

    def render_review_output(**kwargs: Any) -> str | None:
        """Render for real, after blocking in the signal scenarios.

        Args:
            **kwargs: The renderer's keyword arguments.

        Returns:
            str | None: The real renderer's output.
        """
        if scenario in {"post-sigterm", "render-sigterm"}:
            _wait_for_signal(marker=marker, stage="rendering", seconds=_RENDER_SECONDS)
        return real_render_review_output(**kwargs)

    patch.setattr(review_command, "render_review_output", render_review_output)


def main(*, scenario: str, marker: Path) -> None:
    """Run the post tail once for *scenario*.

    Args:
        scenario: One of the module's scenarios.
        marker: File the parent polls.
    """
    _install(scenario=scenario, marker=marker)
    options = SimpleNamespace(
        show_checklist=None,
        advisory_tools=(),
        tool_options=None,
        post=True,
        fail_on_findings=False,
        output_format="json",
        depth=None,
        strictness=None,
        transport=None,
        provider_override=None,
        model_override=None,
        review_override=None,
        max_cost_usd_override=None,
        provider_options=(),
        timeout=None,
        context_window=None,
        semantic_chunks=False,
        path_filter=(),
    )
    review_command._render_post_and_exit(
        options=cast(Any, options),
        lintro_config=cast(
            Any,
            SimpleNamespace(
                config_path=None,
                review=SimpleNamespace(checklist_display=None, auto_resolve=True),
            ),
        ),
        prepared=cast(
            Any,
            SimpleNamespace(
                ai_config=AIConfig(
                    review_inline_min_confidence=ConfidenceLevel.HIGH,
                ),
                context=SimpleNamespace(changed_files=(), head_ref="deadbeef"),
                checklist_items=(),
                workspace_root=".",
            ),
        ),
        result=_result(),
        prior_state=ReviewState(),
        targets=cast(
            Any,
            SimpleNamespace(effective_repo="o/r", resolved_pr=7, state_pr=7),
        ),
        resolved_profile=cast(
            Any,
            SimpleNamespace(
                transport=SimpleNamespace(value="api"),
                auth_mode="token",
            ),
        ),
    )


if __name__ == "__main__":
    main(scenario=sys.argv[1], marker=Path(sys.argv[2]))
