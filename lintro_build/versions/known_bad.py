"""Validate the ``known_bad_versions`` entries in ``manifest.src.json`` (#3556).

lintro refuses a tool release named on its known-bad list. An entry the
runtime cannot read would refuse nothing, so the generator rejects it before
it reaches ``manifest.json``.

``versions`` uses one deliberately small grammar, enforced here and again by
the runtime (``lintro.tools.core.manifest_models``) before it hands the
string to ``packaging``: comma-separated clauses, each ``==``, ``!=``, ``<``,
``<=``, ``>`` or ``>=`` followed by a plain numeric release ``N(.N)*``, with a
trailing ``.*`` allowed only after ``==`` and ``!=``. Nothing else: no
``~=``, no pre-release, post-release, epoch or local parts. Every string the
grammar accepts is a valid ``packaging`` specifier, so a generated manifest
never holds an entry the runtime rejects. The generator is stdlib-only, so
the check is a pattern.
"""

from __future__ import annotations

import re

from .errors import GenerationError

#: Keys every known-bad entry must have, and the only ones it may have.
KNOWN_BAD_KEYS: frozenset[str] = frozenset({"versions", "reason", "reference"})

#: One clause of the known-bad grammar; keep in step with
#: ``lintro.tools.core.manifest_models.KNOWN_BAD_CLAUSE``.
_CLAUSE = re.compile(
    r" *(?:(?:==|!=) *[0-9]+(?:\.[0-9]+)*(?:\.\*)?"
    r"|(?:<=|>=|<|>) *[0-9]+(?:\.[0-9]+)*) *",
)

#: The grammar in words, for the error message and the docs.
GRAMMAR = (
    "comma-separated clauses, each ==, !=, <, <=, > or >= followed by a "
    "plain numeric release such as 2.7.0, with a trailing .* only after == "
    "or != (e.g. '==2.7.0', '>=2.4.0,<2.6.0', '==2.7.*'); no ~=, "
    "pre-release, post-release, epoch or local parts"
)


def versions_problem(versions: str) -> str | None:
    """Check a ``versions`` string against the known-bad grammar.

    Args:
        versions: The entry's ``versions`` value.

    Returns:
        Why it is rejected, or None when it is accepted.
    """
    if all(_CLAUSE.fullmatch(part) for part in versions.split(",")):
        return None
    return f"'versions' {versions!r} must be {GRAMMAR}"


def _entry_problem(entry: object) -> str | None:
    """Say what is wrong with one known-bad entry, or None when it is valid.

    Args:
        entry: One item of a tool's ``known_bad_versions`` list.

    Returns:
        A short description of the defect, or None.
    """
    if not isinstance(entry, dict):
        return "an entry must be an object"
    keys = set(entry)
    if keys != KNOWN_BAD_KEYS:
        missing = sorted(KNOWN_BAD_KEYS - keys)
        unknown = sorted(keys - KNOWN_BAD_KEYS)
        return f"missing keys {missing}, unknown keys {unknown}"
    for key in sorted(KNOWN_BAD_KEYS):
        value = entry[key]
        if not isinstance(value, str) or not value.strip():
            return f"{key!r} must be a non-empty string"
    return versions_problem(entry["versions"])


def validate_known_bad_versions(manifest_data: object) -> None:
    """Reject a ``known_bad_versions`` value the runtime could not apply.

    Args:
        manifest_data: Parsed ``manifest.src.json``; anything but an object
            with a ``tools`` list is left to the other checks.

    Raises:
        GenerationError: Naming the tool and entry, for a non-list value or
            any malformed entry.
    """
    tools = manifest_data.get("tools") if isinstance(manifest_data, dict) else None
    for tool in tools if isinstance(tools, list) else []:
        if not isinstance(tool, dict) or "known_bad_versions" not in tool:
            continue
        name = tool.get("name", "<unnamed>")
        entries = tool["known_bad_versions"]
        if not isinstance(entries, list):
            raise GenerationError(
                f"manifest.src.json tool {name!r}: known_bad_versions must be "
                f"a list, got {type(entries).__name__}",
            )
        for entry in entries:
            problem = _entry_problem(entry)
            if problem is not None:
                raise GenerationError(
                    f"manifest.src.json tool {name!r}: invalid "
                    f"known_bad_versions entry {entry!r}: {problem}",
                )
