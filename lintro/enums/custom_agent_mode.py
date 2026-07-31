"""Activation modes for user-defined review agents."""

from __future__ import annotations

from enum import StrEnum, auto

__all__ = ["CustomAgentMode"]


class CustomAgentMode(StrEnum):
    """How ``lintro review`` activates user-defined review agents.

    * **enabled** — default; discovered agents run alongside the built-in
      checklist corpus.
    * **disabled** — discovery is skipped entirely and only the built-in
      checklist runs.
    * **only** — discovered agents run and the built-in checklist pass is
      skipped.

    The YAML spellings ``true`` / ``false`` map onto ``enabled`` / ``disabled``
    so ``review.custom_agents: true|false|only`` reads naturally in
    ``.lintro-config.yaml``.
    """

    ENABLED = auto()
    DISABLED = auto()
    ONLY = auto()
