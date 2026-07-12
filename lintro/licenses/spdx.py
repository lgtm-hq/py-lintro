"""SPDX license identifier normalization and license category sets.

This module provides a curated, offline mapping from common raw license
strings (as found in package metadata across ecosystems) to canonical SPDX
identifiers, plus category sets used by the policy presets. It intentionally
avoids a network dependency so license checks are deterministic in CI.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum, auto

# Canonical SPDX identifiers that are broadly considered "permissive".
PERMISSIVE_LICENSES: frozenset[str] = frozenset(
    {
        "0BSD",
        "Apache-2.0",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "CC0-1.0",
        "ISC",
        "MIT",
        "MIT-0",
        "PSF-2.0",
        "Python-2.0",
        "Unlicense",
        "Zlib",
    },
)

# Weak copyleft licenses (file/library scoped) — allowed by "copyleft-ok".
WEAK_COPYLEFT_LICENSES: frozenset[str] = frozenset(
    {
        "LGPL-2.1-only",
        "LGPL-2.1-or-later",
        "LGPL-3.0-only",
        "LGPL-3.0-or-later",
        "MPL-2.0",
        "EPL-2.0",
        "CDDL-1.0",
    },
)

# Strong copyleft licenses (project scoped) — denied by most presets.
STRONG_COPYLEFT_LICENSES: frozenset[str] = frozenset(
    {
        "GPL-2.0-only",
        "GPL-2.0-or-later",
        "GPL-3.0-only",
        "GPL-3.0-or-later",
        "AGPL-3.0-only",
        "AGPL-3.0-or-later",
    },
)

# Non-open-source / source-available licenses — denied by default.
RESTRICTED_LICENSES: frozenset[str] = frozenset(
    {
        "SSPL-1.0",
        "BUSL-1.1",
        "Elastic-2.0",
        "Commons-Clause",
    },
)

# Sentinel used when a package declares "no license".
NO_LICENSE_MARKERS: frozenset[str] = frozenset(
    {
        "unlicensed",
        "none",
        "proprietary",
        "nolicense",
    },
)

# Raw string (lower-cased, normalized) -> SPDX identifier.
# Covers the most common metadata spellings across PyPI and npm.
_ALIASES: dict[str, str] = {
    "mit": "MIT",
    "mit license": "MIT",
    "mit-0": "MIT-0",
    "expat": "MIT",
    "apache": "Apache-2.0",
    "apache 2": "Apache-2.0",
    "apache 2.0": "Apache-2.0",
    "apache-2": "Apache-2.0",
    "apache-2.0": "Apache-2.0",
    "apache license 2.0": "Apache-2.0",
    "apache license, version 2.0": "Apache-2.0",
    "apache-2.0 license": "Apache-2.0",
    "apache software license": "Apache-2.0",
    "asl 2.0": "Apache-2.0",
    "bsd": "BSD-3-Clause",
    "bsd license": "BSD-3-Clause",
    "bsd-2": "BSD-2-Clause",
    "bsd-2-clause": "BSD-2-Clause",
    "bsd 2-clause": "BSD-2-Clause",
    "bsd-3": "BSD-3-Clause",
    "bsd-3-clause": "BSD-3-Clause",
    "bsd 3-clause": "BSD-3-Clause",
    "new bsd": "BSD-3-Clause",
    "isc": "ISC",
    "isc license": "ISC",
    "0bsd": "0BSD",
    "zero-clause bsd": "0BSD",
    "zlib": "Zlib",
    "cc0": "CC0-1.0",
    "cc0-1.0": "CC0-1.0",
    "cc0 1.0 universal": "CC0-1.0",
    "unlicense": "Unlicense",
    "the unlicense": "Unlicense",
    "psf": "PSF-2.0",
    "psf-2.0": "PSF-2.0",
    "psfl": "PSF-2.0",
    "python software foundation license": "PSF-2.0",
    "python-2.0": "Python-2.0",
    "python 2.0": "Python-2.0",
    "mpl": "MPL-2.0",
    "mpl-2.0": "MPL-2.0",
    "mpl 2.0": "MPL-2.0",
    "mozilla public license 2.0": "MPL-2.0",
    "mozilla public license 2.0 (mpl 2.0)": "MPL-2.0",
    "epl-2.0": "EPL-2.0",
    "eclipse public license 2.0": "EPL-2.0",
    "cddl-1.0": "CDDL-1.0",
    "lgpl": "LGPL-3.0-or-later",
    "lgpl-2.1": "LGPL-2.1-only",
    "lgpl-2.1-only": "LGPL-2.1-only",
    "lgpl-2.1-or-later": "LGPL-2.1-or-later",
    "lgpl-3.0": "LGPL-3.0-only",
    "lgpl-3.0-only": "LGPL-3.0-only",
    "lgpl-3.0-or-later": "LGPL-3.0-or-later",
    "lgplv3": "LGPL-3.0-only",
    "gpl": "GPL-3.0-or-later",
    "gplv2": "GPL-2.0-only",
    "gpl-2.0": "GPL-2.0-only",
    "gpl-2.0-only": "GPL-2.0-only",
    "gpl-2.0+": "GPL-2.0-or-later",
    "gpl-2.0-or-later": "GPL-2.0-or-later",
    "gplv3": "GPL-3.0-only",
    "gpl-3.0": "GPL-3.0-only",
    "gpl-3.0-only": "GPL-3.0-only",
    "gpl-3.0+": "GPL-3.0-or-later",
    "gpl-3.0-or-later": "GPL-3.0-or-later",
    "gnu general public license v3": "GPL-3.0-only",
    "agpl": "AGPL-3.0-or-later",
    "agpl-3.0": "AGPL-3.0-only",
    "agpl-3.0-only": "AGPL-3.0-only",
    "agpl-3.0-or-later": "AGPL-3.0-or-later",
    "sspl-1.0": "SSPL-1.0",
    "busl-1.1": "BUSL-1.1",
    "elastic-2.0": "Elastic-2.0",
}

# Canonical SPDX ids we recognize directly (case-insensitive match).
_KNOWN_SPDX: frozenset[str] = (
    PERMISSIVE_LICENSES
    | WEAK_COPYLEFT_LICENSES
    | STRONG_COPYLEFT_LICENSES
    | RESTRICTED_LICENSES
)

_SPDX_BY_LOWER: dict[str, str] = {spdx.lower(): spdx for spdx in _KNOWN_SPDX}

_HAS_AND = re.compile(r"\band\b", re.IGNORECASE)
_HAS_OR = re.compile(r"\bor\b", re.IGNORECASE)

# Prefer these when collapsing AND expressions so denials are not dropped.
_RESTRICTIVE_FOR_AND: frozenset[str] = (
    STRONG_COPYLEFT_LICENSES | WEAK_COPYLEFT_LICENSES | RESTRICTED_LICENSES
)

_TOKEN_RE = re.compile(r"\(|\)|\bAND\b|\bOR\b", re.IGNORECASE)


class _TokenKind(StrEnum):
    """Token kinds for SPDX expression parsing."""

    LPAREN = auto()
    RPAREN = auto()
    AND = auto()
    OR = auto()
    ATOM = auto()
    EOF = auto()


@dataclass(frozen=True)
class _Token:
    """A single SPDX expression token."""

    kind: _TokenKind
    value: str = ""


@dataclass(frozen=True)
class _LicenseAtom:
    """Leaf license identifier in an SPDX expression."""

    raw: str


@dataclass(frozen=True)
class _AndNode:
    """AND conjunction of SPDX sub-expressions."""

    children: tuple[_ExprNode, ...]


@dataclass(frozen=True)
class _OrNode:
    """OR disjunction of SPDX sub-expressions."""

    children: tuple[_ExprNode, ...]


_ExprNode = _LicenseAtom | _AndNode | _OrNode


def _clean(raw: str) -> str:
    """Lower-case and collapse whitespace/punctuation noise in a license string.

    Args:
        raw: Raw license string.

    Returns:
        str: Normalized comparison key.
    """
    value = raw.strip().strip("()").strip()
    value = re.sub(r"\s+", " ", value)
    return value.lower()


def _normalize_atom(raw: str) -> str | None:
    """Normalize a single license operand without expression parsing.

    Args:
        raw: Raw license operand text.

    Returns:
        str | None: Canonical SPDX identifier when recognized.
    """
    cleaned = _clean(raw)
    if not cleaned or cleaned in NO_LICENSE_MARKERS:
        return None
    if cleaned in _SPDX_BY_LOWER:
        return _SPDX_BY_LOWER[cleaned]
    if cleaned in _ALIASES:
        return _ALIASES[cleaned]
    return None


def _tokenize(expression: str) -> list[_Token]:
    """Tokenize an SPDX license expression.

    Args:
        expression: Cleaned SPDX expression text.

    Returns:
        list[_Token]: Token stream terminated by EOF.
    """
    tokens: list[_Token] = []
    cursor = 0
    for match in _TOKEN_RE.finditer(expression):
        atom = expression[cursor : match.start()].strip()
        if atom:
            tokens.append(_Token(kind=_TokenKind.ATOM, value=atom))
        token = match.group(0)
        if len(token) == 1 and ord(token) == 40:
            tokens.append(_Token(kind=_TokenKind.LPAREN))
        elif len(token) == 1 and ord(token) == 41:
            tokens.append(_Token(kind=_TokenKind.RPAREN))
        elif token.upper() == "AND":
            tokens.append(_Token(kind=_TokenKind.AND))
        else:
            tokens.append(_Token(kind=_TokenKind.OR))
        cursor = match.end()
    trailing = expression[cursor:].strip()
    if trailing:
        tokens.append(_Token(kind=_TokenKind.ATOM, value=trailing))
    tokens.append(_Token(kind=_TokenKind.EOF))
    return tokens


class _SpdxExpressionParser:
    """Recursive-descent parser for SPDX license expressions."""

    def __init__(self, tokens: list[_Token]) -> None:
        """Initialize the parser.

        Args:
            tokens: Token stream produced by ``_tokenize``.
        """
        self._tokens = tokens
        self._index = 0

    def parse(self) -> _ExprNode | None:
        """Parse the token stream into an expression tree.

        Returns:
            _ExprNode | None: Parsed expression, or None when empty.
        """
        if self._current().kind is _TokenKind.EOF:
            return None
        return self._parse_or()

    def _current(self) -> _Token:
        """Return the current token.

        Returns:
            _Token: Token at the parser cursor.
        """
        return self._tokens[self._index]

    def _advance(self) -> _Token:
        """Consume and return the current token.

        Returns:
            _Token: Token that was consumed.
        """
        token = self._current()
        self._index += 1
        return token

    def _parse_or(self) -> _ExprNode:
        """Parse an OR expression (lowest precedence).

        Returns:
            _ExprNode: Parsed OR node or a single lower-precedence node.
        """
        children = [self._parse_and()]
        while self._current().kind is _TokenKind.OR:
            self._advance()
            children.append(self._parse_and())
        if len(children) == 1:
            return children[0]
        return _OrNode(children=tuple(children))

    def _parse_and(self) -> _ExprNode:
        """Parse an AND expression (higher precedence than OR).

        Returns:
            _ExprNode: Parsed AND node or a single primary node.
        """
        children = [self._parse_primary()]
        while self._current().kind is _TokenKind.AND:
            self._advance()
            children.append(self._parse_primary())
        if len(children) == 1:
            return children[0]
        return _AndNode(children=tuple(children))

    def _parse_primary(self) -> _ExprNode:
        """Parse a parenthesized sub-expression or license atom.

        Returns:
            _ExprNode: Parsed primary expression.

        Raises:
            ValueError: When the token stream is malformed.
        """
        token = self._current()
        if token.kind is _TokenKind.LPAREN:
            self._advance()
            node = self._parse_or()
            if self._current().kind is not _TokenKind.RPAREN:
                msg = "Expected ')' after grouped SPDX expression"
                raise ValueError(msg)
            self._advance()
            return node
        if token.kind is _TokenKind.ATOM:
            self._advance()
            return _LicenseAtom(raw=token.value)
        msg = f"Unexpected token in SPDX expression: {token.kind}"
        raise ValueError(msg)


def _parse_expression(expression: str) -> _ExprNode | None:
    """Parse a cleaned SPDX expression into an AST.

    Args:
        expression: Cleaned SPDX expression text.

    Returns:
        _ExprNode | None: Parsed expression tree, or None when empty or invalid.
    """
    parser = _SpdxExpressionParser(tokens=_tokenize(expression))
    node = parser.parse()
    if node is not None and parser._current().kind is not _TokenKind.EOF:
        return None
    return node


def _normalize_and_node(node: _AndNode) -> str | None:
    """Collapse an AND node to a single SPDX identifier.

    Every operand is normalized. When any operand maps to a restrictive
    license class, that identifier is returned so deny policies cannot be
    bypassed by a permissive conjunct.

    Args:
        node: AND expression node.

    Returns:
        str | None: Collapsed SPDX identifier, or None when unrecognized.
    """
    resolved_ids: list[str] = []
    for child in node.children:
        resolved = _normalize_expr(child)
        if resolved is not None:
            resolved_ids.append(resolved)
    if not resolved_ids:
        return None
    for spdx_id in resolved_ids:
        if spdx_id in _RESTRICTIVE_FOR_AND:
            return spdx_id
    return resolved_ids[0]


def _normalize_or_node(node: _OrNode) -> str | None:
    """Collapse an OR node to the first recognized SPDX identifier.

    Args:
        node: OR expression node.

    Returns:
        str | None: Collapsed SPDX identifier, or None when unrecognized.
    """
    for child in node.children:
        resolved = _normalize_expr(child)
        if resolved is not None:
            return resolved
    return None


def _normalize_expr(node: _ExprNode) -> str | None:
    """Normalize a parsed SPDX expression node.

    Args:
        node: Parsed expression subtree.

    Returns:
        str | None: Canonical SPDX identifier when recognized.
    """
    if isinstance(node, _LicenseAtom):
        return _normalize_atom(node.raw)
    if isinstance(node, _AndNode):
        return _normalize_and_node(node)
    return _normalize_or_node(node)


def normalize_to_spdx(license_string: str | None) -> str | None:
    """Normalize an arbitrary license string to a canonical SPDX identifier.

    Handles direct SPDX identifiers, common metadata aliases, and SPDX
    expressions. ``AND`` binds tighter than ``OR``. ``AND`` expressions
    resolve every operand and prefer a restrictive / denied-class license so
    a conjunction cannot collapse to only the permissive side (e.g.
    ``MIT AND GPL-3.0-only`` → ``GPL-3.0-only``). ``OR`` expressions select
    the first recognized operand, evaluating each branch with the precedence
    rules above (e.g. ``MIT OR Apache-2.0 AND GPL-3.0`` → ``MIT``;
    ``Apache-2.0 AND GPL-3.0 OR MIT`` → ``GPL-3.0-only``).

    Args:
        license_string: Raw license string from package metadata, or None.

    Returns:
        str | None: The SPDX identifier if recognized, otherwise None.
    """
    if not license_string:
        return None

    cleaned = _clean(license_string)
    if not cleaned or cleaned in NO_LICENSE_MARKERS:
        return None

    if cleaned in _SPDX_BY_LOWER:
        return _SPDX_BY_LOWER[cleaned]

    if cleaned in _ALIASES:
        return _ALIASES[cleaned]

    if not _HAS_AND.search(cleaned) and not _HAS_OR.search(cleaned):
        return None

    try:
        parsed = _parse_expression(cleaned)
    except ValueError:
        return None
    if parsed is None:
        return None
    return _normalize_expr(parsed)
