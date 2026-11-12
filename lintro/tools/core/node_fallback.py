"""Guidance for the ``bunx``/``npx`` registry fallback used by Node tools.

Lintro resolves a pinned Node tool from a project-local ``node_modules/.bin``
install first, then a binary on ``PATH``, and only then through a package
runner carrying an explicit version. That last branch is second-class: it needs
network access to the npm registry and it imposes the pinned package's own
``engines`` floor on the consumer's runtime.

Both facts used to be invisible — the fallback was selected silently and, when
it failed, the user saw the tool's raw error with no hint that a devDependency
would fix it. This module builds the messages that make that path explain
itself (see issue #1767).
"""

from __future__ import annotations

import json
import os
import re
import shutil
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path

from loguru import logger

#: An exact npm pin: ``MAJOR.MINOR.PATCH`` with an optional prerelease and
#: build suffix. Partial versions (``1.2``), wildcards (``1.x``), ranges
#: (``^1.2.3``) and dist-tags (``latest``) all let the runner choose (#2043).
EXACT_SEMVER: re.Pattern[str] = re.compile(
    r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?",
)

# Package runners used as the last-resort registry fallback for Node tools.
REGISTRY_RUNNERS: frozenset[str] = frozenset({"bunx", "npx"})

# Runner flags that carry the package spec when it differs from the executable
# name (``bunx --package typescript@6.0.3 tsc``). Both ``bunx`` and ``npx``
# accept the short and long spelling. ``--yes`` is *not* a package flag; it is
# a boolean runner flag (see ``RUNNER_FLAGS``).
PACKAGE_FLAGS: frozenset[str] = frozenset({"-p", "--package"})

#: Flags on every npx fallback so npm configuration cannot change what runs
#: (#2043). Verified with npx 11.20.0 against a localhost registry: each one
#: overrides the same setting from the environment and from the project, user
#: (``~/.npmrc`` or ``userconfig``) and global (``globalconfig``) npmrc files.
#: ``--workspaces=false`` and ``--include-workspace-root=false`` stop workspace
#: selection moving the run into a member with its own ``node_modules/.bin``;
#: ``--bin-links=true`` makes the installed pin's executable exist;
#: ``--global=false`` stops ``global=true`` breaking the run. A configured
#: ``workspace=<name>`` cannot be cleared by any flag; see
#: :func:`npx_pin_refusal`.
NPX_ISOLATION_FLAGS: tuple[str, ...] = (
    "--workspaces=false",
    "--include-workspace-root=false",
    "--bin-links=true",
    "--global=false",
)

# Boolean runner flags that may precede the package spec or ``--package``.
# Skipped when recovering the spec from a resolved command. ``--yes`` / ``-y``
# is npx's non-interactive install flag, not a package selector; the isolation
# flags are fixed settings, not selectors either.
RUNNER_FLAGS: frozenset[str] = frozenset({"--yes", "-y", *NPX_ISOLATION_FLAGS})

# Node runtime floors that a pinned npm tool imposes on the consumer once the
# registry fallback (or a local install) is used. These come from the pinned
# package's own ``engines`` field, which is not carried in lintro's manifest, so
# they are recorded here and surfaced in the fallback guidance rather than being
# discovered only when a runner aborts on an unsupported runtime (#1767).
NODE_ENGINE_REQUIREMENTS: dict[str, str] = {
    "html-validate": "^22.22.0 || >= 24.8.0",
}

# Packages whose fallback-selected notice has already been emitted. The notice
# is diagnostic, not per-invocation news, so it is emitted once per process to
# stay out of the way of a run that checks many files.
_FALLBACK_NOTICES_EMITTED: set[str] = set()


def reset_registry_fallback_notices() -> None:
    """Forget which fallback notices have been emitted.

    Exposed so tests (and long-lived embedders) can exercise the one-time
    notice more than once in a single process.
    """
    _FALLBACK_NOTICES_EMITTED.clear()


def split_npm_spec(spec: str) -> tuple[str, str | None]:
    """Split an npm ``package@version`` spec into its parts.

    Scoped packages start with ``@``, so the version separator is the *last*
    ``@`` rather than the first.

    Args:
        spec: npm spec such as ``html-validate@11.5.6`` or ``@scope/pkg@1.0.0``.

    Returns:
        Tuple of package name and version, with version None when the spec
        carries no pin.
    """
    separator = spec.rfind("@")
    if separator <= 0:
        return spec, None
    return spec[:separator], spec[separator + 1 :]


def registry_fallback_spec(command: Sequence[str]) -> str | None:
    """Extract the npm spec from a resolved registry-fallback command.

    Handles the shapes the builder emits:

    - ``[bunx, spec]`` when the executable and package share a name
    - ``[bunx, "--package", spec, binary]`` when they differ
    - ``[npx, "--yes", "--package", spec, binary]`` always, including when
      the names match

    Boolean runner flags such as ``--yes`` are skipped until ``--package``
    or the spec is reached. ``--yes`` is not a package flag and must not be
    added to :data:`PACKAGE_FLAGS`.

    Args:
        command: Resolved command list, as returned by a command builder.

    Returns:
        The ``package@version`` spec, or None when *command* is not a registry
        fallback.
    """
    if len(command) < 2 or command[0] not in REGISTRY_RUNNERS:
        return None
    index = 1
    while index < len(command) and command[index] in RUNNER_FLAGS:
        index += 1
    if index >= len(command):
        return None
    if command[index] in PACKAGE_FLAGS:
        # The --package shape is ``[..., flag, spec, binary]``; anything
        # shorter is a malformed command, not a fallback to report on.
        return command[index + 1] if len(command) >= index + 3 else None
    return command[index]


#: npm config that selects a workspace and cannot be cleared by a flag: with
#: ``--workspaces=false`` npm 11 refuses to run at all.
_WORKSPACE_KEY: str = "workspace"


def _env_value(env: Mapping[str, str], name: str) -> tuple[str, str] | None:
    """Look up an ``npm_config_*`` variable as npm does, ignoring case.

    Args:
        env: Environment npx would inherit.
        name: Config name, such as ``userconfig``.

    Returns:
        The matching key and its value, or None.
    """
    wanted = f"npm_config_{name}"
    for key, value in env.items():
        if key.lower() == wanted:
            return key, value
    return None


def _read_npmrc(path: Path) -> dict[str, list[str]] | None:
    """Read the keys of an npmrc file, as lower-cased names to values.

    Only key names and values are needed, so the parse is permissive: comments
    and blank lines are skipped, ``key[]`` is folded into ``key``, quotes are
    stripped, and ``${VAR}`` and ``~`` are expanded in values. Keys inside
    ``[section]`` blocks are kept too, which can only add matches.

    Args:
        path: File to read.

    Returns:
        The keys found (empty when the file does not exist), or None when the
        file exists but cannot be read.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeDecodeError):
        return None
    keys: dict[str, list[str]] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line[0] in ";#" or "=" not in line:
            continue
        key, _, value = line.partition("=")
        name = key.strip().strip("\"'").removesuffix("[]").lower()
        cleaned = os.path.expanduser(os.path.expandvars(value.strip().strip("\"'")))
        keys.setdefault(name, []).append(cleaned)
    return keys


def _npm_root() -> Path | None:
    """Locate the npm package npx belongs to, for its builtin ``npmrc``.

    Returns:
        npm's package directory, or None when npx is not on ``PATH``.
    """
    npx = shutil.which("npx")
    if npx is None:
        return None
    # npx is a link to ``<npm>/bin/npx-cli.js``.
    return Path(npx).resolve().parent.parent


def _global_prefixes(env: Mapping[str, str], configured: list[str]) -> Iterator[Path]:
    """Yield every directory npm might use as its global prefix.

    Args:
        env: Environment npx would inherit.
        configured: ``prefix`` values found in npmrc files.

    Yields:
        Path: Candidate prefixes (duplicates are harmless).
    """
    found = _env_value(env, "prefix")
    if found is not None:
        yield Path(os.path.expanduser(found[1]))
    yield from (Path(value) for value in configured)
    if env.get("PREFIX"):
        yield Path(env["PREFIX"])
    node = shutil.which("node")
    if node is not None:
        for binary in (Path(node), Path(node).resolve()):
            prefix = binary.parent.parent
            yield prefix
            if env.get("DESTDIR"):
                yield Path(env["DESTDIR"]) / prefix.relative_to(prefix.anchor)


def _npmrc_candidates(cwd: Path, env: Mapping[str, str]) -> Iterator[Path]:
    """Yield every npmrc file npm 11 could read for a run in *cwd*.

    Follows ``@npmcli/config``: builtin ``<npm>/npmrc``, the project
    ``.npmrc`` (every ancestor is checked, a superset of the project root),
    ``userconfig`` (default ``~/.npmrc``) and ``globalconfig`` (default
    ``<prefix>/etc/npmrc``). Locations configured inside earlier files are
    followed, as npm does.

    Args:
        cwd: Directory npx would run in.
        env: Environment npx would inherit.

    Yields:
        Path: Candidate files, some of which may not exist.
    """
    root = _npm_root()
    early = [] if root is None else [root / "npmrc"]
    origin = cwd.resolve()
    early.extend(directory / ".npmrc" for directory in (origin, *origin.parents))
    yield from early
    settings: dict[str, list[str]] = {}
    for path in early:
        for key, values in (_read_npmrc(path) or {}).items():
            settings.setdefault(key, []).extend(values)
    users = [Path.home() / ".npmrc", *(Path(v) for v in settings.get("userconfig", []))]
    found = _env_value(env, "userconfig")
    if found is not None:
        users.append(Path(os.path.expanduser(found[1])))
    yield from users
    for path in users:
        for key, values in (_read_npmrc(path) or {}).items():
            settings.setdefault(key, []).extend(values)
    found = _env_value(env, "globalconfig")
    if found is not None:
        yield Path(os.path.expanduser(found[1]))
    yield from (Path(value) for value in settings.get("globalconfig", []))
    for prefix in _global_prefixes(env, settings.get("prefix", [])):
        yield prefix / "etc" / "npmrc"


def _workspace_config_reason(cwd: Path, env: Mapping[str, str]) -> str | None:
    """Refuse when npm is configured to select a workspace.

    Args:
        cwd: Directory npx would run in.
        env: Environment npx would inherit.

    Returns:
        Why npx cannot be trusted, or None.
    """
    found = _env_value(env, _WORKSPACE_KEY)
    if found is not None:
        return f"npm workspace selection is configured ({found[0]}={found[1]})"
    seen: set[Path] = set()
    for path in _npmrc_candidates(cwd, env):
        if path in seen:
            continue
        seen.add(path)
        keys = _read_npmrc(path)
        if keys is None:
            return f"{path.as_posix()} cannot be read"
        if _WORKSPACE_KEY in keys:
            return f"npm workspace selection is configured in {path.as_posix()}"
    return None


def _project_reason(cwd: Path, package: str) -> str | None:
    """Refuse when npm's project tree could already hold something to run.

    Args:
        cwd: Directory npx would run in.
        package: npm package the spec names.

    Returns:
        Why npx cannot be trusted, or None.
    """
    origin = cwd.resolve()
    for directory in (origin, *origin.parents):
        tree = directory / "node_modules"
        if tree.exists():
            return f"a node_modules tree exists at {tree.as_posix()}"
        manifest = directory / "package.json"
        if not manifest.exists():
            continue
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return f"{manifest.as_posix()} cannot be read"
        if not isinstance(data, dict):
            return f"{manifest.as_posix()} cannot be read"
        if "workspaces" in data:
            # npm loads every member's node_modules from the workspace root.
            return f"{manifest.as_posix()} declares npm workspaces"
        if data.get("name") == package:
            return f"{manifest.as_posix()} is the {package} package itself"
    return None


def npx_pin_refusal(
    *,
    cwd: Path,
    package: str,
    pin: str,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """Refuse an npx-only fallback unless npx can only run the pinned package.

    ``npx --package <pkg>@<pin> <bin>`` reuses whatever npm finds in its
    project tree and then runs ``<bin>`` by name from any ancestor
    ``node_modules/.bin``, so an installed tree can make it launch a different
    version than the pin, or nothing (#2043). The generated command carries
    :data:`NPX_ISOLATION_FLAGS`, which neutralise workspace, bin-link and
    global settings from every config source. What remains is refused here:

    - a ``node_modules`` directory in *cwd* or any ancestor;
    - a ``package.json`` in *cwd* or any ancestor that declares
      ``workspaces`` (npm loads members' trees from the root), is the package
      itself, or cannot be parsed;
    - ``workspace=<name>`` in the environment or in any npmrc npm would read,
      or an npmrc that cannot be read.

    ``bunx <pkg>@<pin>`` always resolves from the registry and is not subject
    to this rule.

    Args:
        cwd: Directory npx would run in.
        package: npm package the spec names.
        pin: Exact pinned version.
        environ: Environment npx would inherit; defaults to ``os.environ``.

    Returns:
        None when the pin is what npx runs, otherwise an actionable reason.
    """
    env = os.environ if environ is None else environ
    reason = _project_reason(cwd, package) or _workspace_config_reason(cwd, env)
    if reason is None:
        return None
    return (
        f"only npx is available and {reason}; npx may run an unpinned copy. "
        f"Install {package}@{pin} locally or install bun."
    )


def is_registry_fallback_command(command: Sequence[str]) -> bool:
    """Report whether a resolved command is the ``bunx``/``npx`` fallback.

    Args:
        command: Resolved command list, as returned by a command builder.

    Returns:
        True when the command invokes the tool through a package runner rather
        than a project-local or PATH install.
    """
    return registry_fallback_spec(command) is not None


def registry_fallback_install_hint(command: Sequence[str]) -> str:
    """Build the project-local install guidance for a registry fallback.

    Args:
        command: Resolved ``[runner, spec, ...]`` command list.

    Returns:
        Multi-line guidance naming the pinned install commands and, when
        known, the Node runtime the pin requires.
    """
    spec = registry_fallback_spec(command) or ""
    package, _version = split_npm_spec(spec)
    lines = [
        f"Lintro prefers a project-local install of {package}. Add it to this project:",
        f"    bun add -D {spec}",
        f"    npm install -D {spec}",
    ]
    engines = NODE_ENGINE_REQUIREMENTS.get(package)
    if engines:
        lines.append(f"Note: {spec} requires Node {engines}.")
    return "\n".join(lines)


def _format_fallback_command(command: Sequence[str]) -> str:
    """Render a registry-fallback command for logs and user-facing hints.

    Every builder shape must round-trip, including npx's
    ``[npx, "--yes", "--package", spec, binary]``. Joining the resolved argv
    is the only representation that stays accurate for all of them.

    Args:
        command: Resolved command list, as returned by a command builder.

    Returns:
        Space-joined command suitable for wrapping in backticks.
    """
    return " ".join(command)


def registry_fallback_guidance(command: Sequence[str]) -> str:
    """Build the actionable message for a failed registry fallback.

    Args:
        command: Resolved ``[runner, spec, ...]`` command list.

    Returns:
        Message explaining what failed and how to make the tool resolve
        locally instead.
    """
    spec = registry_fallback_spec(command) or ""
    package, _version = split_npm_spec(spec)
    rendered = _format_fallback_command(command)
    return (
        f"{package} could not be run via `{rendered}`.\n"
        f"{registry_fallback_install_hint(command)}"
    )


def notify_registry_fallback_selected(command: Sequence[str]) -> None:
    """Warn once that a tool resolved to the registry fallback.

    The difference between the fast, lockfile-pinned local path and the
    fragile registry path is otherwise invisible until something breaks.

    Args:
        command: Resolved ``[runner, spec, ...]`` command list.
    """
    spec = registry_fallback_spec(command)
    if spec is None:
        return
    package, _version = split_npm_spec(spec)
    if package in _FALLBACK_NOTICES_EMITTED:
        return
    _FALLBACK_NOTICES_EMITTED.add(package)
    rendered = _format_fallback_command(command)
    logger.warning(
        f"No project-local or PATH install of {package} found; falling back to "
        f"`{rendered}`, which needs registry access.\n"
        f"{registry_fallback_install_hint(command)}",
    )
