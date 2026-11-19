"""Read the bounds of a declared node-semver range (#2045).

A declared range is not a conflict when the project's package manager can
satisfy it with a version lintro accepts. When no release the range admits is
at or above lintro's minimum (``2.x``, ``<3``, ``~2.8``, ``>=4 <3.5``),
running the project install would fetch every dependency only to end with a
version lintro rejects, so the npm strategy has to tell the two apart before
it plans ``npm install``.

Each ``||`` alternative is read as an interval of releases, with both bounds
and their inclusivity, and is reachable when some release at or above the
minimum lies inside it. The answer is meant to match npm's own
``semver.intersects(range, ">=<minimum>")``. lintro's minimum is always a
release, so a prerelease upper bound reads as its release: a release is below
``<A.B.C-pre`` or ``<=A.B.C-pre`` exactly when it is below ``A.B.C``.

This does not resolve a range against a registry. Whenever the answer would
depend on a prerelease the range admits, or a comparator is not one this
module parses, it says "cannot tell" and the caller asks the user rather than
installing. A range npm's semver rejects (a leading zero, a number above
``Number.MAX_SAFE_INTEGER``, an empty or malformed prerelease or build
identifier, in any ``||`` alternative) is always "cannot tell".
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
    r"(?:-(?P<pre>[0-9A-Za-z.-]+))?(?:\+(?P<build>[0-9A-Za-z.-]+))?",
)

#: A version number as the semver grammar writes it: no leading zero.
_NUMBER = re.compile(r"0|[1-9]\d*")

#: A prerelease identifier: a number without a leading zero, or alphanumerics
#: and hyphens with at least one non-digit.
_PRERELEASE_ID = re.compile(r"0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*")

#: A build identifier: any non-empty run of alphanumerics and hyphens.
_BUILD_ID = re.compile(r"[0-9A-Za-z-]+")

#: JavaScript's ``Number.MAX_SAFE_INTEGER``; npm's semver rejects any version
#: number above it.
_MAX_SAFE_INTEGER = 2**53 - 1

#: An operator separated from its version by whitespace (``>= 3.9.4``).
DETACHED_OPERATOR = re.compile(r"(<=|>=|<|>|=|~>|~|\^)\s+")

#: A plain release version, the only kind of floor this module compares.
_RELEASE = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")

#: The lowest release.
_ZERO: Release = (0, 0, 0)


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
class _Interval:
    """The releases one comparator, or one comparator set, admits.

    Attributes:
        lowest: Lowest admitted release. Over releases, ``>A.B.C`` is
            ``>=A.B.(C+1)``, so it is inclusive unless ``lowest_exclusive``.
        upper: Bounding release, or None when there is no upper bound.
        upper_inclusive: Whether ``upper`` itself is admitted.
        lowest_exclusive: Whether ``lowest`` itself is excluded. Only the
            continuous reading (``loose`` in :func:`_interval`) sets it.
    """

    lowest: Release
    upper: Release | None
    upper_inclusive: bool = False
    lowest_exclusive: bool = False

    def meet(self, other: _Interval) -> _Interval:
        """Return the releases both intervals admit.

        Args:
            other: Interval that must also hold.

        Returns:
            The intersection.
        """
        # At one release an exclusive bound is the stricter lower bound.
        lowest, exclusive = max(
            (self.lowest, self.lowest_exclusive),
            (other.lowest, other.lowest_exclusive),
        )
        uppers = [
            (bound.upper, bound.upper_inclusive)
            for bound in (self, other)
            if bound.upper is not None
        ]
        if not uppers:
            return _Interval(lowest, None, lowest_exclusive=exclusive)
        upper, inclusive = min(uppers)
        return _Interval(lowest, upper, inclusive, lowest_exclusive=exclusive)

    def admits_at_least(self, floor: Release) -> bool:
        """Report whether some admitted version is at or above *floor*.

        Args:
            floor: Lowest acceptable release.

        Returns:
            True when a version at or above *floor* lies inside the interval.
        """
        lowest, exclusive = max((self.lowest, self.lowest_exclusive), (floor, False))
        if self.upper is None:
            return True
        if lowest == self.upper:
            return self.upper_inclusive and not exclusive
        return lowest < self.upper


#: Admits no release (``<0.0.0``).
_NOTHING = _Interval(_ZERO, _ZERO)

#: Admits every release.
_EVERYTHING = _Interval(_ZERO, None)


def range_admits_at_least(spec: str, floor: str) -> bool | None:
    """Decide whether a declared range can resolve to *floor* or later.

    Args:
        spec: A spec the classifier called a range, such as ``2.x``,
            ``>=3.1.0`` or ``1.x || >=3``.
        floor: Lowest version lintro accepts, as ``MAJOR.MINOR.PATCH``.

    Returns:
        True when at least one ``||`` alternative admits a release at or
        above *floor*, False when none does, and None when that cannot be
        read with confidence. npm rejects a whole range when one alternative
        is invalid, so any unreadable alternative makes the answer None,
        whatever the others say.
    """
    floor_match = _RELEASE.fullmatch(floor.strip())
    if floor_match is None:
        return None
    floor_release = _release(tuple(int(part) for part in floor_match.groups()))
    try:
        parsed = [_comparators(alternative) for alternative in spec.split("||")]
        verdicts = [
            _alternative_verdict(comparators, floor_release) for comparators in parsed
        ]
    except _UnparsedError:
        return None
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


def _alternative_verdict(
    comparators: list[_Comparator],
    floor: Release,
) -> bool | None:
    """Decide whether one comparator set admits *floor* or a later release.

    Args:
        comparators: Comparators that must all hold.
        floor: Lowest acceptable release.

    Returns:
        True or False from the releases the set admits, or None when no
        release qualifies but npm's answer could still be "yes". That
        happens in two ways. npm lets a comparator written with
        ``A.B.C-pre`` admit other prereleases of ``A.B.C``, which rank above
        *floor* when ``A.B.C`` does, unless an upper bound below ``A.B.C``
        excludes them (``>=4.0.0-0 <3``). An exact prerelease
        (``3.9.5-rc.1``) admits only itself, which never satisfies a
        release floor, so it is not such a case. And ``semver.intersects``
        treats versions as continuous, so ``>3.9.3 <3.9.4`` intersects
        ``>=3.2.0`` although no release lies between the two bounds, while
        ``>3.9.4 <=3.9.4`` is empty either way. A bound past the largest
        number npm accepts propagates :class:`_UnparsedError` from
        :func:`_bump`.
    """
    strict = _meet_all(comparators, loose=False)
    if strict.admits_at_least(floor):
        return True
    if _meet_all(comparators, loose=True).admits_at_least(floor):
        return None
    for comparator in comparators:
        if not comparator.prerelease or comparator.op in ("", "="):
            continue
        release = _release(comparator.parts)
        if release > floor and (strict.upper is None or strict.upper >= release):
            return None
    return False


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
        _UnparsedError: When *word* is not a comparator or breaks a rule
            npm's semver enforces: a wildcard followed by a number
            (``3.x.5``), a number with a leading zero (``03.2.0``) or above
            ``Number.MAX_SAFE_INTEGER``, a numeric prerelease identifier
            with a leading zero (``3.2.0-01``), or an empty prerelease or
            build identifier (``3.2.0+foo..bar``). A prerelease after a
            partial version is not read either.
    """
    match = COMPARATOR.fullmatch(word)
    if match is None:
        raise _UnparsedError(word)
    written = match["parts"].split(".")
    parts: list[int] = []
    for part in written:
        if not part.isdigit():
            break
        if _NUMBER.fullmatch(part) is None or int(part) > _MAX_SAFE_INTEGER:
            raise _UnparsedError(word)
        parts.append(int(part))
    if any(part.isdigit() for part in written[len(parts) :]):
        raise _UnparsedError(word)
    if match["pre"] is not None and (
        len(parts) != 3 or not _identifiers_valid(match["pre"], _PRERELEASE_ID)
    ):
        raise _UnparsedError(word)
    if match["build"] is not None and not _identifiers_valid(match["build"], _BUILD_ID):
        raise _UnparsedError(word)
    op = match["op"] or ""
    return _Comparator(
        op="~" if op == "~>" else op,
        parts=tuple(parts),
        prerelease=match["pre"] is not None,
    )


def _meet_all(comparators: list[_Comparator], *, loose: bool) -> _Interval:
    """Return the releases every comparator in a set admits.

    Args:
        comparators: Comparators that must all hold.
        loose: Read ``>A.B.C`` as ``>=A.B.C`` (see :func:`_interval`).

    Returns:
        The intersection of the comparators' intervals.
    """
    interval = _EVERYTHING
    for comparator in comparators:
        interval = interval.meet(_interval(comparator, loose=loose))
    return interval


def _interval(comparator: _Comparator, *, loose: bool = False) -> _Interval:
    """Return the releases one comparator admits.

    Follows node-semver's desugaring: ``3.x`` is ``>=3.0.0 <4.0.0``, ``>3.x``
    is ``>=4.0.0``, ``<=3.1`` is ``<3.2.0``, and ``<*`` and ``>*`` admit
    nothing. A prerelease on a lower bound (``>=A.B.C-pre``, ``>A.B.C-pre``)
    admits releases from ``A.B.C``; on an upper bound (``<A.B.C-pre``,
    ``<=A.B.C-pre``) releases below ``A.B.C``. An exact prerelease admits no
    release.

    Args:
        comparator: Parsed comparator.
        loose: Read ``>A.B.C`` as ``>=A.B.C``. Over releases that admits one
            version too many; it stands in for the continuous reading npm's
            ``semver.intersects`` uses, where ``>A.B.C`` reaches arbitrarily
            close to ``A.B.C``.

    Returns:
        The admitted releases.
    """
    op, parts, pre = comparator.op, comparator.parts, comparator.prerelease
    if not parts:
        return _NOTHING if op in ("<", ">") else _EVERYTHING
    full = len(parts) == 3
    if op in ("~", "^"):
        return _Interval(_release(parts), _caret_or_tilde_ceiling(op, parts))
    if op == ">=":
        return _Interval(_release(parts), None)
    if op == ">":
        if full and pre:
            return _Interval(_release(parts), None)
        if full and loose:
            return _Interval(_release(parts), None, lowest_exclusive=True)
        return _Interval(_bump(parts), None)
    if op == "<":
        return _Interval(_ZERO, _release(parts))
    if op == "<=":
        if not full:
            return _Interval(_ZERO, _bump(parts))
        return _Interval(_ZERO, _release(parts), upper_inclusive=not pre)
    if not full:
        return _Interval(_release(parts), _bump(parts))
    if pre:
        return _NOTHING
    return _Interval(_release(parts), _release(parts), upper_inclusive=True)


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

    Raises:
        _UnparsedError: When the bumped number passes
            ``Number.MAX_SAFE_INTEGER``; npm rejects such a bound
            (``9007199254740991.x``).
    """
    if parts[-1] >= _MAX_SAFE_INTEGER:
        raise _UnparsedError(".".join(str(part) for part in parts))
    bumped = (*parts[:-1], parts[-1] + 1)
    return _release(bumped)


def _identifiers_valid(text: str, identifier: re.Pattern[str]) -> bool:
    """Check a dot-separated prerelease or build suffix.

    Args:
        text: The suffix without its ``-`` or ``+``.
        identifier: Pattern each identifier must match in full.

    Returns:
        True when every identifier is non-empty and matches *identifier*.
    """
    return all(identifier.fullmatch(part) for part in text.split("."))


def _release(parts: tuple[int, ...]) -> Release:
    """Pad a partial version with zeros.

    Args:
        parts: Up to three numeric parts.

    Returns:
        A ``MAJOR.MINOR.PATCH`` triple.
    """
    padded = (*parts, 0, 0, 0)
    return (padded[0], padded[1], padded[2])
