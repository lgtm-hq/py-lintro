"""Pin the declared-range reader to npm's own answers (#2045).

``range_admits_at_least(spec, minimum)`` decides whether a project's declared
range can reach lintro's minimum before lintro plans a whole-project
``npm install``. Its reference is npm's bundled node-semver:
``semver.intersects(spec, ">=" + minimum)``. The table below was produced
once, not at test time, by running this script with Node 26.13.1 against the
semver 7.8.5 that npm 11.20.0 bundles (``$(npm root -g)/npm/node_modules/
semver``, read-only)::

    const semver = require(process.argv[2]);
    const ranges = JSON.parse(require("fs").readFileSync(process.argv[3]));
    for (const r of ranges) {
      const row = ["3.2.0", "3.6.0", "3.9.4"].map((m) => {
        try { return semver.intersects(r, ">=" + m) ? "T" : "F"; }
        catch (e) { return "E"; }
      });
      console.log(JSON.stringify(r) + " " + row.join(""));
    }

Each row is ``(spec, verdicts)`` with one letter per minimum 3.2.0, 3.6.0
and 3.9.4: ``T`` intersects, ``F`` does not, ``E`` npm rejects the range.
"""

from __future__ import annotations

import pytest
from assertpy import assert_that

from lintro.tools.core.install_strategies.declared_range import (
    range_admits_at_least,
)

#: The minimums each verdict letter belongs to, in order.
_MINIMUMS: tuple[str, ...] = ("3.2.0", "3.6.0", "3.9.4")

#: npm's verdicts, pasted from the script's output.
_NPM_TABLE: tuple[tuple[str, str], ...] = (
    ("<4.0.0-rc.1", "TTT"),
    ("<4.0.0-0", "TTT"),
    (">=3.0.0 <4.0.0-0", "TTT"),
    ("<3.6.0-rc.1", "TFF"),
    ("<3.9.5-0", "TTT"),
    ("<=3.9.5-0", "TTT"),
    ("<=4.0.0-rc.1", "TTT"),
    ("2.x || <4.0.0-rc.1", "TTT"),
    (">=4 <3.5", "FFF"),
    ("<3.6.0 >=3.6.0", "FFF"),
    ("3.6.0 - 3.5.0", "FFF"),
    (">3.x <4", "FFF"),
    ("^3", "TTT"),
    ("~3", "TTT"),
    ("3.x", "TTT"),
    ("3.1.x", "FFF"),
    ("*", "TTT"),
    ("", "TTT"),
    (">=4", "TTT"),
    (">2", "TTT"),
    (">2.x", "TTT"),
    ("<=2.9.9", "FFF"),
    (">=2 <3", "FFF"),
    (">=3 <3.5", "TFF"),
    ("^2 || ^3", "TTT"),
    ("2 || 3", "TTT"),
    ("1.x || 2.x", "FFF"),
    ("|| 3", "TTT"),
    ("3.1 - 3.5", "TFF"),
    ("3 - 4", "TTT"),
    ("2 - 3.5", "TFF"),
    ("3.x - 3.7", "TTF"),
    ("1 - 2", "FFF"),
    ("3.0.0 - 3.9.4", "TTT"),
    ("3.5 - 3.5", "TFF"),
    ("3.9.4 - 3.9.4", "TTT"),
    ("3.6 - 3.5", "FFF"),
    ("~>3.9", "TTT"),
    ("~>3.9.4", "TTT"),
    (">= v3.1", "TTT"),
    ("^ 3", "TTT"),
    ("<3.0.0-0", "FFF"),
    ("2.x", "FFF"),
    ("2", "FFF"),
    ("<3", "FFF"),
    ("<=3.9.3", "TTF"),
    ("~2.8", "FFF"),
    ("~3.8", "TTF"),
    ("~3.5", "TFF"),
    (">=2.0.0 <3.0.0", "FFF"),
    (">=3.1.0", "TTT"),
    (">=3.9.4", "TTT"),
    (">= 3.9.4", "TTT"),
    (">=3.0.0 <4.0.0", "TTT"),
    ("3.9.x", "TTT"),
    ("3", "TTT"),
    ("^3.9", "TTT"),
    ("^2.0.0 || ^3.0.0", "TTT"),
    ("3.9.x || >=4", "TTT"),
    (">=7.0.0", "TTT"),
    ("^3.1.0", "TTT"),
    ("<3.9.4", "TTF"),
    ("<=3.9.4", "TTT"),
    (">3.9.4", "TTT"),
    (">3.9.3 <3.9.4", "TTF"),
    (">3.9.3 <=3.9.4", "TTT"),
    (">=3.6.0 <3.6.1", "TTF"),
    (">3.5.9 <3.6.0", "TFF"),
    ("^0.1", "FFF"),
    ("<*", "FFF"),
    (">*", "FFF"),
    (">=*", "TTT"),
    ("<=*", "TTT"),
    ("~*", "TTT"),
    ("^*", "TTT"),
    ("x", "TTT"),
    ("X", "TTT"),
    (">=3.9.5-rc.1 <=3.9.5-rc.3", "TTT"),
    (">=3.0.0-rc.1 <3.2.0", "FFF"),
    (">3.9.4-rc.1", "TTT"),
    ("3.9.5-rc.1 || 2.x", "FFF"),
    ("<=3.2.0-rc.1", "FFF"),
    (">=3.9.4-0", "TTT"),
    ("^3.9.4-beta.1", "TTT"),
    ("~3.5.0-rc.1", "TFF"),
    ("<3.6 || >=3.9", "TTT"),
    (">=3.2.0 <3.2.0", "FFF"),
    ("=3.6", "TTF"),
    (">=3.9 <3.9.4", "TTF"),
    (" >=3.0.0 ", "TTT"),
    ("3.x.5", "EEE"),
    ("<3.2", "FFF"),
    ("<=3.2", "TFF"),
    ("<=3.1", "FFF"),
    (">3.9", "TTT"),
    (">=3.9.5 <4", "TTT"),
    (">3.9.4 <=3.9.4", "FFF"),
    ("<=3.9.4-rc.1", "TTF"),
    ("3.0.0 - 3.9.5-rc.1", "TTT"),
    (">=3.2.0-rc.1 <3.2.1", "TFF"),
    ("^0.0.3", "FFF"),
    ("^0.0", "FFF"),
    ("<0.0.0", "FFF"),
    (">=0.0.0", "TTT"),
    ("3.x || 4.x", "TTT"),
    ("<2 || >=3.7 <3.8", "TTF"),
    (">=3.5.0 <=3.6.0", "TTF"),
    ("=3.9.4 || 2", "TTT"),
    ("~3.9.4", "TTT"),
    ("~v3.9.4", "TTT"),
    ("^v3.9", "TTT"),
    (">=3.6.0-0 <3.6.0", "TFF"),
    # Review round 3: ranges npm rejects, and empty ranges, same script.
    (">=9007199254740992", "EEE"),
    (">=999999999999999999999", "EEE"),
    (">=9007199254740991", "TTT"),
    ("9007199254740991.x", "EEE"),
    (">=03.2.0", "EEE"),
    (">=3.02.0", "EEE"),
    (">=3.2.0-01", "EEE"),
    (">=3.2.0-rc.01", "EEE"),
    (">=3.2.0-rc..1", "EEE"),
    (">=3.2.0-0", "TTT"),
    (">=3.2.0+foo..bar", "EEE"),
    (">=3.2.0+foo.", "EEE"),
    (">=3.2.0+01", "TTT"),
    (">=3.2.0 || 3.x.5", "EEE"),
    (">=3.2.0 || >=03.2.0", "EEE"),
    ("3.x+build", "TTT"),
    (">3.6.0 <=3.6.0", "FFF"),
    (">=4.0.0-0 <3", "FFF"),
    ("3.9.5-rc.1", "FFF"),
    ("3.9.5-rc.1 >=3.0.0", "FFF"),
    (">=4 <4.0.0-rc.1", "FFF"),
)

#: Known conservative answers: ``?`` marks a minimum where the reader says
#: "cannot tell" (and lintro asks the user) although npm gives an answer.
#: Every other letter must equal npm's.
_CONSERVATIVE: dict[str, str] = {
    # npm's intersects treats versions as continuous; no release lies
    # strictly between 3.9.3 and 3.9.4, so ``npm install`` finds nothing.
    ">3.9.3 <3.9.4": "??F",
    # Prerelease lower bounds admit prereleases of 3.9.5 / 3.6.0, which rank
    # above the minimum; the reader does not order prereleases.
    ">=3.9.5-rc.1 <=3.9.5-rc.3": "???",
    ">=3.6.0-0 <3.6.0": "?FF",
    # The prerelease upper bound reads as 4.0.0, which a prerelease of 4.0.0
    # could sit below; the reader does not order prereleases.
    ">=4 <4.0.0-rc.1": "???",
}

_LETTER: dict[bool | None, str] = {True: "T", False: "F", None: "?"}


def test_table_covers_enough_ranges() -> None:
    """The table pins at least 60 distinct ranges."""
    specs = [spec for spec, _verdicts in _NPM_TABLE]

    assert_that(len(set(specs))).is_equal_to(len(specs))
    assert_that(len(specs)).is_greater_than_or_equal_to(60)


def test_conservative_entries_differ_from_npm_only_by_cannot_tell() -> None:
    """A conservative answer may replace npm's verdict only with ``?``."""
    npm = dict(_NPM_TABLE)

    for spec, reader in _CONSERVATIVE.items():
        pairs = zip(reader, npm[spec].replace("E", "?"), strict=True)
        assert_that(all(got in ("?", want) for got, want in pairs)).described_as(
            spec,
        ).is_true()


@pytest.mark.parametrize(
    ("spec", "npm_verdicts"),
    _NPM_TABLE,
    ids=[repr(spec) for spec, _verdicts in _NPM_TABLE],
)
def test_reader_matches_npm_semver(spec: str, npm_verdicts: str) -> None:
    """The reader agrees with npm, or is a listed conservative answer.

    Args:
        spec: Declared range.
        npm_verdicts: npm's verdict letters for each minimum.
    """
    expected = _CONSERVATIVE.get(spec, npm_verdicts.replace("E", "?"))

    got = "".join(
        _LETTER[range_admits_at_least(spec, minimum)] for minimum in _MINIMUMS
    )

    assert_that(got).is_equal_to(expected)
