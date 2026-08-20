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
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from loguru import logger

from lintro.plugins.subprocess_executor import is_compiled_binary
from lintro.tools.core.node_fallback import notify_registry_fallback_selected

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

    def get_command_in(
        self,
        tool_name: str,
        tool_name_enum: ToolName | None,
        cwd: Path | None = None,
    ) -> list[str]:
        """Get the command, resolved relative to the execution directory.

        Builders whose resolution depends on where the tool runs override this.
        The default ignores ``cwd`` and matches :meth:`get_command`, so
        ecosystems with directory-independent resolution need no change.

        Args:
            tool_name: String name of the tool.
            tool_name_enum: Tool name enum, or None if unknown.
            cwd: Directory the tool will execute in, when known.

        Returns:
            Command list to execute the tool.
        """
        return self.get_command(tool_name, tool_name_enum)


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
        cwd: Path | None = None,
    ) -> list[str]:
        """Get command for a tool using registered builders.

        Iterates through registered builders in order, returning the
        command from the first builder that can handle the tool.

        Args:
            tool_name: String name of the tool.
            tool_name_enum: Tool name enum, or None if unknown.
            cwd: Directory the tool will execute in, when known. Builders that
                resolve project-local installs use it so the binary comes from
                the project being checked rather than lintro's own tree.

        Returns:
            Command list, or [tool_name] as fallback.
        """
        for builder in cls._builders:
            if builder.can_handle(tool_name_enum):
                return builder.get_command_in(tool_name, tool_name_enum, cwd)

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


def resolve_venv_tool_command(tool_name: str) -> list[str] | None:
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

    Resolution is deliberately directory-independent, so the inherited
    :meth:`CommandBuilder.get_command_in` default is correct and this class does
    not override it (#1758). Every candidate — the venv scripts directory from
    :mod:`sysconfig`, ``PATH``, and ``sys.executable -m`` — is a property of the
    process Lintro runs in, not of the directory being checked. That is the
    intended source: these tools are Lintro's own declared dependencies, gated
    on the version manifest's minimums and parsed by version-specific parsers,
    so a checked project's own virtualenv is **not** consulted. Running an
    arbitrary project-supplied version would break both the version gate and
    output parsing. Project-relative work (config discovery, ``per-file-ignores``
    and friends) is done by the tool itself from the cwd the executor sets.
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

        When running in a virtual environment, uses resolve_venv_tool_command
        which prefers python -m if the tool binary lives inside the venv, but
        falls back to a PATH-based binary when the tool is installed externally
        (e.g. via a separate Homebrew formula). Outside a venv, prefers PATH
        binary (works with Homebrew, system packages, pipx, uv tool, etc.).

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
        venv_cmd = resolve_venv_tool_command(tool_name)
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

    Like :class:`PythonBundledBuilder`, resolution is directory-independent and
    the inherited :meth:`CommandBuilder.get_command_in` default is kept (#1758):
    the venv scripts directory, ``PATH`` and ``sys.executable -m pytest`` are all
    process-scoped. Pytest must run in the environment whose interpreter can
    import the code under test, and Lintro supports that by being invoked from
    inside that environment (``uv run lintro ...``), where ``sys.prefix`` already
    *is* the project's virtualenv. Discovering some other project's ``.venv`` by
    walking up from the execution directory is intentionally not done — it would
    silently pick an interpreter whose installed plugins and dependencies Lintro
    knows nothing about.
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

        When running in a virtual environment, uses resolve_venv_tool_command
        which prefers python -m pytest if the pytest binary lives inside the
        venv, but falls back to a PATH-based binary when pytest is installed
        externally (e.g. via a separate Homebrew formula). Outside a venv,
        prefers PATH binary (works with Homebrew, system packages, pipx,
        uv tool, etc.).

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
        venv_cmd = resolve_venv_tool_command("pytest")
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


def find_local_node_binary(
    binary_name: str,
    *,
    start: Path | None = None,
) -> str | None:
    """Find a consumer-local ``node_modules/.bin`` executable.

    Walks up from *start* so a tool invoked from a subdirectory still resolves
    the project's own install. A local install is lockfile-tracked, so it is
    preferred over any registry fetch.

    The walk stops at the nearest ancestor that contains ``package.json`` or
    ``.git`` (a file or directory). That marker directory is still searched;
    directories above it are not, so a decoy ``node_modules`` outside the
    project — or a monorepo root above a nested package with its own
    ``package.json`` — cannot win.

    On Windows the package manager writes a ``.cmd`` shim; the extension-less
    shim there is a shell script that cannot be executed with ``shell=False``.

    Args:
        binary_name: Executable name inside ``node_modules/.bin``.
        start: Directory to start searching from. Defaults to the process
            working directory.

    Returns:
        POSIX path to the local executable, or None when none is present.
    """
    origin = (start or Path.cwd()).resolve()
    name = f"{binary_name}.cmd" if sys.platform == "win32" else binary_name
    for directory in (origin, *origin.parents):
        candidate = directory / "node_modules" / ".bin" / name
        if candidate.is_file():
            return candidate.as_posix()
        if (directory / "package.json").is_file() or (directory / ".git").exists():
            break
    return None


def pinned_npm_spec(package_name: str) -> str:
    """Build a version-pinned ``package@version`` npm spec.

    The version is read from the repo's canonical pins (``package.json`` via
    ``lintro._tool_versions``), so Renovate bumps it like any other npm
    dependency instead of the runtime silently resolving ``@latest``.

    Args:
        package_name: npm package name (e.g. ``html-validate``).

    Returns:
        ``package@version`` when a pin is known, otherwise the bare package
        name.
    """
    from lintro._tool_versions import get_tool_version

    version = get_tool_version(package_name)
    return f"{package_name}@{version}" if version else package_name


@register_command_builder
class NodeJSBuilder(CommandBuilder):
    """Builder for every Node.js tool Lintro can run.

    **Resolution mode: one chain for all Node tools** (#1811). Each tool is
    resolved as ``node_modules/.bin`` (walked upward from the execution
    directory) → binary on ``PATH`` → package runner carrying an explicit
    version pin → bare binary name.

    The chain used to be opt-in via a ``pinned_tools`` set that contained only
    ``html-validate``; every other Node tool went straight to
    ``bunx <binary>``/``npx <binary>``. That is now the default, because the
    per-tool rationale is identical for all of them:

    - **A project-local install wins.** Node is the one ecosystem Lintro
      supports that has a standard project-local install location, and it is
      lockfile-pinned. A checked project that declares the tool as a
      devDependency has already answered "which version should run"; nothing
      Lintro knows beats that. Resolution is anchored on the execution
      directory rather than Lintro's own cwd (#1727).
    - **Then ``PATH``.** A global install (``bun add -g``, ``npm install -g``)
      or a Homebrew formula is a deliberate user choice and is reproducible
      offline. Bare ``bunx``/``npx`` never consults ``PATH``, so on any machine
      with ``bun`` or ``npm`` those installs were previously unreachable.
    - **Then a version-pinned runner, never ``@latest``.** Without the pin the
      package manager resolves ``@latest`` on every run, so a broken upstream
      release takes down every consumer at once with nothing to roll back to.
      This branch also needs registry access and imposes the pinned package's
      own ``engines`` floor on the consumer, so it announces itself (#1767).

    No Node tool is exempt. The chain degrades to exactly the old behaviour
    (``bunx``/``npx``) whenever there is no local and no ``PATH`` install, so
    keeping a tool off it could only ever mean "prefer a less specific answer",
    which no tool wants. That also makes ``verify_tool_version`` — which
    resolves through this registry — check the binary the run will actually
    use, for every tool rather than just for ``html-validate``.
    """

    _package_names: dict[ToolName, str] | None = None
    _binary_names: dict[ToolName, str] | None = None

    @property
    def package_names(self) -> dict[ToolName, str]:
        """Get mapping of tools to npm package names.

        These are the names published to the npm registry, so they must match
        ``lintro/_tool_packages.py`` and ``package.json`` exactly: the registry
        fallback installs this name at the pinned version, and
        :func:`pinned_npm_spec` looks the pin up by it.

        Returns:
            Dictionary mapping ToolName to npm package name.
        """
        if self._package_names is None:
            from lintro.enums.tool_name import ToolName

            self._package_names = {
                ToolName.ASTRO_CHECK: "astro",
                ToolName.COMMITLINT: "@commitlint/cli",
                ToolName.HTML_VALIDATE: "html-validate",
                ToolName.MARKDOWNLINT: "markdownlint-cli2",
                ToolName.OXFMT: "oxfmt",
                ToolName.OXLINT: "oxlint",
                ToolName.PRETTIER: "prettier",
                ToolName.SPECTRAL: "@stoplight/spectral-cli",
                ToolName.STYLELINT: "stylelint",
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
                # Package is "typescript", binary is "tsc"
                ToolName.TSC: "tsc",
                # Package is "@commitlint/cli", binary is "commitlint"
                ToolName.COMMITLINT: "commitlint",
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

    def _get_pinned_command(
        self,
        binary_name: str,
        package_name: str,
        start: Path | None = None,
    ) -> list[str]:
        """Resolve a Node.js tool without ever resolving ``@latest``.

        Landing on the ``bunx``/``npx`` branch emits a one-time notice, because
        it is the fragile path: it needs registry access and imposes the pinned
        package's own ``engines`` floor on the consumer's runtime (#1767).

        When the executable name differs from the package name (``tsc`` in
        ``typescript``, ``commitlint`` in ``@commitlint/cli``) the runner is
        given both via ``--package``; bare ``bunx typescript`` would look for a
        ``typescript`` executable that does not exist. ``npx`` always uses
        ``--yes --package spec binary`` so a CI/non-TTY run cannot hang on an
        install prompt, even when the names match. ``bunx`` keeps the shorter
        ``[bunx, spec]`` form when they match.

        ``--package`` (``-p``) is assumed present on both runners. Verified
        against bun 1.3.14 (``bunx --help`` documents ``-p, --package <package>``
        as "Specify package to install when binary name differs from package
        name") and npm 11, where ``npx --package`` predates npm 7. The repo pins
        no minimum bun version, so if a very old bun ever needs supporting, this
        is the branch to revisit — it degrades to a runner error, not a silently
        wrong binary.

        Args:
            binary_name: Executable name (``node_modules/.bin`` entry).
            package_name: npm package name used for the registry fallback.
            start: Directory to resolve ``node_modules/.bin`` from. Defaults to
                the process working directory.

        Returns:
            Command list executing the tool at a known, pinned version.
        """
        local = find_local_node_binary(binary_name, start=start)
        if local is not None:
            logger.debug(f"Using project-local {binary_name}: {local}")
            return [local]

        tool_path = shutil.which(binary_name)
        if tool_path:
            posix_path = Path(tool_path).as_posix().replace("\\", "/")
            logger.debug(f"Using {binary_name} from PATH: {posix_path}")
            return [posix_path]

        spec = pinned_npm_spec(package_name)
        for runner in ("bunx", "npx"):
            if shutil.which(runner):
                if runner == "npx":
                    command = ["npx", "--yes", "--package", spec, binary_name]
                elif package_name == binary_name:
                    command = [runner, spec]
                else:
                    command = [runner, "--package", spec, binary_name]
                notify_registry_fallback_selected(command)
                return command
        return [binary_name]

    def _resolve(
        self,
        tool_name: str,
        tool_name_enum: ToolName | None,
        start: Path | None,
    ) -> list[str]:
        """Resolve a Node.js tool command from a single search origin.

        Both public entry points funnel through here so they cannot drift:
        ``get_command`` is exactly ``get_command_in`` with no execution
        directory known, rather than a second, subtly different code path
        (#1758).

        Args:
            tool_name: String name of the tool.
            tool_name_enum: Tool name enum, or None if unknown.
            start: Directory to resolve project-local installs from, or None to
                use the process working directory.

        Returns:
            Command list to execute the tool locally, from PATH, or via a
            version-pinned bunx/npx invocation.
        """
        if tool_name_enum is None:
            return [tool_name]

        # Get binary name (falls back to package name if not specified)
        binary_name = self.binary_names.get(
            tool_name_enum,
            self.package_names.get(tool_name_enum, tool_name),
        )

        # Every Node tool uses the same chain and never resolves @latest:
        # project-local install -> PATH -> pinned spec (#1811).
        return self._get_pinned_command(
            binary_name=binary_name,
            package_name=self.package_names.get(tool_name_enum, tool_name),
            start=start,
        )

    def get_command(
        self,
        tool_name: str,
        tool_name_enum: ToolName | None,
    ) -> list[str]:
        """Get command for Node.js tool, resolved from the process directory.

        Prefer :meth:`get_command_in` whenever the execution directory is known;
        this overload can only search from :func:`pathlib.Path.cwd`, which for a
        project-local install is the wrong tree unless Lintro happens to have
        been invoked from it.

        Args:
            tool_name: String name of the tool.
            tool_name_enum: Tool name enum.

        Returns:
            Command list to execute the tool via bunx or directly.
        """
        return self._resolve(tool_name, tool_name_enum, None)

    def get_command_in(
        self,
        tool_name: str,
        tool_name_enum: ToolName | None,
        cwd: Path | None = None,
    ) -> list[str]:
        """Resolve a Node.js tool relative to the execution directory.

        ``node_modules/.bin`` is project-local, so resolution must start from
        the directory the tool will run in. Searching lintro's own working
        directory instead can miss the target project's lockfile-pinned binary
        and — worse — select an unrelated install ahead of PATH, making the
        result depend on where lintro happened to be invoked from (#1727).

        Args:
            tool_name: String name of the tool.
            tool_name_enum: Tool name enum, or None if unknown.
            cwd: Directory the tool will execute in, when known.

        Returns:
            Command list to execute the tool.
        """
        return self._resolve(tool_name, tool_name_enum, cwd)


@register_command_builder
class CargoBuilder(CommandBuilder):
    """Builder for Cargo/Rust tools (Clippy, cargo-audit, cargo-deny).

    Invokes Rust tools via cargo subcommands.

    Resolution is directory-independent, so the inherited
    :meth:`CommandBuilder.get_command_in` default is correct and is not
    overridden (#1758). The command is a bare ``cargo`` looked up on ``PATH``
    (in practice the rustup shim), and everything project-relative is resolved
    by cargo itself from *its own* process cwd, which the executor sets to the
    crate root: the workspace root and ``target/`` come from the enclosing
    ``Cargo.toml``, and ``rust-toolchain.toml`` is found by rustup walking up
    from that same directory. Subcommand binaries (``cargo-audit``,
    ``cargo-deny``) live in ``$CARGO_HOME/bin`` or on ``PATH`` — Cargo has no
    project-local ``node_modules``-style install location for them, so there is
    nothing under the checked directory that could or should win.
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

    Resolution is directory-independent, so the inherited
    :meth:`CommandBuilder.get_command_in` default is correct and is not
    overridden (#1758). The builder emits a bare binary name that the OS
    resolves against ``PATH``; none of these ecosystems define a project-local
    install directory that a checked project could ship, so there is no
    per-directory candidate to prefer. Anything project-relative — config
    discovery, lockfile reading, the dependency set ``pip-audit`` audits — is
    done by the tool itself from the cwd the executor sets.
    """

    _tools: frozenset[ToolName] | None = None

    # Explicit mapping from internal tool name to binary name.
    # Only tools whose binary name differs need an entry here.
    TOOL_BINARY_MAP: ClassVar[dict[str, str]] = {
        "osv_scanner": "osv-scanner",
        "golangci_lint": "golangci-lint",
        "dotenv_linter": "dotenv-linter",
        "pip_audit": "pip-audit",
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
                    ToolName.DOTENV_LINTER,
                    ToolName.GITLEAKS,
                    ToolName.GOLANGCI_LINT,
                    ToolName.HADOLINT,
                    ToolName.OSV_SCANNER,
                    ToolName.PIP_AUDIT,
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
