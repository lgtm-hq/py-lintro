"""Install-channel enum for tool update advisories.

Identifies *how a tool binary was installed on this machine* (path
heuristics), which is distinct from the manifest ``install.type`` that
describes how lintro *expects* the tool to be installed.
"""

from __future__ import annotations

from enum import StrEnum, auto


class UpdateChannel(StrEnum):
    """Detected install channel for an external tool binary."""

    HOMEBREW = auto()
    UV_TOOL = auto()
    PIP = auto()
    NPM = auto()
    BUN = auto()
    CARGO = auto()
    RUSTUP = auto()
    STANDALONE = auto()
    UNKNOWN = auto()
