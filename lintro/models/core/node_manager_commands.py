"""Command spellings for a single Node.js package manager."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NodeManagerCommands:
    """The three command forms lintro needs from a Node package manager.

    These are whole command prefixes rather than a verb plus a flag because the
    managers do not share a shape: yarn puts the scope *before* the verb
    (``yarn global add``) where every other manager puts it after
    (``npm install -g``). Composing them from parts produced ``yarn add global``.

    Keeping all three together in one record is deliberate. They used to live in
    three dicts keyed by the same enum, so adding a manager meant three edits
    and a missed one was a ``KeyError`` at runtime — the same
    parallel-table drift that put the wrong npm package name on ``commitlint``.

    Attributes:
        dev_add: Adds a project-local dev dependency, e.g. ``npm install -D``.
        global_add: Adds a global package, e.g. ``npm install -g``.
        install_all: Installs everything the manifest already declares, e.g.
            ``npm install``. This is the right advice for a declared-but-missing
            package: it restores the pinned version from the lockfile rather
            than rewriting the pin.
    """

    dev_add: str
    global_add: str
    install_all: str
