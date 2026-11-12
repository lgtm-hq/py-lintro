"""Shared workspace for baseline unit and integration tests."""

from pathlib import Path

import pytest


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Make the test workspace the invocation root and disable automatic installs."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".lintro-config.yaml").write_text(
        "execution:\n  auto_install_deps: false\n  parallel: false\n",
        encoding="utf-8",
    )
    from lintro.config.config_loader import clear_config_cache

    clear_config_cache()
    return tmp_path
