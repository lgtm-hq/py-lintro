"""Untrusted baseline validation and atomic persistence."""

import json
from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that

from lintro.models.core.tool_result import ToolResult
from lintro.utils.fingerprint_baseline import snapshot
from lintro.utils.fingerprint_baseline_file import change_baseline, read_baseline


@pytest.fixture
def baseline_data(tmp_path: Path, dirty_result: ToolResult) -> dict[str, Any]:
    """Return a serializable valid baseline."""
    return snapshot([dirty_result], root=tmp_path).model_dump()


@pytest.mark.parametrize(
    "changes",
    [
        {"version": True},
        {"version": 1.0},
        {"version": 2},
        {"unknown": []},
        {"created": "yesterday"},
        {"created": "2026-02-30T00:00:00Z"},
        {"entries": None},
        {"entries": {}},
    ],
)
def test_invalid_documents_are_rejected(
    tmp_path: Path,
    baseline_data: dict[str, Any],
    changes: dict[str, object],
) -> None:
    """Unknown schemas and wrong types must never silently disable filtering."""
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(baseline_data | changes), encoding="utf-8")
    with pytest.raises(ValueError):
        read_baseline(path)


@pytest.mark.parametrize(
    "changes",
    [
        {"file": "../escape.py"},
        {"file": "/absolute.py"},
        {"file": "a/../b.py"},
        {"file": "./a.py"},
        {"file": "a//b.py"},
        {"file": "C:/windows.py"},
        {"file": "a\\b.py"},
        {"file": "a\nb.py"},
        {"file": ""},
        {"count": True},
        {"count": 0},
        {"count": -1},
        {"count": 1.5},
        {"line": True},
        {"line": -1},
        {"message_hash": "abc123"},
        {"tool": "Ruff"},
        {"tool": "../ruff"},
        {"message": "not stored"},
    ],
)
def test_invalid_entries_are_rejected(
    tmp_path: Path,
    baseline_data: dict[str, Any],
    changes: dict[str, object],
) -> None:
    """Fingerprints cannot contain traversing paths or ambiguous budgets."""
    path = tmp_path / "baseline.json"
    baseline_data["entries"][0].update(changes)
    path.write_text(json.dumps(baseline_data), encoding="utf-8")
    with pytest.raises(ValueError):
        read_baseline(path)


@pytest.mark.parametrize(
    "kind",
    ["duplicate_key", "duplicate_entry", "missing_version"],
)
def test_ambiguous_or_unversioned_input_is_rejected(
    tmp_path: Path,
    baseline_data: dict[str, Any],
    kind: str,
) -> None:
    """Duplicate objects and missing versions cannot enlarge acknowledgement budgets."""
    if kind == "duplicate_entry":
        baseline_data["entries"] *= 2
    if kind == "missing_version":
        del baseline_data["version"]
    payload = json.dumps(baseline_data)
    if kind == "duplicate_key":
        payload = payload.replace('"version": 1', '"version": 1, "version": 1')
    path = tmp_path / "baseline.json"
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(ValueError):
        read_baseline(path)


def test_roundtrip_is_deterministic_and_creation_does_not_overwrite(
    tmp_path: Path,
    dirty_result: ToolResult,
) -> None:
    """Same data has stable bytes and create refuses an existing snapshot."""
    baseline = snapshot([dirty_result], root=tmp_path)
    path = tmp_path / "baseline.json"
    change_baseline(path, lambda _: baseline, create=True)
    original = path.read_bytes()
    assert_that(read_baseline(path)).is_equal_to(baseline)
    change_baseline(
        path,
        lambda previous: previous if previous is not None else baseline,
    )
    assert_that(path.read_bytes()).is_equal_to(original)
    with pytest.raises(ValueError, match="already exists"):
        change_baseline(path, lambda _: baseline, create=True)
    assert_that(path.read_bytes()).is_equal_to(original)
    assert_that(path.with_suffix(".json.lock").exists()).is_false()


def test_failed_atomic_replace_keeps_old_file_and_cleans_temporary(
    tmp_path: Path,
    dirty_result: ToolResult,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A disk failure cannot truncate the committed acknowledgement file."""
    baseline = snapshot([dirty_result], root=tmp_path)
    path = tmp_path / "baseline.json"
    change_baseline(path, lambda _: baseline, create=True)
    original = path.read_bytes()

    def fail_replace(*args: object) -> None:
        raise OSError("disk unavailable")

    monkeypatch.setattr(
        "lintro.utils.fingerprint_baseline_file.os.replace",
        fail_replace,
    )
    with pytest.raises(OSError, match="disk unavailable"):
        change_baseline(path, lambda _: baseline)
    assert_that(path.read_bytes()).is_equal_to(original)
    assert_that(sorted(p.name for p in tmp_path.iterdir())).is_equal_to(
        ["baseline.json"],
    )


def test_busy_lock_and_symlink_destination_cannot_be_written(
    tmp_path: Path,
    dirty_result: ToolResult,
) -> None:
    """Concurrent writers and symbolic destinations leave target bytes intact."""
    baseline = snapshot([dirty_result], root=tmp_path)
    path = tmp_path / "baseline.json"
    change_baseline(path, lambda _: baseline, create=True)
    lock = path.with_suffix(".json.lock")
    lock.write_text("busy", encoding="utf-8")
    with pytest.raises(FileExistsError):
        change_baseline(path, lambda _: baseline)
    assert_that(lock.read_text()).is_equal_to("busy")
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(ValueError, match="symlink"):
        change_baseline(link, lambda _: baseline)
    assert_that(read_baseline(path)).is_equal_to(baseline)


def test_oversized_input_is_rejected_before_decoding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bounded reads reject large untrusted files."""
    monkeypatch.setattr("lintro.utils.fingerprint_baseline_file.MAX_BASELINE_BYTES", 4)
    path = tmp_path / "baseline.json"
    path.write_bytes(b"x" * 5)
    with pytest.raises(ValueError, match="exceeds"):
        read_baseline(path)
