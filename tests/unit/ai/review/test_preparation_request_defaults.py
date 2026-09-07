"""Request-level defaults for shared review preparation (#2300).

``ReviewRunRequest`` leaves the review shape unset by default and
:func:`~lintro.ai.review.preparation.prepare_review` fills each unset field
from the workspace's ``review:`` section. ``custom_agent_mode`` follows that
same None-means-config rule, so a caller that omits it gets the configured
mode rather than silently reviewing with the built-in checklist alone.
"""

from __future__ import annotations

from pathlib import Path

from assertpy import assert_that

from lintro.ai.review.enums.custom_agent_mode import CustomAgentMode
from lintro.ai.review.preparation import (
    ReviewRunRequest,
    resolve_custom_agent_mode,
    resolve_review_depth,
    resolve_review_strictness,
)
from lintro.config.lintro_config import LintroConfig


def _request(*, mode: CustomAgentMode | None) -> ReviewRunRequest:
    """Build a request over a default config with the given agent mode.

    Args:
        mode: The requested custom-agent mode, or None to leave it unset.

    Returns:
        ReviewRunRequest: A request anchored to a placeholder workspace.
    """
    return ReviewRunRequest(
        workspace_root=Path("/workspace"),
        lintro_config=LintroConfig(),
        custom_agent_mode=mode,
    )


def test_custom_agent_mode_is_unset_by_default() -> None:
    """An omitted ``custom_agent_mode`` means "not requested", not disabled.

    Built without the field so the assertion observes the dataclass default
    itself rather than a ``None`` the helper passed in (#2377 review).
    """
    request = ReviewRunRequest(
        workspace_root=Path("/workspace"),
        lintro_config=LintroConfig(),
    )

    assert_that(request.custom_agent_mode).is_none()


def test_unset_custom_agent_mode_resolves_from_config() -> None:
    """A request that omits the mode runs the configured one.

    The dataclass used to default to ``DISABLED`` while ``review.custom_agents``
    defaults to ``ENABLED``, so any caller that omitted the field silently ran
    the built-in checklist only.
    """
    resolved = resolve_custom_agent_mode(_request(mode=None))

    assert_that(resolved).is_equal_to(LintroConfig().review.custom_agents)
    assert_that(resolved).is_equal_to(CustomAgentMode.ENABLED)


def test_an_explicit_custom_agent_mode_wins_over_config() -> None:
    """A caller that wants the built-in checklist only still says so."""
    request = _request(mode=CustomAgentMode.DISABLED)

    assert_that(resolve_custom_agent_mode(request)).is_equal_to(
        CustomAgentMode.DISABLED,
    )


def test_depth_and_strictness_follow_the_same_rule() -> None:
    """``custom_agent_mode`` resolves the way the other unset fields do."""
    request = _request(mode=None)
    config = LintroConfig()

    assert_that(resolve_review_depth(request)).is_equal_to(config.review.depth)
    assert_that(resolve_review_strictness(request).value).is_equal_to(
        config.review.strictness.value,
    )
