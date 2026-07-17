"""Command chaining orchestration for Lintro CLI.

This module provides the CommandChainer class that handles parsing and
execution of comma-separated command chains in the Lintro CLI.

Example:
    lintro fmt . , chk . , tst
    This chains format, check, and test commands sequentially.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

import click

from lintro.utils.lazy_logger import logger

if TYPE_CHECKING:
    pass


class CommandChainer:
    """Orchestrates execution of multiple CLI commands in sequence.

    This class extracts command chaining logic from the main CLI group,
    making it easier to test and maintain. It handles:

    - Detection of command chains (comma-separated commands)
    - Normalization of arguments (splitting joined commands)
    - Grouping of commands with their arguments
    - Sequential execution with proper error handling
    """

    def __init__(self, group: click.Group, separator: str = ",") -> None:
        """Initialize the command chainer.

        Args:
            group: The Click group containing available commands.
            separator: The character used to separate commands (default: ",").

        Attributes:
            group: The Click group containing available commands.
            separator: The character used to separate commands.
        """
        self.group = group
        self.separator = separator
        self._command_names: set[str] | None = None

    @property
    def command_names(self) -> set[str]:
        """Get available command names lazily.

        Returns:
            Set of command names and aliases available in the group.
        """
        if self._command_names is None:
            ctx = click.Context(self.group)
            self._command_names = set(self.group.list_commands(ctx))
        return self._command_names

    def should_chain(self, args: Sequence[str]) -> bool:
        """Check if arguments contain command chaining.

        Args:
            args: Command line arguments to check.

        Returns:
            True if the arguments contain comma separators indicating chaining.
        """
        for arg in args:
            if arg == self.separator:
                return True
            if self.separator in arg:
                # Check if splitting by comma yields known commands
                parts = [p.strip() for p in arg.split(self.separator) if p.strip()]
                if parts and all(p in self.command_names for p in parts):
                    return True
        return False

    def normalize_args(self, args: Sequence[str]) -> list[str]:
        """Normalize comma-adjacent args into separate tokens.

        Handles cases like:
        - "fmt,chk" -> ["fmt", ",", "chk"]
        - "fmt , chk" -> ["fmt", ",", "chk"]
        - "--tools ruff,bandit" -> ["--tools", "ruff,bandit"] (preserved)

        Args:
            args: Raw command line arguments.

        Returns:
            Normalized list of arguments with separators as distinct tokens.
        """
        normalized: list[str] = []

        for arg in args:
            if arg == self.separator:
                normalized.append(arg)
                continue

            if self.separator in arg:
                # Check if this looks like comma-separated commands
                raw_parts = [part.strip() for part in arg.split(self.separator)]
                fragments = [part for part in raw_parts if part]

                # Only split if all parts are known commands
                if fragments and all(part in self.command_names for part in fragments):
                    for idx, part in enumerate(fragments):
                        if part:
                            normalized.append(part)
                        if idx < len(fragments) - 1:
                            normalized.append(self.separator)
                    continue

            # Not comma-separated commands, keep as-is
            normalized.append(arg)

        return normalized

    def group_commands(self, args: list[str]) -> list[list[str]]:
        """Split arguments into command groups at separators.

        Args:
            args: Normalized arguments with separators as distinct tokens.

        Returns:
            List of command groups, where each group is a command with its args.
        """
        command_groups: list[list[str]] = []
        current_group: list[str] = []

        for arg in args:
            if arg == self.separator:
                if current_group:
                    command_groups.append(current_group)
                    current_group = []
                continue
            current_group.append(arg)

        if current_group:
            command_groups.append(current_group)

        return command_groups

    def execute_chain(
        self,
        ctx: click.Context,
        command_groups: list[list[str]],
    ) -> int:
        """Execute command groups sequentially, return max exit code.

        Args:
            ctx: The parent Click context.
            command_groups: List of command groups to execute.

        Returns:
            The maximum exit code from all commands (0 if all succeeded).
        """
        exit_codes: list[int] = []

        for cmd_args in command_groups:
            if not cmd_args:
                continue

            exit_code = self._execute_single_command(ctx, cmd_args)
            exit_codes.append(exit_code)

        return max(exit_codes) if exit_codes else 0

    def _execute_single_command(
        self,
        parent_ctx: click.Context,
        cmd_args: list[str],
    ) -> int:
        """Execute a single command with its arguments.

        Args:
            parent_ctx: The parent Click context.
            cmd_args: Command name followed by its arguments.

        Returns:
            Exit code from the command execution.

        Raises:
            KeyboardInterrupt: Re-raised to allow normal user interruption.
        """
        try:
            # Create a new context for this command
            ctx_copy = self.group.make_context(
                parent_ctx.info_name,
                cmd_args,
                parent=parent_ctx,
                allow_extra_args=True,
                allow_interspersed_args=False,
            )

            # Invoke the command
            with ctx_copy.scope() as subctx:
                result = self.group.invoke(subctx)
                return result if isinstance(result, int) else 0

        except SystemExit as e:
            return e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
        except KeyboardInterrupt:
            # Re-raise KeyboardInterrupt to allow normal interruption
            raise
        except Exception as e:  # noqa: BLE001 - intentional: allow chain to continue
            # Catch all other exceptions to allow command chain to continue
            exit_code = getattr(e, "exit_code", 1)
            logger.exception(
                f"Error executing command '{' '.join(cmd_args)}': "
                f"{type(e).__name__}: {e}",
            )
            click.echo(
                click.style(
                    f"Error executing command '{' '.join(cmd_args)}': "
                    f"{type(e).__name__}: {e}",
                    fg="red",
                ),
                err=True,
            )
            return exit_code
