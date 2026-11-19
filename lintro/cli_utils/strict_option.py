"""Shared ``--strict-missing-tools`` option for ``lintro chk`` and ``lintro fmt``."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

import click

_F = TypeVar("_F", bound=Callable[..., object])

_HELP: str = (
    "Fail the run (exit 1) when a selected tool is not installed or is below "
    "lintro's minimum version, instead of skipping it. The failing row names "
    "the tool, the reason and an install hint. Tools skipped because no files "
    "matched, or disabled in config, never fail. Overrides "
    "execution.strict_missing_tools for this run."
)


def strict_missing_tools_option(func: _F) -> _F:
    """Attach ``--strict-missing-tools/--no-strict-missing-tools`` to a command.

    The default is ``None`` so an absent flag leaves the configured
    ``execution.strict_missing_tools`` value in charge.

    Args:
        func: Click command callback to decorate.

    Returns:
        The decorated callback, receiving ``strict_missing_tools``.
    """
    decorator = click.option(
        "--strict-missing-tools/--no-strict-missing-tools",
        "strict_missing_tools",
        default=None,
        help=_HELP,
    )
    return decorator(func)
