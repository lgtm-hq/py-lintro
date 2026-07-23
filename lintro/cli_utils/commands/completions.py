"""Shell completion command implementation for lintro CLI."""

from __future__ import annotations

from typing import Final

import click
from click.shell_completion import shell_complete

_COMPLETION_VAR: Final = "_LINTRO_COMPLETE"
_COMPLETION_INSTRUCTIONS: Final = {
    "bash": "bash_source",
    "zsh": "zsh_source",
    "fish": "fish_source",
}


@click.command("completions")
@click.argument(
    "shell",
    type=click.Choice(tuple(_COMPLETION_INSTRUCTIONS), case_sensitive=True),
)
def completions_command(shell: str) -> None:
    """Print a shell completion script for bash, zsh, or fish.

    \u000c

    Args:
        shell: The shell completion script to generate.
    """
    from lintro.cli import cli

    shell_complete(
        cli=cli,
        ctx_args={},
        prog_name="lintro",
        complete_var=_COMPLETION_VAR,
        instruction=_COMPLETION_INSTRUCTIONS[shell],
    )
