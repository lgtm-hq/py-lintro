"""Classify the dependency spec a ``package.json`` declares (#2045).

The npm install strategy has to decide whether a declared spec disagrees with
lintro's recommended version. It used to strip a *set* of characters
(``lstrip("^~=v ")``) and compare the remainder with the recommendation, so
every range and every non-version form counted as a conflict. A project that
declared ``workspace:*`` was told it "declares workspace:* but lintro
recommends 3.9.4".

Classification is by prefix and shape only. lintro does not resolve semver
ranges itself; the project's package manager does that.
"""

from __future__ import annotations

import re

from lintro.enums.declared_spec_kind import DeclaredSpecKind

#: Prefixes an exact-compatible spec may carry. At most one is stripped.
_EXACT_PREFIXES: tuple[str, ...] = ("^", "~", "=", "v")

#: A complete ``MAJOR.MINOR.PATCH`` version, optionally with a prerelease and
#: build suffix.
_FULL_VERSION = re.compile(
    r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?",
)

#: One node-semver comparator: an optional operator and a version that may be
#: partial (``3``, ``3.9``) or use ``x``/``X``/``*`` wildcards.
_COMPARATOR = re.compile(
    r"(?:<=|>=|<|>|=|~>|~|\^)?v?"
    r"(?:\d+|[xX*])(?:\.(?:\d+|[xX*])){0,2}"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?",
)

#: The separator of a hyphen range (``1.2.3 - 2.0.0``).
_HYPHEN = "-"

#: An operator separated from its version by whitespace (``>= 3.9.4``).
_DETACHED_OPERATOR = re.compile(r"(<=|>=|<|>|=|~>|~|\^)\s+")

#: Opaque forms, checked in order, with the wording used to name them.
_OPAQUE_FORMS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("workspace:",), "the workspace protocol"),
    (("npm:",), "an npm alias"),
    (("catalog:",), "a pnpm catalog"),
    (
        ("git+", "git:", "git@", "github:", "gitlab:", "bitbucket:", "gist:"),
        "a git dependency",
    ),
    (("http://", "https://"), "a tarball URL"),
    (("file:", "link:", "portal:", ".", "/", "~/"), "a local path"),
)


def classify_declared_spec(spec: str) -> DeclaredSpecKind:
    """Classify a declared dependency spec by its shape.

    Args:
        spec: The spec exactly as written in ``package.json``.

    Returns:
        :attr:`DeclaredSpecKind.EXACT` for a full version with at most one
        ``^ ~ = v`` prefix, :attr:`DeclaredSpecKind.RANGE` for any other
        node-semver range, and :attr:`DeclaredSpecKind.OPAQUE` for everything
        else.
    """
    if exact_version(spec) is not None:
        return DeclaredSpecKind.EXACT
    if _is_range(spec.strip()):
        return DeclaredSpecKind.RANGE
    return DeclaredSpecKind.OPAQUE


def exact_version(spec: str) -> str | None:
    """Return the version an exact-compatible spec names.

    Exactly one leading ``^``, ``~``, ``=`` or ``v`` is removed, so ``v3.9.4``
    names ``3.9.4`` but ``=v3.9.4`` and ``^^3.9.4`` are not exact-compatible.

    Args:
        spec: The declared spec.

    Returns:
        The bare version, or None when the spec is not exact-compatible.
    """
    candidate = spec.strip()
    if candidate.startswith(_EXACT_PREFIXES):
        candidate = candidate[1:].strip()
    return candidate if _FULL_VERSION.fullmatch(candidate) else None


def describe_opaque_spec(spec: str) -> str:
    """Name the form of an opaque spec for a user-facing message.

    Args:
        spec: A spec classified as :attr:`DeclaredSpecKind.OPAQUE`.

    Returns:
        A short noun phrase such as ``"the workspace protocol"``.
    """
    candidate = spec.strip()
    lowered = candidate.lower()
    for prefixes, label in _OPAQUE_FORMS:
        if lowered.startswith(prefixes):
            return label
    if "/" in candidate:
        # ``owner/repo`` and ``owner/repo#ref`` are GitHub shorthand.
        return "a git dependency"
    return "a dist-tag or other non-version spec"


def _is_range(spec: str) -> bool:
    """Report whether *spec* is a node-semver range.

    Args:
        spec: Stripped spec text.

    Returns:
        True when every ``||`` alternative is made of comparators, optionally
        joined as a hyphen range. An empty spec means any version to npm.
    """
    for alternative in spec.split("||"):
        words = _DETACHED_OPERATOR.sub(r"\1", alternative.strip()).split()
        for position, word in enumerate(words):
            if word == _HYPHEN and 0 < position < len(words) - 1:
                continue
            if _COMPARATOR.fullmatch(word) is None:
                return False
    return True
