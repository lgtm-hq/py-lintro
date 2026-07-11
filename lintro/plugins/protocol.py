"""Plugin protocol defining the contract for all Lintro tools.

This module defines the core abstractions for Lintro's plugin system:
- ToolDefinition: Metadata describing what a tool IS
- LintroPlugin: Protocol contract that all tools must satisfy
- LINTRO_PLUGIN_API_VERSION: Public API version for third-party plugins

Public plugin contract (stable surface for third-party plugins):
    Third-party tool plugins are ordinary Python classes shipped in a separate
    distribution and advertised through the ``lintro.tools`` entry-point group.
    To be discovered and registered, a plugin class must:

    1. Subclass :class:`lintro.plugins.base.BaseToolPlugin` (recommended) or
       otherwise satisfy the :class:`LintroPlugin` protocol below — i.e. expose
       a ``definition`` property plus ``check``, ``fix`` and ``set_options``
       methods.
    2. Optionally declare ``LINTRO_PLUGIN_API_VERSION`` (a class attribute) so
       the loader can reject plugins built against an incompatible core.

    Subclassing ``BaseToolPlugin`` is strongly preferred: it provides the same
    lifecycle builtin tools use (config injection, file discovery, subprocess
    execution, output normalization) and — critically — the per-invocation
    isolation contract (:meth:`~lintro.plugins.base.BaseToolPlugin.copy_for_execution`)
    that the parallel executor relies on.

Example:
    >>> from lintro.plugins.protocol import ToolDefinition, LintroPlugin
    >>> from lintro.plugins.base import BaseToolPlugin
    >>>
    >>> class MyPlugin(BaseToolPlugin):
    ...     @property
    ...     def definition(self) -> ToolDefinition:
    ...         return ToolDefinition(name="my-tool", description="My custom tool")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from lintro.enums.tool_type import ToolType

if TYPE_CHECKING:
    from lintro.models.core.tool_result import ToolResult

#: Current major version of the public Lintro plugin API.
#:
#: This is the stability contract for third-party tool plugins discovered via
#: the ``lintro.tools`` entry-point group. External plugins may declare the API
#: version they were written against with a class-level
#: ``LINTRO_PLUGIN_API_VERSION`` attribute; at load time the registry compares
#: it against this constant and skips (with a warning) any plugin built for an
#: incompatible major version, so core refactors never silently break — or
#: crash — installed plugins.
#:
#: Bump this only on a breaking change to the plugin-facing contract
#: (``BaseToolPlugin`` / ``LintroPlugin`` / ``ToolDefinition``).
LINTRO_PLUGIN_API_VERSION: int = 1


def is_compatible_api_version(declared: object) -> bool:
    """Report whether a plugin's declared API version is compatible with core.

    A plugin may declare the plugin-API major version it targets via a
    class-level ``LINTRO_PLUGIN_API_VERSION`` attribute. Compatibility rules:

    - ``None`` (undeclared) is treated as compatible for convenience, though
      declaring a version is strongly recommended for forward safety.
    - An integer equal to :data:`LINTRO_PLUGIN_API_VERSION` is compatible.
    - Anything else (a different major version, or a non-integer value) is
      incompatible.

    Args:
        declared: The value of the plugin's ``LINTRO_PLUGIN_API_VERSION``
            attribute, or ``None`` if it declares none.

    Returns:
        True if the declared version is compatible with the running core.
    """
    if declared is None:
        return True
    if isinstance(declared, bool) or not isinstance(declared, int):
        return False
    return declared == LINTRO_PLUGIN_API_VERSION


@dataclass(frozen=True)
class ToolDefinition:
    """Metadata describing a Lintro tool.

    This is the single source of truth for what a tool IS.
    Implementation details go in separate files (under implementations/).

    Attributes:
        name: Unique identifier for the tool (lowercase, e.g., "hadolint").
        description: Human-readable description of what the tool does.
        can_fix: Whether the tool can auto-fix issues.
        tool_type: Bitmask of ToolType flags describing capabilities.
        file_patterns: Glob patterns for files this tool operates on.
        priority: Execution priority (lower = runs first). Default is 50.
        conflicts_with: Names of tools that conflict with this one.
        native_configs: Config files the tool respects natively
            (Lintro won't interfere).
        version_command: Command to check tool version
            (e.g., ["hadolint", "--version"]).
        min_version: Minimum required version string.
        default_options: Default tool-specific options.
        default_timeout: Default execution timeout in seconds.
    """

    # Identity
    name: str
    description: str

    # Capabilities
    can_fix: bool = False
    tool_type: ToolType = ToolType.LINTER

    # File targeting
    file_patterns: list[str] = field(default_factory=list)

    # Execution
    priority: int = 50
    conflicts_with: list[str] = field(default_factory=list)

    # Native config files this tool respects (Lintro should NOT interfere)
    native_configs: list[str] = field(default_factory=list)

    # Version checking
    version_command: list[str] | None = None
    min_version: str | None = None

    # Default options
    default_options: dict[str, object] = field(default_factory=dict)
    default_timeout: int = 30

    def __post_init__(self) -> None:
        """Validate tool definition.

        Raises:
            ValueError: If name is empty or priority is negative.
        """
        if not self.name:
            raise ValueError("Tool name cannot be empty")
        if self.priority < 0:
            raise ValueError(f"Tool priority must be non-negative, got {self.priority}")


@runtime_checkable
class LintroPlugin(Protocol):
    """Contract that all Lintro tools must satisfy.

    This protocol defines the interface for tool plugins. Tools can be
    implemented by inheriting from BaseToolPlugin or by implementing
    this protocol directly.

    Example:
        >>> class MyPlugin:
        ...     @property
        ...     def definition(self) -> ToolDefinition:
        ...         return ToolDefinition(name="my-tool", description="My tool")
        ...
        ...     def check(self, paths, options) -> ToolResult:
        ...         ...  # Implementation here
        ...
        ...     def fix(self, paths, options) -> ToolResult:
        ...         raise NotImplementedError("No fixing")
        ...
        ...     def set_options(self, **kwargs: object) -> None:
        ...         # Set tool options
        ...         ...
    """

    @property
    def definition(self) -> ToolDefinition:
        """Return the tool's metadata."""
        ...

    def check(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Check files for issues.

        Args:
            paths: List of file or directory paths to check.
            options: Tool-specific options that override defaults.

        Returns:
            ToolResult containing check results and any issues found.
        """
        ...

    def fix(self, paths: list[str], options: dict[str, object]) -> ToolResult:
        """Fix issues in files.

        Args:
            paths: List of file or directory paths to fix.
            options: Tool-specific options that override defaults.

        Returns:
            ToolResult containing fix results and any remaining issues.
        """
        ...

    def set_options(self, **kwargs: object) -> None:
        """Set tool-specific options.

        Args:
            **kwargs: Tool-specific options to set.
        """
        ...

    def doc_url(self, code: str) -> str | None:
        """Return a documentation URL for the given rule code.

        Args:
            code: The rule/error code (e.g., "E501", "SC2086").

        Returns:
            Documentation URL string, or None if no docs available.
        """
        ...
