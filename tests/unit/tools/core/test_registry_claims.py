"""Registry-level invariants over every built-in tool's claims (#2607)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from assertpy import assert_that

from lintro.enums.capability import Cap
from lintro.plugins.registry import ToolRegistry

_MUTATING: frozenset[Cap] = frozenset({Cap.FIX, Cap.FORMAT})


def test_every_mutating_claim_also_declares_check() -> None:
    """A claim that can rewrite a file must also be able to verify it.

    The run-level verify pass measures residuals with each tool's ``CHECK``
    capability and is the only source of residuals. A mutator without
    ``CHECK`` would silently keep whatever it self-reports, which is exactly
    the narrower truth #2607 removes. Fails with the tool's name so a future
    tool cannot regress the invariant unnoticed.
    """
    offenders: list[str] = []
    for name, definition in ToolRegistry.get_definitions().items():
        for claim in getattr(definition, "claims", None) or ():
            if claim.capabilities & _MUTATING and Cap.CHECK not in claim.capabilities:
                offenders.append(name)
    assert_that(offenders).described_as(
        "mutating claims without CHECK",
    ).is_empty()


@pytest.mark.parametrize(
    ("tool_name", "relative_path", "content"),
    [
        ("prettier", "styles.css", "a{color:red}\n"),
        ("oxfmt", "app.js", "const a={b:1}\n"),
        ("rustfmt", "src/main.rs", "fn main(){}\n"),
        ("shfmt", "run.sh", "#!/bin/sh\nif true;then echo hi;fi\n"),
    ],
)
def test_check_discovers_the_same_file_fix_rewrote(
    tmp_path: Path,
    tool_name: str,
    relative_path: str,
    content: str,
) -> None:
    """``CHECK`` must not answer ``no_files`` over a scope ``FIX`` rewrote.

    The verify pass treats a ``no_files`` check as "this tool rewrote nothing"
    and lets every pre-fix finding stand, which would report a clean format as
    zero net resolved. That is only safe while check and fix discovery agree,
    which this pins for the four tools that gained ``CHECK`` in #2607.

    Args:
        tmp_path: Temporary project directory.
        tool_name: The tool under test.
        relative_path: Fixture file the tool claims.
        content: Fixture file content.
    """
    if tool_name == "rustfmt":
        (tmp_path / "Cargo.toml").write_text(
            '[package]\nname = "test"\nversion = "0.1.0"\n',
            encoding="utf-8",
        )
    target = tmp_path / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")

    plugin = ToolRegistry.get(tool_name)
    with (
        patch(
            "lintro.plugins.execution_preparation.verify_tool_version",
            return_value=None,
        ),
        patch.object(plugin, "_run_subprocess", return_value=(True, "")),
    ):
        fixed = plugin.fix([str(target)], {})
        checked = plugin.check([str(target)], {})

    assert_that(fixed.no_files).described_as(f"{tool_name} fix").is_false()
    assert_that(checked.no_files).described_as(f"{tool_name} check").is_false()
