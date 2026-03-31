"""Command builder registry for language-specific tool execution.

This module provides a registry pattern for determining how to invoke
external tools based on their runtime environment (Python, Node.js, Cargo, etc.).

The registry pattern:
- Satisfies ISP (BaseToolPlugin doesn't know about any language)
- Satisfies OCP (add new languages without modifying existing code)
- Provides extensibility for future languages (Go, Ruby, etc.)

Example:
    # Register a new language builder
    @register_command_builder
    class GoBuilder(CommandBuilder):
        def can_handle(self, tool_name_enum: ToolName | None) -> bool:
            return tool_name_enum in {ToolName.GOLINT, ToolName.STATICCHECK}

        def get_command(
            self,
            tool_name: str,
            tool_name_enum: ToolName | None,
        ) -> list[str]:
            return [tool_name]
"""

from __future__ import annotations

import shutil
import sys
import sysconfig
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar

from loguru import logger

from lintro.plugins.subprocess_executor import is_compiled_binary

if TYPE_CHECKING:
    from lintro.enums.tool_name import ToolName


class CommandBuilder(ABC):
    """Abstract base for language-specific command builders.

    Subclasses implement language-specific logic for determining
    how to invoke tools (e.g., via Python module, npx, cargo).
    """

    @abstractmethod
    def can_handle(self, tool_name_enum: ToolName | None) -> bool:
        """Check if this builder can handle the given tool.

        Args:
            tool_name_enum: Tool name enum, or None if unknown.

        Returns:
            True if this builder should handle the tool.
        """
        ...

    @abstractmethod
    def get_command(
        self,
        tool_name: str,
        tool_name_enum: ToolName | None,
    ) -> list[str]:
        """Get the command to execute the tool.

        Args:
            tool_name: String name of the tool.
            tool_name_enum: Tool name enum, or None if unknown.

        Returns:
            Command list to execute the tool.
        """
        ...


class CommandBuilderRegistry:
    """Registry for command builders.

    Builders are checked in registration order. First builder that
    can_handle() the tool wins.

    This is a class-level registry that accumulates builders as they
    are registered via the @register_command_builder decorator.
    """

    _builders: list[CommandBuilder] = []

    @classmethod
    def register(cls, builder: CommandBuilder) -> None:
        """Register a command builder.

        Args:
            builder: The command builder instance to register.
        """
        cls._builders.append(builder)

    @classmethod
    def get_command(
        cls,
        tool_name: str,
        tool_name_enum: ToolName | None,
    ) -> list[str]:
        """Get command for a tool using registered builders.

        Iterates through registered builders in order, returning the
        command from the first builder that can handle the tool.

        Args:
            tool_name: String name of the tool.
            tool_name_enum: Tool name enum, or None if unknown.

        Returns:
            Command list, or [tool_name] as fallback.
        """
        for builder in cls._builders:
            if builder.can_handle(tool_name_enum):
                return builder.get_command(tool_name, tool_name_enum)

        # Fallback: just use the tool name directly
        return [tool_name]

    @classmethod
    def clear(cls) -> None:
        """Clear all registered builders (for testing)."""
        cls._builders = []

    @classmethod
    def is_registered(cls, tool_name_enum: ToolName | None) -> bool:
        """Check if any builder can handle the given tool.

        Args:
            tool_name_enum: Tool name enum to check.

        Returns:
            True if a builder exists for this tool.
        """
        return any(b.can_handle(tool_name_enum) for b in cls._builders)


def register_command_builder(cls: type[CommandBuilder]) -> type[CommandBuilder]:
    """Decorator to register a command builder.

    Args:
        cls: The CommandBuilder subclass to register.

    Returns:
        The same class, unmodified.
    """
    CommandBuilderRegistry.register(cls())
    return cls


# -----------------------------------------------------------------------------
# Helper Functions
# -----------------------------------------------------------------------------


def _is_compiled_binary() -> bool:
    """Detect if running as a Nuitka-compiled binary.

    When compiled with Nuitka, sys.executable points to the lintro binary
    itself, not a Python interpreter.

    Returns:
        True if running as a compiled binary, False otherwise.
    """
    return is_compiled_binary()


def _resolve_venv_tool_command(tool_name: str) -> list[str] | None:
    """Resolve a Python tool command when running inside a virtualenv.

    Checks if the tool exists in the venv's scripts directory (via sysconfig)
    and returns the appropriate command. Used by both PythonBundledBuilder
    and PytestBuilder to avoid duplicated venv detection logic.

    Args:
        tool_name: Name of the tool binary (e.g., "ruff", "pytest").

    Returns:
        Command list if in a venv and resolved, None if not in a venv.
    """
    if sys.prefix == sys.base_prefix:
        return None  # Not in a venv

    scripts_dir = sysconfig.get_path("scripts")
    venv_tool = shutil.which(tool_name, path=scripts_dir) if scripts_dir else None
    if venv_tool:
        python_exe = sys.executable
        if python_exe:
            logger.debug(
                f"Running in venv ({sys.prefix}), "
                f"{tool_name} found in venv scripts, "
                f"using python -m {tool_name}",
            )
            return [python_exe, "-m", tool_name]

    # Tool not in venv — try PATH (e.g., separate Homebrew formula)
    tool_path = shutil.which(tool_name)
    if tool_path:
        logger.debug(
            f"Running in venv ({sys.prefix}), "
            f"{tool_name} not in venv scripts, "
            f"found in PATH: {tool_path}",
        )
        return [tool_path]

    # Last resort: try python -m anyway
    python_exe = sys.executable
    if python_exe:
        logger.debug(
            f"Running in venv ({sys.prefix}), "
            f"{tool_name} not in venv or PATH, "
            f"falling back to python -m {tool_name}",
        )
        return [python_exe, "-m", tool_name]

    return [tool_name]


# -----------------------------------------------------------------------------
# Built-in Builders
# -----------------------------------------------------------------------------


@register_command_builder
class PythonBundledBuilder(CommandBuilder):
    """Builder for Python tools bundled with Lintro.

    Handles: ruff, black, bandit, yamllint, mypy.

    Prefers PATH-based discovery to support various installation methods
    (Homebrew, system packages, pipx, uv tool). Falls back to Python module
    execution for pip installs where the binary isn't in PATH.
    """

    _tools: frozenset[ToolName] | None = None

    @property
    def tools(self) -> frozenset[ToolName]:
        """Get the set of tools this builder handles.

        Returns:
            Frozen set of ToolName enums for Python bundled tools.
        """
        if self._tools is None:
            from lintro.enums.tool_name import ToolName

            self._tools = frozenset(
                {
                    ToolName.RUFF,
                    ToolName.BLACK,
                    ToolName.BANDIT,
                    ToolName.YAMLLINT,
                    ToolName.MYPY,
                },
            )
        return self._tools

    def can_handle(self, tool_name_enum: ToolName | None) -> bool:
        """Check if this builder handles the tool.

        Args:
            tool_name_enum: Tool name enum to check.

        Returns:
            True if tool is a Python bundled tool.
        """
        return tool_name_enum in self.tools

    def get_command(
        self,
        tool_name: str,
        tool_name_enum: ToolName | None,
    ) -> list[str]:
        """Get command for Python bundled tool.

        When running in a virtual environment, always uses python -m to ensure
        the tool runs with the same packages as lintro. Otherwise, prefers
        PATH binary (works with Homebrew, system packages, pipx, uv tool, etc.).

        Args:
            tool_name: String name of the tool.
            tool_name_enum: Tool name enum.

        Returns:
            Command list to execute the tool.
        """
        # Skip python -m fallback when compiled (sys.executable is the lintro binary)
        if _is_compiled_binary():
            tool_path = shutil.which(tool_name)
            if tool_path:
                logger.debug(f"Found {tool_name} in PATH: {tool_path}")
                return [tool_path]
            logger.debug(
                f"Tool {tool_name} not in PATH and running as compiled binary, "
                "skipping python -m fallback",
            )
            return [tool_name]

        # When running in a venv, resolve using shared helper
        venv_cmd = _resolve_venv_tool_command(tool_name)
        if venv_cmd is not None:
            return venv_cmd

        # Outside venv: prefer PATH binary (Homebrew, apt, pipx, etc.)
        tool_path = shutil.which(tool_name)
        if tool_path:
            logger.debug(f"Found {tool_name} in PATH: {tool_path}")
            return [tool_path]

        # Fallback to python -m for pip installs where binary isn't in PATH
        python_exe = sys.executable
        if python_exe:
            logger.debug(f"Tool {tool_name} not in PATH, using python -m")
            return [python_exe, "-m", tool_name]
        return [tool_name]


@register_command_builder
class PytestBuilder(CommandBuilder):
    """Builder for pytest (special case of Python tool).

    Pytest is handled separately because it uses a different module
    invocation pattern. Prefers PATH-based discovery like PythonBundledBuilder.
    """

    def can_handle(self, tool_name_enum: ToolName | None) -> bool:
        """Check if this builder handles pytest.

        Args:
            tool_name_enum: Tool name enum to check.

        Returns:
            True if tool is pytest.
        """
        from lintro.enums.tool_name import ToolName

        return tool_name_enum == ToolName.PYTEST

    def get_command(
        self,
        tool_name: str,
        tool_name_enum: ToolName | None,
    ) -> list[str]:
        """Get command for pytest.

        When running in a virtual environment, always uses python -m pytest to
        ensure pytest runs with the same packages as lintro. Otherwise, prefers
        PATH binary (works with Homebrew, system packages, pipx, uv tool, etc.).

        Args:
            tool_name: String name of the tool.
            tool_name_enum: Tool name enum.

        Returns:
            Command list to execute pytest.
        """
        # Skip python -m fallback when compiled (sys.executable is the lintro binary)
        if _is_compiled_binary():
            tool_path = shutil.which("pytest")
            if tool_path:
                logger.debug(f"Found pytest in PATH: {tool_path}")
                return [tool_path]
            logger.debug(
                "pytest not in PATH and running as compiled binary, "
                "skipping python -m fallback",
            )
            return ["pytest"]

        # When running in a venv, resolve using shared helper
        venv_cmd = _resolve_venv_tool_command("pytest")
        if venv_cmd is not None:
            return venv_cmd

        # Outside venv: prefer PATH binary (Homebrew, apt, pipx, etc.)
        tool_path = shutil.which("pytest")
        if tool_path:
            logger.debug(f"Found pytest in PATH: {tool_path}")
            return [tool_path]

        # Fallback to python -m for pip installs where binary isn't in PATH
        python_exe = sys.executable
        if python_exe:
            logger.debug("pytest not in PATH, using python -m pytest")
            return [python_exe, "-m", "pytest"]
        return ["pytest"]


@register_command_builder
class NodeJSBuilder(CommandBuilder):
    """Builder for Node.js tools (Astro, Markdownlint, TypeScript, Vue-tsc).

    Uses bunx to run Node.js tools when available, falling back to
    direct tool invocation if bunx is not found.
    """

    _package_names: dict[ToolName, str] | None = None
    _binary_names: dict[ToolName, str] | None = None

    @property
    def package_names(self) -> dict[ToolName, str]:
        """Get mapping of tools to npm package names.

        Returns:
            Dictionary mapping ToolName to npm package name.
        """
        if self._package_names is None:
            from lintro.enums.tool_name import ToolName

            self._package_names = {
                ToolName.ASTRO_CHECK: "astro",
                ToolName.MARKDOWNLINT: "markdownlint-cli2",
                ToolName.OXFMT: "oxfmt",
                ToolName.OXLINT: "oxlint",
                ToolName.SVELTE_CHECK: "svelte-check",
                ToolName.TSC: "typescript",
                ToolName.VUE_TSC: "vue-tsc",
            }
        return self._package_names

    @property
    def binary_names(self) -> dict[ToolName, str]:
        """Get mapping of tools to executable binary names.

        For most tools, the binary name matches the package name.
        This mapping is only needed when they differ (e.g., typescript -> tsc).

        Returns:
            Dictionary mapping ToolName to binary name.
        """
        if self._binary_names is None:
            from lintro.enums.tool_name import ToolName

            self._binary_names = {
                ToolName.TSC: "tsc",  # Package is "typescript", binary is "tsc"
            }
        return self._binary_names

    def can_handle(self, tool_name_enum: ToolName | None) -> bool:
        """Check if this builder handles the tool.

        Args:
            tool_name_enum: Tool name enum to check.

        Returns:
            True if tool is a Node.js tool.
        """
        return tool_name_enum in self.package_names

    def get_command(
        self,
        tool_name: str,
        tool_name_enum: ToolName | None,
    ) -> list[str]:
        """Get command for Node.js tool.

        Args:
            tool_name: String name of the tool.
            tool_name_enum: Tool name enum.

        Returns:
            Command list to execute the tool via bunx or directly.
        """
        if tool_name_enum is None:
            return [tool_name]

        # Get binary name (falls back to package name if not specified)
        binary_name = self.binary_names.get(
            tool_name_enum,
            self.package_names.get(tool_name_enum, tool_name),
        )

        # Prefer bunx (bun), fall back to npx (npm), then direct tool invocation
        if shutil.which("bunx"):
            return ["bunx", binary_name]
        if shutil.which("npx"):
            return ["npx", binary_name]
        return [binary_name]


@register_command_builder
class CargoBuilder(CommandBuilder):
    """Builder for Cargo/Rust tools (Clippy, cargo-audit, cargo-deny).

    Invokes Rust tools via cargo subcommands.
    """

    def can_handle(self, tool_name_enum: ToolName | None) -> bool:
        """Check if this builder handles the tool.

        Args:
            tool_name_enum: Tool name enum to check.

        Returns:
            True if tool is a Cargo/Rust tool.
        """
        from lintro.enums.tool_name import ToolName

        return tool_name_enum in {
            ToolName.CLIPPY,
            ToolName.CARGO_AUDIT,
            ToolName.CARGO_DENY,
        }

    def get_command(
        self,
        tool_name: str,
        tool_name_enum: ToolName | None,
    ) -> list[str]:
        """Get command for Cargo tool.

        Args:
            tool_name: String name of the tool.
            tool_name_enum: Tool name enum.

        Returns:
            Command list to execute the tool via cargo.
        """
        from lintro.enums.tool_name import ToolName

        if tool_name_enum is None:
            return ["cargo", "clippy"]

        # Mapping of cargo tools to their subcommands for extensibility
        cargo_subcommands: dict[ToolName, str] = {
            ToolName.CARGO_AUDIT: "audit",
            ToolName.CARGO_DENY: "deny",
            ToolName.CLIPPY: "clippy",
        }
        subcommand = cargo_subcommands.get(tool_name_enum, "clippy")
        return ["cargo", subcommand]


@register_command_builder
class StandaloneBuilder(CommandBuilder):
    """Builder for standalone binary tools.

    These tools are invoked directly by name without any wrapper.
    Uses an explicit mapping for tools whose binary name differs
    from their internal tool name.
    """

    _tools: frozenset[ToolName] | None = None

    # Explicit mapping from internal tool name to binary name.
    # Only tools whose binary name differs need an entry here.
    TOOL_BINARY_MAP: ClassVar[dict[str, str]] = {
        "osv_scanner": "osv-scanner",
    }

    @property
    def tools(self) -> frozenset[ToolName]:
        """Get the set of tools this builder handles.

        Returns:
            Frozen set of ToolName enums for standalone tools.
        """
        if self._tools is None:
            from lintro.enums.tool_name import ToolName

            self._tools = frozenset(
                {
                    ToolName.ACTIONLINT,
                    ToolName.GITLEAKS,
                    ToolName.HADOLINT,
                    ToolName.OSV_SCANNER,
                    ToolName.SHELLCHECK,
                    ToolName.SHFMT,
                    ToolName.SEMGREP,
                },
            )
        return self._tools

    def can_handle(self, tool_name_enum: ToolName | None) -> bool:
        """Check if this builder handles the tool.

        Args:
            tool_name_enum: Tool name enum to check.

        Returns:
            True if tool is a standalone binary.
        """
        return tool_name_enum in self.tools

    def get_command(
        self,
        tool_name: str,
        tool_name_enum: ToolName | None,
    ) -> list[str]:
        """Get command for standalone tool.

        Args:
            tool_name: String name of the tool.
            tool_name_enum: Tool name enum.

        Returns:
            Command list containing the binary name.
        """
        return [self.TOOL_BINARY_MAP.get(tool_name, tool_name)]
