"""Loaders for the committed matrix and corpus files.

Both files may be written as YAML or JSON; YAML is a superset of JSON, so one
parser reads either and the extension only decides nothing.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from lintro.ai.review.models.review_finding import Severity
from review_matrix.models.corpus import Corpus, CorpusItem, LabeledFinding
from review_matrix.models.matrix import MatrixConfig, MatrixSpec

__all__ = [
    "SAFE_ID_PATTERN",
    "SpecError",
    "load_corpus",
    "load_matrix",
    "parse_corpus",
    "parse_matrix",
]

#: Config and corpus ids become path segments under the run directory, so
#: they must be a single safe segment: no separator, no traversal, not
#: absolute, and never a leading dot.
SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

#: ``lintro review --depth`` is ``click.IntRange(1, 3)``, so a matrix that
#: asked for anything else would parse cleanly and then fail every cell at
#: invoke time. Bounded here so the failure is one spec error, not N runs.
MIN_DEPTH = 1
MAX_DEPTH = 3

DEFAULT_DEPTH = 1
#: ``--timeout`` overrides ``ai.api_timeout``, which sits above the
#: built-in per-transport default (api 60s, cli 1800s). A smaller value
#: would *lower* the CLI per-chunk budget that scripts/ci/run-ai-review.sh
#: keeps at 1800s so a large chunk can finish, so the harness default
#: matches it rather than undercutting it.
DEFAULT_TIMEOUT_SECONDS = 1800.0
DEFAULT_REPEATS = 3


class SpecError(ValueError):
    """Raised when a matrix or corpus file cannot be read as specified."""


def _load_document(path: Path) -> Mapping[str, Any]:
    """Parse a YAML or JSON document from disk.

    Args:
        path: File to read.

    Returns:
        The decoded top-level mapping.

    Raises:
        SpecError: When the file is missing, unreadable, undecodable,
            unparseable, or not a mapping. Every failure mode is funnelled
            into one exception type so the CLI can report it as an error and
            exit 2 rather than traceback.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise SpecError(f"cannot read {path}: {exc}") from exc
    try:
        data = yaml.safe_load(text)
    except (yaml.YAMLError, ValueError) as exc:
        raise SpecError(f"cannot parse {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SpecError(f"{path} must contain a top-level mapping")
    return data


def _require_str(mapping: Mapping[str, Any], key: str, *, where: str) -> str:
    """Read a non-empty string field.

    Args:
        mapping: Mapping to read from.
        key: Field name.
        where: Human-readable location used in the error message.

    Returns:
        The stripped field value.

    Raises:
        SpecError: When the field is absent, empty, or not a string. A list or
            mapping would otherwise be stringified into a nonsense provider or
            model name that only fails much later, at the provider.
    """
    raw = mapping.get(key)
    if raw is None:
        raise SpecError(f"{where}: '{key}' is required")
    if not isinstance(raw, str):
        raise SpecError(f"{where}: '{key}' must be a string")
    value = raw.strip()
    if not value:
        raise SpecError(f"{where}: '{key}' is required")
    return value


def _require_safe_id(value: str, *, where: str) -> str:
    """Validate an id that will be used as an output path segment.

    Args:
        value: Candidate id.
        where: Human-readable location used in the error message.

    Returns:
        The id unchanged.

    Raises:
        SpecError: When the id is not a single safe path segment.
    """
    if not SAFE_ID_PATTERN.fullmatch(value):
        raise SpecError(
            f"{where}: 'id' must be a single path segment matching "
            f"{SAFE_ID_PATTERN.pattern} (got {value!r})",
        )
    return value


def parse_matrix(document: Mapping[str, Any]) -> MatrixSpec:
    """Build a matrix specification from a decoded document.

    Args:
        document: Decoded matrix mapping.

    Returns:
        The parsed specification.

    Raises:
        SpecError: When the document is malformed or defines no configs.
    """
    raw_configs = document.get("configs")
    if not isinstance(raw_configs, list) or not raw_configs:
        raise SpecError("matrix: 'configs' must be a non-empty list")
    configs: list[MatrixConfig] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_configs):
        if not isinstance(raw, dict):
            raise SpecError(f"matrix: config #{index + 1} must be a mapping")
        where = f"matrix config #{index + 1}"
        config_id = _require_safe_id(
            _require_str(raw, "id", where=where),
            where=where,
        )
        if config_id in seen:
            raise SpecError(f"matrix: duplicate config id '{config_id}'")
        seen.add(config_id)
        max_cost = _positive_float(
            raw.get("max_cost_usd"),
            where=where,
            key="max_cost_usd",
        )
        projected_raw = raw.get("projected_cost_usd")
        projected = (
            max_cost
            if projected_raw is None
            else _positive_float(projected_raw, where=where, key="projected_cost_usd")
        )
        configs.append(
            MatrixConfig(
                config_id=config_id,
                provider=_require_str(raw, "provider", where=where),
                model=_require_str(raw, "model", where=where),
                transport=_require_str(raw, "transport", where=where),
                max_cost_usd=max_cost,
                projected_cost_usd=projected,
            ),
        )
    repeats = _positive_int(
        document.get("repeats", DEFAULT_REPEATS),
        where="matrix",
        key="repeats",
    )
    return MatrixSpec(
        version=_positive_int(
            document.get("version", 1),
            where="matrix",
            key="version",
        ),
        repeats=repeats,
        depth=_bounded_depth(document.get("depth", DEFAULT_DEPTH)),
        timeout_seconds=_positive_float(
            document.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS),
            where="matrix",
            key="timeout_seconds",
        ),
        configs=tuple(configs),
    )


def parse_corpus(document: Mapping[str, Any]) -> Corpus:
    """Build a corpus from a decoded document.

    Args:
        document: Decoded corpus mapping.

    Returns:
        The parsed corpus.

    Raises:
        SpecError: When the document is malformed or defines no items.
    """
    raw_items = document.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise SpecError("corpus: 'items' must be a non-empty list")
    default_repo = (
        ""
        if document.get("repo") is None
        else _require_str(document, "repo", where="corpus")
    )
    items: list[CorpusItem] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            raise SpecError(f"corpus: item #{index + 1} must be a mapping")
        where = f"corpus item #{index + 1}"
        item_id = _require_safe_id(
            _require_str(raw, "id", where=where),
            where=where,
        )
        if item_id in seen:
            raise SpecError(f"corpus: duplicate item id '{item_id}'")
        seen.add(item_id)
        repo = (
            default_repo
            if raw.get("repo") is None
            else _require_str(raw, "repo", where=where)
        )
        if not repo:
            raise SpecError(f"{where}: 'repo' is required (no corpus-level default)")
        items.append(
            CorpusItem(
                item_id=item_id,
                repo=repo,
                pr=_positive_int(raw.get("pr"), where=where, key="pr"),
                title=(
                    ""
                    if raw.get("title") is None
                    else _require_str(raw, "title", where=where)
                ),
                labeled_findings=_parse_labels(
                    raw.get("expected_findings"),
                    where=where,
                ),
            ),
        )
    return Corpus(
        version=_positive_int(
            document.get("version", 1),
            where="corpus",
            key="version",
        ),
        items=tuple(items),
    )


def _parse_labels(value: Any, *, where: str) -> tuple[LabeledFinding, ...]:
    """Parse a corpus item's ground-truth labels.

    Args:
        value: Raw ``expected_findings`` value.
        where: Human-readable location used in error messages.

    Returns:
        The parsed labels; empty when the item declares none.

    Raises:
        SpecError: When the labels are malformed.
    """
    if value is None:
        return ()
    if not isinstance(value, list):
        raise SpecError(f"{where}: 'expected_findings' must be a list")
    labels: list[LabeledFinding] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, dict):
            raise SpecError(f"{where}: label #{index + 1} must be a mapping")
        label_where = f"{where} label #{index + 1}"
        # Severity is required, never defaulted: a label's severity is what
        # the expected verdict is derived from, so a forgotten P1 silently
        # stored as P2 would move the verdict without anyone noticing.
        severity_raw = _require_str(raw, "severity", where=label_where).upper()
        try:
            severity = Severity(severity_raw)
        except ValueError as exc:
            raise SpecError(
                f"{label_where}: unknown severity '{severity_raw}'",
            ) from exc
        labels.append(
            LabeledFinding(
                file=_require_str(raw, "file", where=label_where),
                category=_require_str(raw, "category", where=label_where),
                title=_require_str(raw, "title", where=label_where),
                severity=severity,
            ),
        )
    return tuple(labels)


def _bounded_depth(value: Any) -> int:
    """Read the shared review depth, bounded to what the CLI accepts.

    Args:
        value: Raw ``depth`` value.

    Returns:
        The parsed depth.

    Raises:
        SpecError: When the depth is not an integer in ``[1, 3]``.
    """
    depth = _positive_int(value, where="matrix", key="depth")
    if depth > MAX_DEPTH:
        raise SpecError(
            f"matrix: 'depth' must be between {MIN_DEPTH} and {MAX_DEPTH} "
            f"(lintro review --depth accepts no more)",
        )
    return depth


def _positive_int(value: Any, *, where: str, key: str) -> int:
    """Read a strictly positive integer field.

    Args:
        value: Raw field value.
        where: Human-readable location used in the error message.
        key: Field name.

    Returns:
        The parsed integer.

    Raises:
        SpecError: When the value is not a positive integer. Booleans, NaN,
            infinities and non-integral floats are rejected rather than
            silently coerced.
    """
    if isinstance(value, bool):
        raise SpecError(f"{where}: '{key}' must be an integer")
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        raise SpecError(f"{where}: '{key}' must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise SpecError(f"{where}: '{key}' must be an integer") from exc
    if isinstance(value, float) and parsed != value:
        raise SpecError(f"{where}: '{key}' must be an integer")
    if parsed <= 0:
        raise SpecError(f"{where}: '{key}' must be positive")
    return parsed


def _positive_float(value: Any, *, where: str, key: str) -> float:
    """Read a strictly positive float field.

    Args:
        value: Raw field value.
        where: Human-readable location used in the error message.
        key: Field name.

    Returns:
        The parsed float.

    Raises:
        SpecError: When the value is not a positive number. Booleans, NaN and
            infinities are rejected rather than silently accepted.
    """
    if isinstance(value, bool):
        raise SpecError(f"{where}: '{key}' must be a number")
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise SpecError(f"{where}: '{key}' must be a number") from exc
    if math.isnan(parsed) or math.isinf(parsed):
        raise SpecError(f"{where}: '{key}' must be a number")
    if parsed <= 0:
        raise SpecError(f"{where}: '{key}' must be positive")
    return parsed


def load_matrix(path: Path) -> MatrixSpec:
    """Load a matrix specification from a YAML or JSON file.

    Args:
        path: Matrix file path.

    Returns:
        The parsed specification.
    """
    return parse_matrix(_load_document(path))


def load_corpus(path: Path) -> Corpus:
    """Load a corpus from a YAML or JSON file.

    Args:
        path: Corpus file path.

    Returns:
        The parsed corpus.
    """
    return parse_corpus(_load_document(path))
