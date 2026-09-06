"""Characterization tests for the review session options object (#2301).

``run_review`` is the public facade and keeps its keyword signature; the values
it does not receive come from its own defaults, while every layer below reads
:class:`ReviewSessionOptions`. Until the final slice of #2301 collapses the two
onto one surface, the defaults live in two places, so they are pinned against
each other here: a default that drifts on one side silently changes what a
caller who omits the keyword gets.
"""

from __future__ import annotations

import dataclasses
import inspect

from assertpy import assert_that

from lintro.ai.review.orchestrator import run_review
from lintro.ai.review.session import ReviewSessionOptions


def _run_review_keyword_defaults() -> dict[str, object]:
    """Collect the defaulted keyword parameters of ``run_review``.

    Returns:
        Mapping of parameter name to default value, for every keyword-only
        parameter of ``run_review`` that has a default.
    """
    return {
        name: parameter.default
        for name, parameter in inspect.signature(run_review).parameters.items()
        if parameter.kind is inspect.Parameter.KEYWORD_ONLY
        and parameter.default is not inspect.Parameter.empty
    }


def _session_option_defaults() -> dict[str, object]:
    """Collect the defaulted fields of ``ReviewSessionOptions``.

    Returns:
        Mapping of field name to default value, for every field of
        ``ReviewSessionOptions`` that has a plain (non-factory) default.
    """
    return {
        field.name: field.default
        for field in dataclasses.fields(ReviewSessionOptions)
        if field.default is not dataclasses.MISSING
    }


def test_every_defaulted_run_review_keyword_is_a_session_option() -> None:
    """No defaulted facade keyword is missing from the options object."""
    missing = sorted(
        set(_run_review_keyword_defaults()) - set(_session_option_defaults()),
    )

    assert_that(missing).is_empty()


def test_run_review_keyword_defaults_equal_session_option_defaults() -> None:
    """The facade and the options object agree on every shared default."""
    facade = _run_review_keyword_defaults()
    options = _session_option_defaults()

    shared = sorted(set(facade) & set(options))
    mismatched = [name for name in shared if facade[name] != options[name]]

    assert_that(shared).is_not_empty()
    assert_that(mismatched).is_empty()
