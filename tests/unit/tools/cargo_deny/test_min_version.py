"""cargo-deny reads its minimum version from the tool manifest (#2400)."""

from __future__ import annotations

import json
from pathlib import Path

from assertpy import assert_that

import lintro.tools
from lintro.tools.cargo_deny.definition import CargoDenyPlugin


def test_min_version_comes_from_manifest() -> None:
    """The definition's floor is the manifest source's ``min_version`` entry.

    A version literal in the definition drifts from the manifest the install
    scripts and the version gate read, so the manifest is the single source.
    """
    source = Path(lintro.tools.__file__).parent / "manifest.src.json"
    entries = json.loads(source.read_text(encoding="utf-8"))["tools"]
    declared = next(e for e in entries if e["name"] == "cargo_deny")

    assert_that(declared).contains_key("min_version")
    assert_that(CargoDenyPlugin().definition.min_version).is_equal_to(
        declared["min_version"],
    )
