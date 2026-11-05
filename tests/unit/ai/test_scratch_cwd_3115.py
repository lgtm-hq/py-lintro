"""Guard: AI tests keep cwd-rooted writes out of the checkout (#3115).

``persist_raw_response`` and ``write_audit_log`` both default to paths under
the process working directory. The suite-wide ``_run_from_scratch_cwd``
fixture in ``tests/unit/ai/conftest.py`` moves every AI test into an empty
directory of its own, so neither writer can reach the directory pytest was
started from — normally the repository checkout.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.ai.audit import AUDIT_DIR, AUDIT_JSONL_FILE, write_audit_log
from lintro.ai.paths import resolve_workspace_root
from lintro.ai.raw_response import RAW_RESPONSE_DIR, persist_raw_response


def _ai_artefacts(root: Path) -> set[str]:
    """Return the raw-response captures and audit log present under *root*.

    Args:
        root: Directory whose ``.lintro-cache/ai`` artefacts are listed.

    Returns:
        Capture file names plus the audit log name and size, so an append to
        an existing log counts as a change too.
    """
    found: set[str] = set()
    capture_dir = root / RAW_RESPONSE_DIR
    if capture_dir.is_dir():
        found.update(entry.name for entry in capture_dir.iterdir())
    audit_log = root / AUDIT_DIR / AUDIT_JSONL_FILE
    if audit_log.is_file():
        found.add(f"{AUDIT_JSONL_FILE}:{audit_log.stat().st_size}")
    return found


def test_default_rooted_ai_writes_stay_out_of_the_invocation_directory(
    request: pytest.FixtureRequest,
    tmp_path: Path,
) -> None:
    """Captures and audit records land in the scratch cwd, not the checkout."""
    invocation_dir = request.config.invocation_params.dir.resolve()
    before = _ai_artefacts(invocation_dir)

    capture = persist_raw_response(
        provider="guard",
        stage="parse-failure",
        raw="not json at all",
    )
    write_audit_log(resolve_workspace_root(), [], 0, 0.0)

    scratch = Path.cwd().resolve()
    assert_that(scratch).is_not_equal_to(invocation_dir)
    assert_that(scratch).is_not_equal_to(tmp_path.resolve())
    assert_that(capture).is_not_none()
    assert_that(str(capture)).starts_with(str(scratch / RAW_RESPONSE_DIR))
    assert_that(Path(str(capture)).read_text(encoding="utf-8")).is_equal_to(
        "not json at all",
    )
    assert_that(
        (scratch / AUDIT_DIR / AUDIT_JSONL_FILE).read_text(encoding="utf-8"),
    ).contains('"applied_count": 0')
    assert_that(_ai_artefacts(invocation_dir)).is_equal_to(before)
