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

from collections.abc import Sequence

from loguru import logger

# Package runners used as the last-resort registry fallback for Node tools.
REGISTRY_RUNNERS: frozenset[str] = frozenset({"bunx", "npx"})

# Runner flags that carry the package spec when it differs from the executable
# name (``bunx --package typescript@6.0.3 tsc``). Both ``bunx`` and ``npx``
# accept the short and long spelling. ``--yes`` is *not* a package flag; it is
# a boolean runner flag (see ``RUNNER_FLAGS``).
PACKAGE_FLAGS: frozenset[str] = frozenset({"-p", "--package"})

# Boolean runner flags that may precede the package spec or ``--package``.
# Skipped when recovering the spec from a resolved command. ``--yes`` / ``-y``
# is npx's non-interactive install flag, not a package selector.
RUNNER_FLAGS: frozenset[str] = frozenset({"--yes", "-y"})

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
        f"Lintro prefers a project-local install of {package}. Add it to this "
        "project:",
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
