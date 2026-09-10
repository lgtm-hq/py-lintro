"""Front-matter parsing for user-defined review agents (issue #1245).

Split out of :mod:`lintro.ai.review.custom_agents` (#2301). The markdown
front-matter splitter and every field validator live here; the schema types
live in :mod:`lintro.ai.review.custom_agent_types`. Both are re-exported from
:mod:`lintro.ai.review.custom_agents` for existing importers.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from lintro.ai.review.custom_agent_types import (
    CustomAgentConfigError,
    CustomAgentSpec,
)
from lintro.ai.review.enums.review_strictness import ReviewStrictness
from lintro.ai.review.finding_parser import parse_severity_label
from lintro.ai.review.models.review_finding import Severity

__all__ = [
    "parse_custom_agent",
    "split_front_matter",
]

_FRONT_MATTER_FENCE = "---"
_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_KNOWN_FIELDS: frozenset[str] = frozenset(
    {
        "name",
        "description",
        "include",
        "exclude",
        "severity",
        "strictness",
        "model",
        "enabled",
    },
)


def split_front_matter(*, text: str) -> tuple[str, str]:
    """Split a markdown document into YAML front matter and body.

    The document must open with a ``---`` fence on its first non-empty line
    and close with a matching ``---`` fence on its own line.

    Args:
        text: Full markdown file contents.

    Returns:
        Tuple of raw front-matter YAML text and the markdown body.

    Raises:
        CustomAgentConfigError: When the opening or closing fence is missing.
    """
    stripped = text.lstrip("﻿")
    lines = stripped.splitlines()
    first_content = next(
        (index for index, line in enumerate(lines) if line.strip()),
        None,
    )
    if first_content is None or lines[first_content].strip() != _FRONT_MATTER_FENCE:
        raise CustomAgentConfigError(
            field="front-matter",
            message=("file must start with a YAML front-matter block fenced by '---'"),
        )

    for index in range(first_content + 1, len(lines)):
        if lines[index].strip() == _FRONT_MATTER_FENCE:
            front_matter = "\n".join(lines[first_content + 1 : index])
            body = "\n".join(lines[index + 1 :])
            return front_matter, body

    raise CustomAgentConfigError(
        field="front-matter",
        message="front-matter block is not closed by a '---' fence",
    )


def _load_front_matter(*, front_matter: str) -> dict[str, Any]:
    """Parse the front-matter YAML into a mapping.

    Args:
        front_matter: Raw YAML text between the ``---`` fences.

    Returns:
        The parsed mapping.

    Raises:
        CustomAgentConfigError: When the YAML is invalid, empty, or not a
            mapping, or when it declares unknown fields.
    """
    try:
        parsed = yaml.safe_load(front_matter)
    except yaml.YAMLError as error:
        raise CustomAgentConfigError(
            field="front-matter",
            message=f"front matter is not valid YAML: {error}",
        ) from error

    if parsed is None:
        raise CustomAgentConfigError(
            field="front-matter",
            message="front matter is empty",
        )
    if not isinstance(parsed, dict):
        raise CustomAgentConfigError(
            field="front-matter",
            message="front matter must be a YAML mapping",
        )
    if not all(isinstance(key, str) for key in parsed):
        raise CustomAgentConfigError(
            field="front-matter",
            message="front-matter keys must be strings",
        )

    unknown = sorted(set(parsed) - _KNOWN_FIELDS)
    if unknown:
        raise CustomAgentConfigError(
            field=unknown[0],
            message=(
                f"unknown front-matter field(s): {', '.join(unknown)}; "
                f"known fields: {', '.join(sorted(_KNOWN_FIELDS))}"
            ),
        )
    return parsed


def _require_name(*, raw: object) -> str:
    """Validate the ``name`` field.

    Args:
        raw: Raw ``name`` value from front matter.

    Returns:
        The validated agent name.

    Raises:
        CustomAgentConfigError: When the name is missing or malformed.
    """
    if raw is None:
        raise CustomAgentConfigError(field="name", message="is required")
    if not isinstance(raw, str) or not _NAME_PATTERN.match(raw.strip()):
        raise CustomAgentConfigError(
            field="name",
            message=(
                "must be a short identifier of letters, digits, '.', '_' or "
                f"'-' (got {raw!r})"
            ),
        )
    return raw.strip()


def _parse_globs(*, raw: object, field: str, required: bool) -> tuple[str, ...]:
    """Validate a glob list field.

    Args:
        raw: Raw field value from front matter.
        field: Field name, used in error messages.
        required: Whether at least one glob must be present.

    Returns:
        The validated glob patterns.

    Raises:
        CustomAgentConfigError: When the value is not a list of non-empty
            strings, or is empty while required.
    """
    if raw is None:
        if required:
            raise CustomAgentConfigError(
                field=field,
                message="is required and must list at least one glob pattern",
            )
        return ()
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        raise CustomAgentConfigError(
            field=field,
            message="must be a list of glob patterns",
        )
    patterns: list[str] = []
    for entry in raw:
        if not isinstance(entry, str) or not entry.strip():
            raise CustomAgentConfigError(
                field=field,
                message=f"glob patterns must be non-empty strings (got {entry!r})",
            )
        patterns.append(entry.strip())
    if required and not patterns:
        raise CustomAgentConfigError(
            field=field,
            message="is required and must list at least one glob pattern",
        )
    return tuple(patterns)


def _parse_severity(*, raw: object) -> Severity:
    """Validate the ``severity`` field.

    Args:
        raw: Raw ``severity`` value from front matter.

    Returns:
        The resolved severity, defaulting to ``P2``.

    Raises:
        CustomAgentConfigError: When the label is not recognized.
    """
    if raw is None:
        return Severity.P2
    resolved = parse_severity_label(raw=raw)
    if resolved is None:
        raise CustomAgentConfigError(
            field="severity",
            message=(f"unknown severity {raw!r}; use P1/P2/P3 or high/medium/low"),
        )
    return resolved


def _parse_strictness(*, raw: object) -> ReviewStrictness:
    """Validate the ``strictness`` field.

    Args:
        raw: Raw ``strictness`` value from front matter.

    Returns:
        The resolved strictness preset, defaulting to ``balanced``.

    Raises:
        CustomAgentConfigError: When the preset is not recognized.
    """
    if raw is None:
        return ReviewStrictness.BALANCED
    try:
        return ReviewStrictness(str(raw).strip().lower())
    except ValueError as error:
        allowed = ", ".join(level.value for level in ReviewStrictness)
        raise CustomAgentConfigError(
            field="strictness",
            message=f"unknown strictness {raw!r}; expected one of: {allowed}",
        ) from error


def _parse_enabled(*, raw: object) -> bool:
    """Validate the ``enabled`` field.

    Args:
        raw: Raw ``enabled`` value from front matter.

    Returns:
        The resolved flag, defaulting to True.

    Raises:
        CustomAgentConfigError: When the value is not a boolean.
    """
    if raw is None:
        return True
    if not isinstance(raw, bool):
        raise CustomAgentConfigError(
            field="enabled",
            message=f"must be a boolean (got {raw!r})",
        )
    return raw


def _parse_model(*, raw: object) -> str | None:
    """Validate the optional ``model`` field.

    ``default`` is accepted as an explicit "use the configured model" spelling
    and normalizes to no override.

    Args:
        raw: Raw ``model`` value from front matter.

    Returns:
        The model override, or None when the configured model should be used.

    Raises:
        CustomAgentConfigError: When the value is not a non-empty string.
    """
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise CustomAgentConfigError(
            field="model",
            message=f"must be a non-empty model identifier (got {raw!r})",
        )
    model = raw.strip()
    return None if model.lower() == "default" else model


def _parse_description(*, raw: object) -> str:
    """Validate the optional ``description`` field.

    Args:
        raw: Raw ``description`` value from front matter.

    Returns:
        The description text, empty when unset.

    Raises:
        CustomAgentConfigError: When the value is not a string.
    """
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise CustomAgentConfigError(
            field="description",
            message=f"must be a string (got {raw!r})",
        )
    return " ".join(raw.split())


def parse_custom_agent(*, path: Path, text: str) -> CustomAgentSpec:
    """Parse and validate one custom review agent markdown file.

    Args:
        path: Absolute path to the markdown file (used for reporting).
        text: Full file contents.

    Returns:
        The validated agent specification.

    Raises:
        CustomAgentConfigError: When the front matter or body is invalid. The
            raised error names the offending field.
    """
    front_matter, body = split_front_matter(text=text)
    data = _load_front_matter(front_matter=front_matter)

    if not body.strip():
        raise CustomAgentConfigError(
            field="body",
            message="markdown body must contain the review instruction prose",
        )

    return CustomAgentSpec(
        name=_require_name(raw=data.get("name")),
        description=_parse_description(raw=data.get("description")),
        include=_parse_globs(raw=data.get("include"), field="include", required=True),
        exclude=_parse_globs(raw=data.get("exclude"), field="exclude", required=False),
        severity=_parse_severity(raw=data.get("severity")),
        strictness=_parse_strictness(raw=data.get("strictness")),
        model=_parse_model(raw=data.get("model")),
        enabled=_parse_enabled(raw=data.get("enabled")),
        body=body.strip(),
        path=path,
    )
