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


def is_registry_fallback_command(command: Sequence[str]) -> bool:
    """Report whether a resolved command is the ``bunx``/``npx`` fallback.

    Args:
        command: Resolved command list, as returned by a command builder.

    Returns:
        True when the command invokes the tool through a package runner rather
        than a project-local or PATH install.
    """
    return len(command) >= 2 and command[0] in REGISTRY_RUNNERS


def registry_fallback_install_hint(command: Sequence[str]) -> str:
    """Build the project-local install guidance for a registry fallback.

    Args:
        command: Resolved ``[runner, spec, ...]`` command list.

    Returns:
        Multi-line guidance naming the pinned install commands and, when
        known, the Node runtime the pin requires.
    """
    package, _version = split_npm_spec(command[1])
    spec = command[1]
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


def registry_fallback_guidance(command: Sequence[str]) -> str:
    """Build the actionable message for a failed registry fallback.

    Args:
        command: Resolved ``[runner, spec, ...]`` command list.

    Returns:
        Message explaining what failed and how to make the tool resolve
        locally instead.
    """
    runner, spec = command[0], command[1]
    package, _version = split_npm_spec(spec)
    return (
        f"{package} could not be run via `{runner} {spec}`.\n"
        f"{registry_fallback_install_hint(command)}"
    )


def notify_registry_fallback_selected(command: Sequence[str]) -> None:
    """Warn once that a tool resolved to the registry fallback.

    The difference between the fast, lockfile-pinned local path and the
    fragile registry path is otherwise invisible until something breaks.

    Args:
        command: Resolved ``[runner, spec, ...]`` command list.
    """
    if not is_registry_fallback_command(command):
        return
    package, _version = split_npm_spec(command[1])
    if package in _FALLBACK_NOTICES_EMITTED:
        return
    _FALLBACK_NOTICES_EMITTED.add(package)
    logger.warning(
        f"No project-local or PATH install of {package} found; falling back to "
        f"`{command[0]} {command[1]}`, which needs registry access.\n"
        f"{registry_fallback_install_hint(command)}",
    )
