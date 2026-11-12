"""What ``npx --package <pkg>@<pin> <bin>`` would launch (#2043).

The version gate must know which binary npx runs before it can trust the pin
as version evidence. Lintro decides offline, without calling npm.
"""

from __future__ import annotations

from enum import StrEnum, auto


class NpxLaunch(StrEnum):
    """Offline prediction of the executable npx would start.

    Attributes:
        PINNED: No installed package matches the pin, so npx installs (or
            reuses its cache for) the pinned package and runs it.
        LOCAL: The pin is installed somewhere in the project tree, so npx
            skips the install and runs ``<bin>`` by name from an ancestor
            ``node_modules/.bin``. That binary may be a different version.
        NO_BIN: The pin is installed but no ``<bin>`` is on npx's ``PATH``;
            npx would exit 127.
        UNDECIDED: The tree could not be inspected within its bounds, so the
            outcome is unknown and the gate must fail closed.
    """

    PINNED = auto()
    LOCAL = auto()
    NO_BIN = auto()
    UNDECIDED = auto()
