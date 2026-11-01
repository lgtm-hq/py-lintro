"""Keep the opt-in script pack aligned with declared formatting capabilities."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from assertpy import assert_that

from lintro.enums.capability import Cap
from lintro.plugins.file_discovery import discover_files
from lintro.plugins.registry import ToolRegistry
from lintro.rules import available_rule_packs, resolve_rule_pack
from lintro.tools.semgrep.definition import SemgrepPlugin

PACK = "scripts/no-direct-formatter"


def test_pack_tracks_builtin_format_claimants() -> None:
    """Adding or removing FORMAT requires updating executable pack examples."""
    declared = {
        name
        for name, definition in ToolRegistry.get_definitions().items()
        if ToolRegistry.get_origin(name) == ToolRegistry.BUILTIN_ORIGIN
        and any(Cap.FORMAT in claim.capabilities for claim in definition.claims)
    }
    rules = yaml.safe_load(resolve_rule_pack(PACK).read_text(encoding="utf-8"))["rules"]
    covered = {name for rule in rules for name in rule["metadata"]["lintro_formatters"]}
    assert_that(covered).is_equal_to(declared)
    assert_that(available_rule_packs()).contains(PACK)


@pytest.mark.parametrize("selected", [False, True], ids=["default-off", "opt-in"])
def test_pack_discovers_scripts_only_after_opt_in(
    tmp_path: Path,
    selected: bool,
) -> None:
    """Makefiles and extensionless scripts reach the engine only when selected.

    Args:
        tmp_path: Temporary candidate tree.
        selected: Whether to enable the script pack.
    """
    script = tmp_path / "scripts" / "generate"
    script.parent.mkdir()
    makefile = tmp_path / "Makefile"
    module = tmp_path / "build.mjs"
    for path in (script, makefile, module):
        path.write_text("black generated.py\n", encoding="utf-8")
    plugin = SemgrepPlugin()
    if selected:
        plugin.set_options(rule_packs=[PACK])
    files = discover_files([str(tmp_path)], plugin.definition, [])
    expected = {str(script), str(makefile), str(module)} if selected else set()
    assert_that(set(files)).is_equal_to(expected)

    plugin.reset_options()
    assert_that(discover_files([str(tmp_path)], plugin.definition, [])).is_empty()
