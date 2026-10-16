"""Tests for SPDX license-list codegen determinism."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from assertpy import assert_that

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _REPO_ROOT / "scripts" / "release" / "generate_spdx_data.py"
_FIXTURE = _REPO_ROOT / "tests" / "fixtures" / "spdx" / "licenses_sample.json"


def _load_generator() -> ModuleType:
    """Load ``generate_spdx_data`` as a module.

    Returns:
        ModuleType: Loaded module with ``main`` / ``render_spdx_data_module``.
    """
    spec = importlib.util.spec_from_file_location("generate_spdx_data", _SCRIPT)
    assert_that(spec).is_not_none()
    assert_that(getattr(spec, "loader", None)).is_not_none()
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def test_generate_spdx_data_is_deterministic(tmp_path: Path) -> None:
    """Running the generator twice on the same fixture yields identical output.

    Args:
        tmp_path: Pytest temporary directory.
    """
    generator = _load_generator()
    out_a = tmp_path / "a" / "_spdx_data.py"
    out_b = tmp_path / "b" / "_spdx_data.py"
    out_a.parent.mkdir()
    out_b.parent.mkdir()

    rc_a = generator.main(
        ["--from-file", str(_FIXTURE), "--output", str(out_a)],
    )
    rc_b = generator.main(
        ["--from-file", str(_FIXTURE), "--output", str(out_b)],
    )
    assert_that(rc_a).is_equal_to(0)
    assert_that(rc_b).is_equal_to(0)
    assert_that(out_a.read_text(encoding="utf-8")).is_equal_to(
        out_b.read_text(encoding="utf-8"),
    )


def test_generate_spdx_data_check_detects_drift(tmp_path: Path) -> None:
    """``--check`` exits 1 when the committed module would change.

    Args:
        tmp_path: Pytest temporary directory.
    """
    generator = _load_generator()
    out = tmp_path / "_spdx_data.py"
    assert_that(
        generator.main(["--from-file", str(_FIXTURE), "--output", str(out)]),
    ).is_equal_to(0)

    out.write_text("# drifted\n", encoding="utf-8")
    assert_that(
        generator.main(
            ["--check", "--from-file", str(_FIXTURE), "--output", str(out)],
        ),
    ).is_equal_to(1)


def test_generate_spdx_data_renders_sorted_ids() -> None:
    """Rendered module lists license ids in sorted order with expected flags."""
    generator = _load_generator()
    data = generator.load_licenses_json(_FIXTURE)
    rendered = generator.render_spdx_data_module(data)
    assert_that(rendered).contains('SPDX_LIST_VERSION: str = "test-0.1"')
    assert_that(rendered).contains('"Apache-2.0"')
    assert_that(rendered).contains('"GPL-1.0"')
    assert_that(rendered).contains('"MIT"')
    # GPL-1.0 omits isFsfLibre upstream → None in the flags tuple.
    assert_that(rendered).contains('"GPL-1.0": (False, None, True)')
    # Ids appear sorted: Apache before GPL before MIT inside the frozenset.
    apache_pos = rendered.index('"Apache-2.0"')
    gpl_pos = rendered.index('"GPL-1.0"')
    mit_pos = rendered.index('"MIT"')
    assert_that(apache_pos).is_less_than(gpl_pos)
    assert_that(gpl_pos).is_less_than(mit_pos)
