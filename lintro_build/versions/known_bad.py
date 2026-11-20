"""Validate the ``known_bad_versions`` entries in ``manifest.src.json`` (#3556).

lintro refuses a tool release named on its known-bad list. An entry the
runtime cannot read would refuse nothing, so the generator rejects it before
it reaches ``manifest.json``.

Stdlib-only like the rest of the generator, so the specifier check is a
pattern for PEP 440 clauses rather than ``packaging``. It accepts the
operators lintro uses (``==``, ``!=``, ``<``, ``<=``, ``>``, ``>=``, ``~=``)
followed by a release such as ``2.7.0``, ``2.7.*`` or ``2.7.0rc1``; the
runtime parses the entry again with ``packaging`` when it loads.
"""

from __future__ import annotations

import re

from .errors import GenerationError

#: Keys every known-bad entry must have, and the only ones it may have.
KNOWN_BAD_KEYS: frozenset[str] = frozenset({"versions", "reason", "reference"})

_CLAUSE = re.compile(
    r"^\s*(?:~=|==|!=|<=|>=|<|>)\s*"
    r"v?\d+(?:\.\d+)*(?:\.\*|(?:[-_.]?(?:a|b|rc|post|dev)\d*)*)\s*$",
)


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
    if not all(_CLAUSE.match(part) for part in entry["versions"].split(",")):
        return (
            f"'versions' {entry['versions']!r} is not a PEP 440 specifier "
            "such as '==2.7.0' or '>=2.4.0,<2.6.0'"
        )
    return None


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
