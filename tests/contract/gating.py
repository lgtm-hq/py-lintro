"""Preconditions for the tiered AI CLI contract tests, and how to fail them.

The tiers are separated by cost. Tier 1 reads only ``--version`` and ``--help``,
so it is free and can gate every change. Tier 2 makes a real call, so it spends
quota and runs on a schedule.

What both tiers must never do is skip *quietly*. Every unmet precondition names
which link of the ``presence -> liveness -> invoke`` chain broke, and in the CI
gate — where the binaries and credentials are supposed to exist — the same
condition fails outright instead of skipping. A suite reporting green for checks
it never ran looks exactly like one where they passed, which is the defect behind
both #1826 and this issue.
"""

from __future__ import annotations

import os
from typing import Final, NoReturn

import pytest

#: Set in the contract-gate workflow, where the agent CLIs are baked into the
#: image. There, a missing binary is a broken gate, not an absent developer tool.
REQUIRE_BINARIES_ENV: Final[str] = "LINTRO_CONTRACT_REQUIRE_BINARIES"

#: Opt-in for tier 2. Real invocations cost quota, so they never run implicitly —
#: not on a developer machine, and not as a side effect of ``pytest tests/``.
ENABLE_TIER2_ENV: Final[str] = "LINTRO_CONTRACT_TIER2"

_TRUTHY: Final[frozenset[str]] = frozenset({"1", "true", "yes", "on"})


def _flag_enabled(name: str) -> bool:
    """Return whether an environment flag is set to a truthy value.

    Args:
        name: Environment variable name.

    Returns:
        True when the variable is set to a recognised truthy value.
    """
    return os.environ.get(name, "").strip().lower() in _TRUTHY


def binaries_required() -> bool:
    """Return whether a missing agent CLI must fail rather than skip.

    Returns:
        True when the contract gate declared the binaries present.
    """
    return _flag_enabled(REQUIRE_BINARIES_ENV)


def tier2_enabled() -> bool:
    """Return whether real-invocation smoke tests were explicitly enabled.

    Returns:
        True when quota-spending probes are permitted.
    """
    return _flag_enabled(ENABLE_TIER2_ENV)


def unmet_precondition(reason: str) -> NoReturn:
    """Fail or skip on an unmet precondition, never pass silently.

    Never returns: both branches abort the calling test. In the contract gate,
    where the environment guarantees the precondition, the absence is itself the
    bug and the test fails; elsewhere it skips with the reason attached.

    Args:
        reason: What was missing, phrased so a reader knows which link of the
            chain broke and what to do about it.
    """
    if binaries_required():
        pytest.fail(
            f"contract gate precondition not met: {reason}. "
            f"{REQUIRE_BINARIES_ENV} is set, so this environment is supposed to "
            "provide it — failing rather than skipping.",
        )
    pytest.skip(f"contract check not runnable: {reason}")
