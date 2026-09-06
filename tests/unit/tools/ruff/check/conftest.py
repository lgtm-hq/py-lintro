"""Shared fixtures for ruff check tests.

Most fixtures are inherited from parent conftest.py files; this module adds a
real loguru sink so logging behaviour can be asserted on captured records
rather than on a patched logger (#2315).
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from loguru import logger


@pytest.fixture
def loguru_records() -> Generator[list[tuple[str, str]]]:
    """Capture every loguru record emitted during the test.

    Yields:
        list[tuple[str, str]]: A list of ``(level name, message)`` pairs,
        appended to as records are emitted. The sink accepts every level so
        DEBUG output is visible.
    """
    records: list[tuple[str, str]] = []

    def sink(message: object) -> None:
        """Append one loguru record to the captured list.

        Args:
            message: Loguru message object carrying the record.
        """
        record = message.record  # type: ignore[attr-defined]
        records.append((record["level"].name, record["message"]))

    sink_id = logger.add(sink, level="DEBUG", format="{message}")
    try:
        yield records
    finally:
        logger.remove(sink_id)
