"""Shared helpers for the ``--diff`` CLI option."""

from __future__ import annotations

import os

import click

from lintro.utils.git_diff import DIFF_DEFAULT_SENTINEL, ref_exists

_DIFF_PATH_ERROR: str = (
    "--diff value '{value}' looks like a filesystem path, not a git ref. "
    "Use --diff=<ref> for an explicit base ref (for example --diff=main), "
    "or place scan paths after '--' (for example lintro chk --diff -- path/)."
)


def validate_diff_base_ref(diff_base: str | None) -> str | None:
    """Reject ``--diff`` values that name existing filesystem paths.

    Args:
        diff_base: Parsed ``--diff`` option value.

    Returns:
        The same value when it is valid.

    Raises:
        click.UsageError: When the value names an existing path.
    """
    if diff_base is None or diff_base == DIFF_DEFAULT_SENTINEL:
        return diff_base
    if os.path.exists(diff_base) and not ref_exists(diff_base):
        raise click.UsageError(_DIFF_PATH_ERROR.format(value=diff_base))
    return diff_base
