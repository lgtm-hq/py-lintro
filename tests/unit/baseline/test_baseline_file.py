"""Untrusted baseline validation and atomic persistence."""

import errno
import json
import os
import stat
from pathlib import Path
from typing import Any

import pytest
from assertpy import assert_that

from lintro.models.core.fingerprint_baseline import FingerprintBaseline
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


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes and umask")
@pytest.mark.parametrize("mask", [0o002, 0o022, 0o077])
def test_new_baseline_respects_umask(
    tmp_path: Path,
    dirty_result: ToolResult,
    mask: int,
) -> None:
    """New acknowledgements have ordinary file permissions bounded by the umask."""
    baseline = snapshot([dirty_result], root=tmp_path)
    path = tmp_path / "baseline.json"
    previous_mask = os.umask(mask)
    try:
        change_baseline(path, lambda _: baseline, create=True)
    finally:
        os.umask(previous_mask)
    assert_that(stat.S_IMODE(path.stat().st_mode)).is_equal_to(0o666 & ~mask)
    assert_that(read_baseline(path)).is_equal_to(baseline)


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes and umask")
@pytest.mark.parametrize("mode", [0o600, 0o640, 0o664])
def test_replacement_preserves_existing_baseline_mode(
    tmp_path: Path,
    dirty_result: ToolResult,
    mode: int,
) -> None:
    """Updating a snapshot preserves its access policy even under a stricter umask."""
    baseline = snapshot([dirty_result], root=tmp_path)
    path = tmp_path / "baseline.json"
    change_baseline(path, lambda _: baseline, create=True)
    path.chmod(mode)
    updated = baseline.model_copy(update={"entries": []})
    previous_mask = os.umask(0o077)
    try:
        change_baseline(path, lambda _: updated)
    finally:
        os.umask(previous_mask)
    assert_that(stat.S_IMODE(path.stat().st_mode)).is_equal_to(mode)
    assert_that(read_baseline(path)).is_equal_to(updated)


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


@pytest.mark.parametrize("fail_transform", [False, True])
def test_missing_lock_does_not_mask_transaction_outcome(
    tmp_path: Path,
    dirty_result: ToolResult,
    fail_transform: bool,
) -> None:
    """An externally removed lock preserves both successful writes and real errors."""
    baseline = snapshot([dirty_result], root=tmp_path)
    path = tmp_path / "baseline.json"
    change_baseline(path, lambda _: baseline, create=True)
    original = path.read_bytes()

    def transform(previous: FingerprintBaseline | None) -> FingerprintBaseline:
        path.with_name(path.name + ".lock").unlink()
        if fail_transform:
            raise ValueError("transaction rejected")
        return baseline.model_copy(update={"entries": []})

    if fail_transform:
        with pytest.raises(ValueError, match="transaction rejected"):
            change_baseline(path, transform)
        assert_that(path.read_bytes()).is_equal_to(original)
    else:
        updated = change_baseline(path, transform)
        assert_that(updated.entries).is_empty()
        assert_that(read_baseline(path)).is_equal_to(updated)
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
    original = path.read_bytes()
    lock = path.with_suffix(".json.lock")
    lock.write_text("busy", encoding="utf-8")
    with pytest.raises(ValueError, match="lock already exists") as error:
        change_baseline(path, lambda _: baseline)
    assert_that(str(error.value)).contains(str(lock), "active", "remove", "retry")
    assert_that(lock.read_text()).is_equal_to("busy")
    assert_that(path.read_bytes()).is_equal_to(original)
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


@pytest.mark.parametrize(
    "error",
    [errno.ENOSYS, errno.EOPNOTSUPP, errno.EPERM, errno.EXDEV],
)
def test_unsupported_hardlinks_report_a_remedy_and_leave_no_files(
    tmp_path: Path,
    dirty_result: ToolResult,
    monkeypatch: pytest.MonkeyPatch,
    error: int,
) -> None:
    """Unsupported atomic creation fails with a usable remedy and cleans up."""
    baseline = snapshot([dirty_result], root=tmp_path)
    path = tmp_path / "baseline.json"

    def unsupported(*args: object) -> None:
        raise OSError(error, "hard links unavailable")

    monkeypatch.setattr("lintro.utils.fingerprint_baseline_file.os.link", unsupported)
    with pytest.raises(ValueError, match="Cannot atomically create baseline") as caught:
        change_baseline(path, lambda _: baseline, create=True)
    assert_that(str(caught.value)).contains("--baseline", "filesystem", "hard links")
    assert_that(list(tmp_path.iterdir())).is_empty()
