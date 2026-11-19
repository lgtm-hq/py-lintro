"""Read the bounds of a declared node-semver range (#2045).

A declared range is not a conflict when the project's package manager can
satisfy it with a version lintro accepts. When the range's upper bound sits
below lintro's minimum (``2.x``, ``<3``, ``~2.8``, ``1 - 2``), running the
project install would fetch every dependency only to end with a version that
is too old, so the npm strategy has to tell the two apart before it plans
``npm install``.

This reads bounds only. It does not resolve a range against a registry and
does not order prereleases; whenever a bound depends on a prerelease, or a
comparator is not one this module parses, the answer is "cannot tell" and the
caller asks the user rather than installing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: A ``MAJOR.MINOR.PATCH`` triple.
Release = tuple[int, int, int]

#: One node-semver comparator: an optional operator, an optional ``v``, a
#: version of one to three parts (each a number or an ``x``/``X``/``*``
#: wildcard), an optional prerelease and an optional build suffix.
COMPARATOR = re.compile(
    r"(?P<op><=|>=|<|>|=|~>|~|\^)?v?"
    r"(?P<parts>(?:\d+|[xX*])(?:\.(?:\d+|[xX*])){0,2})"
    r"(?:-(?P<pre>[0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?",
)

#: An operator separated from its version by whitespace (``>= 3.9.4``).
DETACHED_OPERATOR = re.compile(r"(<=|>=|<|>|=|~>|~|\^)\s+")

#: A plain release version, the only kind of floor this module compares.
_RELEASE = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")


class _UnparsedError(Exception):
    """A comparator or bound this module does not read with confidence."""


@dataclass(frozen=True)
class _Comparator:
    """One parsed comparator.

    Attributes:
        op: Operator, ``""`` when none was written. ``~>`` reads as ``~``.
        parts: Numeric parts up to the first wildcard (``3.x`` gives ``(3,)``).
        prerelease: Whether a prerelease suffix was written.
    """

    op: str
    parts: tuple[int, ...]
    prerelease: bool


@dataclass(frozen=True)
class _Upper:
    """The upper bound of a comparator set.

    Attributes:
        release: Bounding version.
        inclusive: Whether ``release`` itself is admitted.
    """

    release: Release
    inclusive: bool

    def admits_at_least(self, floor: Release) -> bool:
        """Report whether some version at or above *floor* is below this bound.

        Args:
            floor: Lowest acceptable version.

        Returns:
            True when the bound leaves room for *floor* or a later version.
        """
        if self.inclusive:
            return self.release >= floor
        return self.release > floor


def range_admits_at_least(spec: str, floor: str) -> bool | None:
    """Decide whether a declared range can resolve to *floor* or later.

    Args:
        spec: A spec the classifier called a range, such as ``2.x``,
            ``>=3.1.0`` or ``1.x || >=3``.
        floor: Lowest version lintro accepts, as ``MAJOR.MINOR.PATCH``.

    Returns:
        True when at least one ``||`` alternative leaves room for a version
        at or above *floor*, False when every alternative's upper bound is
        below it, and None when that cannot be read with confidence.
    """
    floor_match = _RELEASE.fullmatch(floor.strip())
    if floor_match is None:
        return None
    floor_release = _release(tuple(int(part) for part in floor_match.groups()))
    verdicts: list[bool | None] = []
    for alternative in spec.split("||"):
        try:
            upper = _alternative_upper(_comparators(alternative))
        except _UnparsedError:
            verdicts.append(None)
            continue
        verdicts.append(upper is None or upper.admits_at_least(floor_release))
    if any(verdict is True for verdict in verdicts):
        return True
    if all(verdict is False for verdict in verdicts):
        return False
    return None


def range_lowest_major(spec: str) -> int | None:
    """Return the lowest major version a range admits.

    Used to tell a Yarn Berry ``packageManager`` range (``yarn@^4``) from a
    Classic one.

    Args:
        spec: A node-semver range.

    Returns:
        The smallest major any ``||`` alternative can resolve to, or None
        when an alternative has no lower bound or cannot be read.
    """
    majors: list[int] = []
    for alternative in spec.split("||"):
        try:
            comparators = _comparators(alternative)
        except _UnparsedError:
            return None
        lowest = [_lower_major(comparator) for comparator in comparators]
        bounded = [major for major in lowest if major is not None]
        if not bounded:
            return None
        majors.append(max(bounded))
    return min(majors) if majors else None


def _comparators(alternative: str) -> list[_Comparator]:
    """Parse one ``||`` alternative into comparators.

    A hyphen range ``A - B`` becomes ``>=A`` plus an upper bound from ``B``
    (``<=B`` when ``B`` is complete, ``<`` the next version otherwise), which
    is how node-semver desugars it.

    Args:
        alternative: Text between ``||`` separators.

    Returns:
        Parsed comparators; empty for an empty alternative (any version).

    Raises:
        _UnparsedError: When a word is not a comparator.
    """
    words = DETACHED_OPERATOR.sub(r"\1", alternative.strip()).split()
    if len(words) == 3 and words[1] == "-":
        low = _parse(words[0])
        high = _parse(words[2])
        if low.op or high.op:
            raise _UnparsedError(alternative)
        lower = _Comparator(">=", low.parts, low.prerelease)
        if not high.parts:
            return [lower]
        if len(high.parts) == 3:
            return [lower, _Comparator("<=", high.parts, high.prerelease)]
        return [lower, _Comparator("<", _bump(high.parts), prerelease=False)]
    return [_parse(word) for word in words]


def _parse(word: str) -> _Comparator:
    """Parse one comparator word.

    Args:
        word: Comparator text without whitespace.

    Returns:
        The parsed comparator.

    Raises:
        _UnparsedError: When *word* is not a comparator.
    """
    match = COMPARATOR.fullmatch(word)
    if match is None:
        raise _UnparsedError(word)
    parts: list[int] = []
    for part in match["parts"].split("."):
        if not part.isdigit():
            break
        parts.append(int(part))
    op = match["op"] or ""
    return _Comparator(
        op="~" if op == "~>" else op,
        parts=tuple(parts),
        prerelease=match["pre"] is not None,
    )


def _alternative_upper(comparators: list[_Comparator]) -> _Upper | None:
    """Return the tightest upper bound of a comparator set.

    Args:
        comparators: Comparators that must all hold.

    Returns:
        The lowest upper bound, or None when the set has none.
    """
    bounds = [_upper(comparator) for comparator in comparators]
    present = [bound for bound in bounds if bound is not None]
    if not present:
        return None
    return min(present, key=lambda bound: (bound.release, bound.inclusive))


def _upper(comparator: _Comparator) -> _Upper | None:
    """Return the upper bound one comparator sets.

    Args:
        comparator: Parsed comparator.

    Returns:
        The bound, or None when the comparator sets no upper bound.

    Raises:
        _UnparsedError: When the bound would depend on a prerelease, or the
            comparator admits nothing (``<*``).
    """
    op, parts = comparator.op, comparator.parts
    if op in (">", ">=") or not parts and op != "<":
        return None
    if op in ("~", "^"):
        return _Upper(_caret_or_tilde_ceiling(op, parts), inclusive=False)
    if comparator.prerelease or not parts:
        raise _UnparsedError(op)
    if len(parts) == 3 and op != "<":
        return _Upper(_release(parts), inclusive=True)
    if op == "<":
        return _Upper(_release(parts), inclusive=False)
    return _Upper(_bump(parts), inclusive=False)


def _caret_or_tilde_ceiling(op: str, parts: tuple[int, ...]) -> Release:
    """Return the exclusive ceiling of a ``^`` or ``~`` comparator.

    Args:
        op: ``"^"`` or ``"~"``.
        parts: Numeric parts, at least one.

    Returns:
        The first version the comparator excludes.
    """
    if op == "~":
        return _bump(parts[:2])
    # ``^`` keeps the left-most non-zero part fixed; with only zeros written it
    # keeps every written part fixed (``^0.0`` is ``<0.1.0``).
    for index, part in enumerate(parts):
        if part != 0:
            return _bump(parts[: index + 1])
    return _bump(parts)


def _lower_major(comparator: _Comparator) -> int | None:
    """Return the lowest major version one comparator admits.

    Args:
        comparator: Parsed comparator.

    Returns:
        The major, or None when the comparator sets no lower bound.
    """
    if comparator.op in ("<", "<=") or not comparator.parts:
        return None
    if comparator.op == ">" and len(comparator.parts) == 1:
        return comparator.parts[0] + 1
    return comparator.parts[0]


def _bump(parts: tuple[int, ...]) -> Release:
    """Return the first version past a partial version.

    Args:
        parts: One to three numeric parts.

    Returns:
        ``(3,)`` gives ``4.0.0``, ``(3, 1)`` gives ``3.2.0`` and
        ``(3, 1, 4)`` gives ``3.1.5``.
    """
    bumped = (*parts[:-1], parts[-1] + 1)
    return _release(bumped)


def _release(parts: tuple[int, ...]) -> Release:
    """Pad a partial version with zeros.

    Args:
        parts: Up to three numeric parts.

    Returns:
        A ``MAJOR.MINOR.PATCH`` triple.
    """
    padded = (*parts, 0, 0, 0)
    return (padded[0], padded[1], padded[2])
