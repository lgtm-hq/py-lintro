"""Node.js project detection and package-manager selection policy.

``lintro install`` used to pick bun whenever bun happened to be on ``PATH`` and
then emit a **global** ``bun add -g`` regardless of what the project said. In an
npm-first repository with a ``package-lock.json`` that produces two authorities:
direct project commands and editors use the local dependency, while lintro
installs (and, before #1811, sometimes execution) use a global one (#2005).

This module centralises the two decisions that fixes that:

1. **Which manager** — explicit user choice, then the ``packageManager`` field,
   then lockfile evidence, then whatever is installed.
2. **Where** — a project-local dev dependency whenever there is a project
   manifest; global only on explicit request or with no manifest at all.

It also reads the *declared* dependency version so the installer can refuse to
silently replace a project's pin with lintro's manifest recommendation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from lintro.enums.install_context import PackageManager
from lintro.enums.node_manager_source import NodeManagerSource
from lintro.models.core.node_manager_commands import NodeManagerCommands

#: Lockfile name to the manager that writes it. Ordered most-specific first so
#: a repository mid-migration (two lockfiles present) resolves deterministically
#: rather than by directory-listing order.
LOCKFILE_MANAGERS: tuple[tuple[str, PackageManager], ...] = (
    ("bun.lock", PackageManager.BUN),
    ("bun.lockb", PackageManager.BUN),
    ("pnpm-lock.yaml", PackageManager.PNPM),
    ("yarn.lock", PackageManager.YARN),
    ("package-lock.json", PackageManager.NPM),
    ("npm-shrinkwrap.json", PackageManager.NPM),
)

#: Managers lintro will fall back to when the project says nothing, in
#: preference order. bun stays first because it is the faster installer; npm
#: next because it is the default Node toolchain. pnpm and yarn follow so a
#: machine that only has those still gets a command it can run, rather than a
#: hardcoded ``npm install`` that then fails with an OS error. This is the
#: *only* branch where lintro's own preference is allowed to decide.
FALLBACK_MANAGER_ORDER: tuple[PackageManager, ...] = (
    PackageManager.BUN,
    PackageManager.NPM,
    PackageManager.PNPM,
    PackageManager.YARN,
)

#: Every Node package manager lintro can drive, with all of its command
#: spellings in one place. ``brew`` and friends are deliberately absent: they
#: have no notion of a project-local dependency.
NODE_MANAGER_COMMANDS: dict[PackageManager, NodeManagerCommands] = {
    PackageManager.BUN: NodeManagerCommands(
        dev_add="bun add -D",
        global_add="bun add -g",
        install_all="bun install",
    ),
    PackageManager.NPM: NodeManagerCommands(
        dev_add="npm install -D",
        global_add="npm install -g",
        install_all="npm install",
    ),
    PackageManager.PNPM: NodeManagerCommands(
        dev_add="pnpm add -D",
        global_add="pnpm add -g",
        install_all="pnpm install",
    ),
    PackageManager.YARN: NodeManagerCommands(
        # Yarn Berry dropped the `-D` short flag; `--dev` works on both.
        dev_add="yarn add --dev",
        global_add="yarn global add",
        install_all="yarn install",
    ),
}

#: Derived so it can never disagree with the command table above.
NODE_MANAGERS: frozenset[PackageManager] = frozenset(NODE_MANAGER_COMMANDS)

#: ``package.json`` dependency tables searched for an existing declaration,
#: in the order a tool would normally be declared.
DEPENDENCY_TABLES: tuple[str, ...] = (
    "devDependencies",
    "dependencies",
    "optionalDependencies",
)


@dataclass(frozen=True)
class NodeProject:
    """A Node.js project discovered by walking up from a starting directory.

    Attributes:
        root: Directory containing the ``package.json``.
        declared_manager: Manager named by the ``packageManager`` field, or
            None when the field is absent or unparseable.
        lockfile_manager: Manager implied by a lockfile in ``root``, or None.
        dependencies: Merged declared dependency specs, package name to spec
            (e.g. ``{"prettier": "^3.4.0"}``).
    """

    root: Path
    declared_manager: PackageManager | None
    lockfile_manager: PackageManager | None
    dependencies: dict[str, str]

    def declared_spec(self, package: str) -> str | None:
        """Return the version spec this project declares for *package*.

        Args:
            package: npm package name.

        Returns:
            The declared spec (e.g. ``"^3.4.0"``), or None when the project does
            not declare the package.
        """
        return self.dependencies.get(package)


def _parse_package_manager_field(value: object) -> PackageManager | None:
    """Parse a ``packageManager`` field value into a known manager.

    The field is a Corepack spec such as ``pnpm@9.1.0+sha512...``; only the name
    before the first ``@`` identifies the manager.

    Args:
        value: Raw JSON value of the ``packageManager`` key.

    Returns:
        The named manager, or None when absent or unrecognised.
    """
    if not isinstance(value, str) or not value:
        return None
    name = value.split("@", 1)[0].strip().lower()
    try:
        manager = PackageManager(name)
    except ValueError:
        logger.debug(f"Unrecognised packageManager field: {value!r}")
        return None
    return manager if manager in NODE_MANAGERS else None


def _read_dependencies(manifest: dict[str, object]) -> dict[str, str]:
    """Collect declared dependency specs from a parsed ``package.json``.

    Args:
        manifest: Parsed ``package.json`` object.

    Returns:
        Mapping of package name to declared spec. Earlier tables in
        :data:`DEPENDENCY_TABLES` win, so a devDependency is not masked by a
        duplicate entry in another table.
    """
    collected: dict[str, str] = {}
    for table in DEPENDENCY_TABLES:
        entries = manifest.get(table)
        if not isinstance(entries, dict):
            continue
        for name, spec in entries.items():
            if isinstance(name, str) and isinstance(spec, str):
                collected.setdefault(name, spec)
    return collected


def search_chain(origin: Path) -> tuple[Path, ...]:
    """Return the directories to search, stopped at a repository boundary.

    An unbounded walk to the filesystem root is dangerous here in a way it is
    not for read-only lookups: a stray ``package.json`` in ``$HOME`` would make
    lintro believe it is inside a Node project, switch from a global install to
    ``npm install -D``, and write a dev dependency into an unrelated manifest.
    A ``~/package-lock.json`` could likewise decide the manager for a project
    that has none.

    The walk therefore stops at the first directory containing a ``.git`` entry
    (inclusive — a repository root is a legitimate project root), and never
    ascends above the user's home directory.

    Args:
        origin: Directory to search from.

    Returns:
        The origin followed by its ancestors, up to and including the boundary.
    """
    try:
        home = Path.home().resolve()
    except (OSError, RuntimeError):  # pragma: no cover - no home on this host
        home = None
    chain: list[Path] = []
    for directory in (origin, *origin.parents):
        # ``$HOME`` itself is excluded once the walk has left it: a stray
        # ``~/package.json`` is exactly the unrelated manifest this boundary
        # exists to avoid, and it is never the project root of something
        # nested below it. Starting *in* ``$HOME`` is a different matter — that
        # is a deliberate cwd, so the manifest there is the one meant.
        if directory == home and chain:
            break
        chain.append(directory)
        # ``.git`` is a directory in a normal clone and a file in a worktree or
        # submodule, so existence is the right test.
        if (directory / ".git").exists() or directory == home:
            break
    return tuple(chain)


def detect_node_project(start: Path | None = None) -> NodeProject | None:
    """Find the nearest enclosing Node.js project.

    Walks up from *start* so running lintro in a subdirectory still finds the
    project manifest, matching how the runtime resolves ``node_modules/.bin``
    (#1811) — install planning and execution must agree on which project they
    are talking about.

    Args:
        start: Directory to search from. Defaults to the process working
            directory.

    Returns:
        The discovered project, or None when no ``package.json`` is found.
    """
    origin = (start or Path.cwd()).resolve()
    chain = search_chain(origin)
    for index, directory in enumerate(chain):
        manifest_path = directory / "package.json"
        if not manifest_path.is_file():
            continue
        manifest = _read_manifest(manifest_path)
        # A workspace package usually has no lockfile and no packageManager
        # field of its own — both live at the repository root. Keep walking for
        # that evidence so a nested manifest does not silently downgrade the
        # decision to "whichever manager is installed".
        declared_manager, lockfile_manager = _ancestor_manager_evidence(
            chain[index:],
            manifest,
        )
        return NodeProject(
            root=directory,
            declared_manager=declared_manager,
            lockfile_manager=lockfile_manager,
            dependencies=_read_dependencies(manifest),
        )
    return None


def _read_manifest(manifest_path: Path) -> dict[str, object]:
    """Parse a ``package.json``, tolerating unreadable or malformed files.

    A manifest that cannot be parsed still marks a project root; it just
    contributes no metadata.

    Args:
        manifest_path: Path to the ``package.json``.

    Returns:
        Parsed manifest object, or an empty dict.
    """
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.debug(f"Could not read {manifest_path}: {exc}")
        return {}
    return manifest if isinstance(manifest, dict) else {}


def _ancestor_manager_evidence(
    directories: tuple[Path, ...],
    manifest: dict[str, object],
) -> tuple[PackageManager | None, PackageManager | None]:
    """Collect manager evidence from a project and its ancestors.

    The nearest evidence of each kind wins, so a workspace package that *does*
    declare its own ``packageManager`` still overrides the repository root.

    Args:
        directories: Project root followed by its ancestors, nearest first.
        manifest: Parsed manifest of the project root.

    Returns:
        Tuple of the declared manager and the lockfile manager, either None.
    """
    declared = _parse_package_manager_field(manifest.get("packageManager"))
    lockfile = _lockfile_manager(directories[0])
    for directory in directories[1:]:
        if declared is not None and lockfile is not None:
            break
        if declared is None and (directory / "package.json").is_file():
            declared = _parse_package_manager_field(
                _read_manifest(directory / "package.json").get("packageManager"),
            )
        if lockfile is None:
            lockfile = _lockfile_manager(directory)
    return declared, lockfile


def _lockfile_manager(root: Path) -> PackageManager | None:
    """Identify the manager from lockfiles present in *root*.

    Args:
        root: Project root directory.

    Returns:
        The manager whose lockfile is present, or None.
    """
    for filename, manager in LOCKFILE_MANAGERS:
        if (root / filename).exists():
            return manager
    return None


def select_node_manager(
    *,
    available: frozenset[PackageManager],
    project: NodeProject | None,
    explicit: PackageManager | None = None,
) -> tuple[PackageManager, NodeManagerSource]:
    """Choose the Node.js package manager to install with.

    Priority is explicit user choice, then the project's ``packageManager``
    metadata, then lockfile evidence, then whatever is installed. Availability
    does **not** veto the first three: telling a user with only bun installed to
    run ``npm install -D`` in their npm-locked project is more useful than
    quietly writing a ``bun.lock`` into it.

    Args:
        available: Managers found on ``PATH``.
        project: Discovered Node project, or None outside one.
        explicit: Manager the user named on the command line.

    Returns:
        Tuple of the chosen manager and why it was chosen.
    """
    if explicit is not None:
        return explicit, NodeManagerSource.EXPLICIT
    if project is not None:
        if project.declared_manager is not None:
            return project.declared_manager, NodeManagerSource.PACKAGE_MANAGER_FIELD
        if project.lockfile_manager is not None:
            return project.lockfile_manager, NodeManagerSource.LOCKFILE
    for manager in FALLBACK_MANAGER_ORDER:
        if manager in available:
            return manager, NodeManagerSource.AVAILABLE_FALLBACK
    return PackageManager.NPM, NodeManagerSource.AVAILABLE_FALLBACK


def add_dependency_command(
    *,
    manager: PackageManager,
    spec: str,
    global_install: bool,
) -> str:
    """Build the add-a-dependency command for a manager.

    Args:
        manager: Package manager to use.
        spec: ``package@version`` spec to install.
        global_install: Install globally rather than as a project dev
            dependency.

    Returns:
        Shell command string.
    """
    commands = NODE_MANAGER_COMMANDS[manager]
    prefix = commands.global_add if global_install else commands.dev_add
    return f"{prefix} {spec}"
