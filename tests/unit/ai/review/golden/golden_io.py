"""Plain-file golden comparison for the AI review characterization suite.

Deliberately dependency-free (issue #2298): no snapshot library, no
auto-approval. A golden is a file on disk, the comparison is a string
comparison, and rewriting one is an explicit opt-in via the
``LINTRO_UPDATE_GOLDENS`` environment variable::

    LINTRO_UPDATE_GOLDENS=1 uv run pytest tests/unit/ai/review/golden

Every golden in ``snapshots/`` was produced that way against the code on
``main``; they are the behaviour baseline for the #1972 decomposition
(#2299-#2302), so a diff here means production behaviour moved.
"""

from __future__ import annotations

import difflib
import json
import os
from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

#: Environment switch that rewrites goldens instead of comparing them.
UPDATE_GOLDENS_ENV: str = "LINTRO_UPDATE_GOLDENS"

#: Directory holding the checked-in snapshot files.
SNAPSHOT_DIR: Path = Path(__file__).resolve().parent / "snapshots"

#: Directory holding the fixed provider response payloads.
PAYLOAD_DIR: Path = Path(__file__).resolve().parent / "payloads"


def goldens_are_being_updated() -> bool:
    """Return whether the run should rewrite goldens instead of asserting.

    The switch is an allowlist rather than a truthiness test, so an unexpected
    value (``2``, ``maybe``) compares instead of rewriting. Rewriting goldens
    is destructive, so it fails closed.

    Returns:
        True when ``LINTRO_UPDATE_GOLDENS`` is one of ``1``, ``true``, ``yes``
        or ``on``, case-insensitively.
    """
    return os.environ.get(UPDATE_GOLDENS_ENV, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def load_payload(*, name: str) -> dict[str, Any]:
    """Load one fixed provider response payload from ``payloads/``.

    Args:
        name: File name of the payload, including the ``.json`` suffix.

    Returns:
        The parsed payload mapping.

    Raises:
        TypeError: When the payload file does not hold a JSON object.
    """
    parsed = json.loads((PAYLOAD_DIR / name).read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        msg = f"payload {name} must be a JSON object"
        raise TypeError(msg)
    return parsed


def to_jsonable(value: Any) -> Any:
    """Convert dataclasses, enums, and containers to JSON-safe values.

    Field order follows declaration order so a golden diff reads as a
    behaviour change rather than a dictionary reordering.

    Args:
        value: Any value reachable from a review result.

    Returns:
        A value composed only of JSON primitives, lists, and dicts.
    """
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: to_jsonable(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, Enum):
        return to_jsonable(value.value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = (
            sorted(value, key=repr) if isinstance(value, (set, frozenset)) else value
        )
        return [to_jsonable(item) for item in items]
    return value


def dump_json(*, value: Any) -> str:
    """Serialise a value to the canonical golden JSON text.

    Args:
        value: Value to serialise.

    Returns:
        Pretty-printed JSON text with a trailing newline.
    """
    return json.dumps(to_jsonable(value), indent=2, sort_keys=False) + "\n"


def assert_golden(*, name: str, actual: str) -> None:
    """Compare text against a checked-in golden file, or rewrite it.

    Args:
        name: Snapshot file name inside ``snapshots/``. Goldens use the
            ``.golden`` extension so the repo's formatters leave their bytes
            alone; a reformatted golden is not a golden.
        actual: Text produced by the code under test.

    Raises:
        AssertionError: When the text differs from the golden, or when the
            golden does not exist and the run is not updating goldens.
    """
    path = SNAPSHOT_DIR / name
    if goldens_are_being_updated():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(actual, encoding="utf-8")
        return
    if not path.exists():
        msg = (
            f"missing golden {path}; regenerate with "
            f"{UPDATE_GOLDENS_ENV}=1 uv run pytest tests/unit/ai/review/golden"
        )
        raise AssertionError(msg)
    expected = path.read_text(encoding="utf-8")
    if actual != expected:
        # pytest does not rewrite asserts in a helper module, so the diff has
        # to be built here or a failure says only "the bytes moved".
        diff = "".join(
            difflib.unified_diff(
                expected.splitlines(keepends=True),
                actual.splitlines(keepends=True),
                fromfile=f"{name} (golden)",
                tofile=f"{name} (actual)",
                n=3,
            ),
        )
        msg = (
            f"golden mismatch for {name}: review behaviour changed. "
            f"If the change is intended, say so in the PR body and rerun with "
            f"{UPDATE_GOLDENS_ENV}=1.\n\n{diff}"
        )
        raise AssertionError(msg)


def assert_golden_json(*, name: str, value: Any) -> None:
    """Compare a serialised value against a checked-in golden JSON file.

    Args:
        name: Snapshot file name inside ``snapshots/``.
        value: Value to serialise and compare.
    """
    assert_golden(name=name, actual=dump_json(value=value))
