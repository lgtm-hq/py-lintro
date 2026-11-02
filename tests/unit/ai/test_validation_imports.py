"""Post-fix validation imports must not initialize the tool/plugin system."""

from __future__ import annotations

import json
import subprocess  # nosec B404 - fixed argv against this interpreter
import sys
from pathlib import Path

import pytest
from assertpy import assert_that

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_IMPORT_PROBE = """
import importlib
import json
import sys

sys.path.insert(0, sys.argv[2])
module = importlib.import_module(sys.argv[1])
print(json.dumps({
    "file": module.__file__,
    "loaded": sorted(name for name in sys.modules
                     if name in {"lintro.tools", "lintro.plugins"}
                     or name.startswith(("lintro.tools.", "lintro.plugins."))),
}))
"""


@pytest.mark.parametrize(
    "module",
    ["lintro.ai.validation", "lintro.ai.rerun", "lintro.utils.check_answered"],
)
def test_validation_import_does_not_load_tools_or_plugins(module: str) -> None:
    """Probe real imports in a fresh process, independent of pytest collection."""
    completed = subprocess.run(  # nosec B603 - fixed argv, shell=False
        [sys.executable, "-I", "-c", _IMPORT_PROBE, module, str(_PROJECT_ROOT)],
        cwd=_PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    report = json.loads(completed.stdout)

    assert_that(Path(report["file"]).resolve()).is_equal_to(
        _PROJECT_ROOT.joinpath(*module.split(".")).with_suffix(".py"),
    )
    assert_that(report["loaded"]).is_empty()
