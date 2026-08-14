"""Provenance of a resolved AI configuration field."""

from __future__ import annotations

from enum import StrEnum

__all__ = ["ConfigSource"]


class ConfigSource(StrEnum):
    """Where one effective AI config value came from.

    Precedence is ``flag > env > config > default``. A future user-config
    tier (#1235) slots between project config and the built-in default.
    """

    FLAG = "flag"
    ENV = "env"
    CONFIG = "config"
    DEFAULT = "default"
