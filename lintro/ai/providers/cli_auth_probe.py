"""Declarative description of a provider CLI's authentication surface.

``lintro doctor`` reports whether a locally installed agent binary is likely to
authenticate before anything spends a call on it. That verdict used to be a
per-provider ``if`` ladder in :mod:`lintro.ai.doctor_checks`; it is data, so it
lives on the provider's metadata instead and doctor renders it uniformly.

The probe is deliberately *presence only* and never spawns the binary: it reads
environment variables and looks for auth files the vendor CLI writes at login.
A present credential is not a working one — that is
:mod:`lintro.ai.liveness`'s job.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

__all__ = ["CliAuthProbe"]


@dataclass(frozen=True, kw_only=True, slots=True)
class CliAuthProbe:
    """How one provider's CLI proves, cheaply, that it can authenticate.

    Attributes:
        honors_api_key_env: Whether the provider's API-key variable (the
            configured ``ai.api_key_env`` override, else the provider default)
            counts as proof. Vendors whose CLI reads a different variable than
            the SDK set this ``False`` and list that variable in
            ``extra_env_vars`` instead.
        extra_env_vars: Additional environment variables whose presence proves
            auth, checked in declaration order.
        auth_files: Home-relative paths the vendor CLI writes at login; the
            presence of any one proves auth. Relative to the user's home
            directory so no absolute path is baked into the metadata.
        configured_message: Doctor message when auth is present. Formatted with
            a ``key_env`` keyword holding the resolved API-key variable name.
        unverified_message: Doctor message when nothing proved auth. Not an
            error: an interactive vendor login lintro cannot see is the common
            case.
        hint: Actionable guidance shown alongside *unverified_message*.
    """

    honors_api_key_env: bool = False
    extra_env_vars: tuple[str, ...] = ()
    auth_files: tuple[str, ...] = ()
    configured_message: str
    unverified_message: str
    hint: str

    def is_configured(self, *, key_env: str) -> bool:
        """Report whether a credential this CLI can read is present.

        Args:
            key_env: The API-key variable resolved for this run — the user's
                ``ai.api_key_env`` override when set, else the provider
                default. Only read when :attr:`honors_api_key_env`; callers
                pass an empty string otherwise, so a provider whose CLI reads a
                different variable than its SDK is never handed the SDK one.

        Returns:
            True when an environment variable or auth file proves the CLI can
            authenticate.
        """
        if self.honors_api_key_env and key_env and os.environ.get(key_env):
            return True
        if any(os.environ.get(name) for name in self.extra_env_vars):
            return True
        if not self.auth_files:
            # An env-only probe has no reason to resolve a home directory, and
            # `Path.home()` raises rather than returning None when it cannot.
            return False
        home = Path.home()
        return any((home / name).is_file() for name in self.auth_files)

    def describe(self, *, key_env: str) -> str:
        """Render the doctor message for a configured credential.

        Args:
            key_env: The API-key variable resolved for this run.

        Returns:
            :attr:`configured_message` with ``{key_env}`` substituted.
        """
        return self.configured_message.format(key_env=key_env)
