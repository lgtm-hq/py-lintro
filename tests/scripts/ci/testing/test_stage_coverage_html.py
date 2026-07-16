"""Tests for coverage.json HTML staging helpers."""

from __future__ import annotations

import importlib.util
import json
import subprocess  # nosec B404 - subprocess used with fixed argv in controlled tests
from pathlib import Path
from typing import Any

from assertpy import assert_that

ROOT = Path(__file__).resolve().parents[4]
RENDER_SCRIPT = ROOT / "scripts" / "ci" / "testing" / "render-coverage-json-html.py"
STAGE_SCRIPT = ROOT / "scripts" / "ci" / "testing" / "stage-python-coverage-html.sh"


def _load_render_module() -> Any:
    """Load render-coverage-json-html.py as a module.

    Returns:
        Imported module object.
    """
    spec = importlib.util.spec_from_file_location(
        "render_coverage_json_html",
        RENDER_SCRIPT,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_render_coverage_json_html_writes_index(
    tmp_path: Path,
) -> None:
    """JSON coverage reports should produce a browsable index.html."""
    mod = _load_render_module()
    coverage_data = {
        "totals": {"percent_covered": 80.0},
        "files": {
            "pkg/mod.py": {
                "summary": {
                    "covered_lines": 8,
                    "num_statements": 10,
                    "percent_covered": 80.0,
                },
            },
        },
    }
    index = mod.render_coverage_json_html(
        coverage_data=coverage_data,
        output_dir=tmp_path,
    )
    text = index.read_text(encoding="utf-8")
    assert_that(text).contains("80.0%")
    assert_that(text).contains("pkg/mod.py")


def test_stage_script_uses_coverage_json(
    tmp_path: Path,
) -> None:
    """Stage script should render HTML from coverage.json when no .coverage."""
    report = tmp_path / "coverage-report"
    report.mkdir()
    (report / "coverage.json").write_text(
        json.dumps(
            {
                "totals": {"percent_covered": 90.0},
                "files": {
                    "a.py": {
                        "summary": {
                            "covered_lines": 9,
                            "num_statements": 10,
                            "percent_covered": 90.0,
                        },
                    },
                },
            },
        ),
        encoding="utf-8",
    )
    # Script resolves ROOT from its path; mirror the repo layout under tmp.
    fake_root = tmp_path / "repo"
    fake_root.mkdir()
    script_dir = fake_root / "scripts" / "ci" / "testing"
    script_dir.mkdir(parents=True)
    (script_dir / "stage-python-coverage-html.sh").write_text(
        STAGE_SCRIPT.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (script_dir / "render-coverage-json-html.py").write_text(
        RENDER_SCRIPT.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (script_dir / "stage-python-coverage-html.sh").chmod(0o755)
    (fake_root / "coverage-report").symlink_to(report)

    result = subprocess.run(  # nosec B603 - fixed argv run against a real binary in a controlled test; shell=False, no user shell input
        [str(script_dir / "stage-python-coverage-html.sh")],
        cwd=fake_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert_that(result.returncode).is_equal_to(0)
    index = fake_root / "coverage-html" / "index.html"
    assert_that(index.exists()).is_true()
    assert_that(index.read_text(encoding="utf-8")).contains("90.0%")
