"""Execution-class enum separating deterministic tools from advisory finders.

Every classic lintro tool wraps an external binary and is deterministic:
the same input always produces the same findings, which is what makes
``lintro chk`` safe to run reflexively in editors, hooks, and CI gates.

AI *finder* tools (``idiom-review`` and any future sibling) break that
contract: they ask a model for opinions, so identical input can yield
different findings, at real API cost and latency. Marking them
:attr:`ExecutionClass.ADVISORY` keeps them out of ``chk`` (and therefore out
of the health score) and routes them to ``lintro review`` instead (#1308).
"""

from __future__ import annotations

from enum import StrEnum, auto


class ExecutionClass(StrEnum):
    """How a tool's findings should be treated by the CLI verbs.

    Attributes:
        DETERMINISTIC: Same input always yields the same findings. Runs under
            ``lintro chk`` / ``lintro fmt`` and feeds the health score.
        ADVISORY: Nondeterministic, opinion-shaped findings (AI finders).
            Runs under ``lintro review`` only, and never affects the exit
            code unless ``--fail-on-findings`` is passed.
    """

    DETERMINISTIC = auto()
    ADVISORY = auto()


def normalize_execution_class(value: str | ExecutionClass) -> ExecutionClass:
    """Normalize a raw value to an :class:`ExecutionClass`.

    Args:
        value: str or ExecutionClass to normalize (case-insensitive).

    Returns:
        ExecutionClass: Normalized enum value.

    Raises:
        ValueError: If the value is not a valid execution class.
    """
    if isinstance(value, ExecutionClass):
        return value
    try:
        return ExecutionClass[value.upper()]
    except KeyError as err:
        raise ValueError(
            f"Unknown execution class: {value!r}. "
            f"Supported values: {[member.value for member in ExecutionClass]}",
        ) from err
