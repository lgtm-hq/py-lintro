"""Install-lock text fields must not serialize JSON null as Python text."""

from __future__ import annotations

from pathlib import Path

import pytest
from assertpy import assert_that

from lintro.tools.core.install_lock import (
    InstallLock,
    read_install_lock,
    write_install_lock,
)


@pytest.mark.parametrize("value", [None, 42, False, [], {}])
def test_install_lock_non_string_text_uses_defaults(value: object) -> None:
    """Optional entry fields and timestamps use their documented defaults."""
    lock = InstallLock.from_dict(
        {
            "created_at": value,
            "tools": [
                {
                    "name": "ruff",
                    "version": value,
                    "install_hint": value,
                    "status": value,
                },
            ],
        },
    )

    assert_that(lock.created_at).is_empty()
    assert_that(lock.tools).is_length(1)
    assert_that(lock.to_dict()["tools"]).is_equal_to(
        [
            {"name": "ruff", "version": "", "install_hint": "", "status": "to_install"},
        ],
    )


def test_install_lock_string_fields_survive_json_round_trip(tmp_path: Path) -> None:
    """Valid text, including an intentional empty status, is preserved."""
    data = {
        "created_at": "2026-10-31T00:00:00Z",
        "tools": [
            {
                "name": "ruff",
                "version": "0.14.0",
                "install_hint": "uv tool install ruff",
                "status": "",
            },
        ],
    }
    lock = InstallLock.from_dict(data)
    path = tmp_path / "install.lock.json"
    write_install_lock(path, lock)

    assert_that(read_install_lock(path).to_dict()).is_equal_to(lock.to_dict())
    assert_that(lock.created_at).is_equal_to(data["created_at"])
    assert_that(lock.to_dict()["tools"]).is_equal_to(data["tools"])
