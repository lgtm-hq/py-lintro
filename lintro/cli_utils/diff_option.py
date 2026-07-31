"""Shared helpers for the ``--diff`` CLI option."""

from __future__ import annotations

import os

import click

from lintro.utils.git_diff import DIFF_DEFAULT_SENTINEL

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
    looks_like_path = (
        os.path.isabs(diff_base)
        or diff_base in {".", ".."}
        or diff_base.startswith(("./", "../"))
        or os.path.sep in diff_base
        or (os.path.altsep is not None and os.path.altsep in diff_base)
    )
    if looks_like_path and os.path.exists(diff_base):
        raise click.UsageError(_DIFF_PATH_ERROR.format(value=diff_base))
    return diff_base
