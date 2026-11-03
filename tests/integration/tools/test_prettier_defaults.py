"""Exercise real Prettier with Lintro defaults injection enabled."""

from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path

import pytest

from lintro.config import tool_config_generator
from lintro.config.config_loader import clear_config_cache
from lintro.tools.prettier.definition import PrettierPlugin
from tests.integration._tools import require_tool

pytestmark = require_tool("prettier")


@pytest.fixture
def prettier_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[Path, None, None]:
    """Keep config discovery and generated defaults in an isolated project.

    Args:
        tmp_path: Temporary project directory.
        monkeypatch: Fixture for working-directory and environment isolation.

    Yields:
        Path: The isolated project.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("LINTRO_SKIP_CONFIG_INJECTION", raising=False)
    monkeypatch.delenv("LINTRO_TEST_MODE", raising=False)
    (tmp_path / ".lintro-config.yaml").write_text("{}\n", encoding="utf-8")
    clear_config_cache()
    monkeypatch.setattr(tool_config_generator, "_temp_files", [])
    yield tmp_path
    for config_path in list(tool_config_generator._temp_files):
        tool_config_generator.cleanup_temp_config(config_path)


@pytest.mark.parametrize("tab_width", [2, 4], ids=["builtin", "user-defaults"])
def test_generated_defaults_check_and_fix(
    prettier_project: Path,
    tab_width: int,
) -> None:
    """Check real violations, apply defaults, and verify formatting converges.

    Args:
        prettier_project: Isolated project with config injection enabled.
        tab_width: Expected indentation, with four supplied as a user default.
    """
    if tab_width == 4:
        (prettier_project / ".lintro-config.yaml").write_text(
            "defaults:\n  prettier:\n    tabWidth: 4\n",
            encoding="utf-8",
        )
    target = prettier_project / "sample.json"
    original = '{\n"key":true}'
    target.write_text(original, encoding="utf-8")
    plugin = PrettierPlugin()
    args = plugin._build_config_args()
    assert args[0] == "--config"
    assert "--no-config" not in args
    defaults = json.loads(Path(args[1]).read_text(encoding="utf-8"))
    assert defaults == (
        {"proseWrap": "always", "tabWidth": 4}
        if tab_width == 4
        else {"proseWrap": "always"}
    )

    checked = plugin.check([str(target)], {})
    assert not checked.success
    assert checked.issues_count == 1
    assert target.read_text(encoding="utf-8") == original
    fixed = plugin.fix([str(target)], {})
    assert fixed.success
    assert target.read_text(encoding="utf-8") == (
        "{\n" + " " * tab_width + '"key": true\n}\n'
    )
    rechecked = plugin.check([str(target)], {})
    assert rechecked.success
    assert rechecked.issues_count == 0


@pytest.mark.parametrize("native_name", [".prettierrc.json", "package.json"])
def test_native_config_takes_precedence(
    prettier_project: Path,
    native_name: str,
) -> None:
    """Let native config override conflicting Lintro defaults.

    Args:
        prettier_project: Isolated project with config injection enabled.
        native_name: Native config filename to exercise.
    """
    (prettier_project / ".lintro-config.yaml").write_text(
        "defaults:\n  prettier:\n    tabWidth: 2\n",
        encoding="utf-8",
    )
    native = {"tabWidth": 4}
    payload = {"prettier": native} if native_name == "package.json" else native
    (prettier_project / native_name).write_text(json.dumps(payload), encoding="utf-8")
    target = prettier_project / "sample.json"
    expected = '{\n    "key": true\n}\n'
    target.write_text(expected, encoding="utf-8")
    plugin = PrettierPlugin()
    assert plugin._build_config_args() == []
    checked = plugin.check([str(target)], {})
    assert checked.success
    assert checked.issues_count == 0
    assert plugin.fix([str(target)], {}).success
    assert target.read_text(encoding="utf-8") == expected


@pytest.mark.parametrize("native", [False, True], ids=["generated", "native"])
@pytest.mark.parametrize("operation", ["check", "fix"])
def test_invalid_config_fails_without_changing_input(
    prettier_project: Path,
    native: bool,
    operation: str,
) -> None:
    """Keep invalid native and generated config failures visible.

    Args:
        prettier_project: Isolated project with config injection enabled.
        native: Whether the invalid option comes from native config.
        operation: Prettier operation to exercise.
    """
    if native:
        (prettier_project / ".prettierrc.json").write_text(
            '{"tabWidth": "invalid"}',
            encoding="utf-8",
        )
    else:
        (prettier_project / ".lintro-config.yaml").write_text(
            "defaults:\n  prettier:\n    tabWidth: invalid\n",
            encoding="utf-8",
        )
    target = prettier_project / "sample.json"
    original = '{"key":true}'
    target.write_text(original, encoding="utf-8")
    result = getattr(PrettierPlugin(), operation)([str(target)], {})
    assert not result.success
    assert "Invalid tabWidth value" in (result.output or "")
    assert target.read_text(encoding="utf-8") == original
