"""Pytest configuration for swiftlint tests."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from lintro.tools.definitions.swiftlint import SwiftlintPlugin


@pytest.fixture
def swiftlint_plugin() -> SwiftlintPlugin:
    """Provide a SwiftlintPlugin instance for testing.

    Returns:
        A SwiftlintPlugin instance with version verification stubbed out.
    """
    with patch(
        "lintro.plugins.execution_preparation.verify_tool_version",
        return_value=None,
    ):
        return SwiftlintPlugin()


# JSON payload for two identifier_name violations (kept small and realistic).
SAMPLE_JSON = (
    '[{"character": 9, "file": "Sample.swift", "line": 4, '
    '"reason": "Variable name \'x\' should be between 3 and 40 characters long", '
    '"rule_id": "identifier_name", "severity": "Error", "type": "Identifier Name"}, '
    '{"character": 7, "file": "Sample.swift", "line": 8, '
    '"reason": "Type name \'foo\' should start with an uppercase character", '
    '"rule_id": "type_name", "severity": "Error", "type": "Type Name"}]'
)

# JSON payload for a single fixable violation (trailing_semicolon).
FIXABLE_JSON = (
    '[{"character": 19, "file": "Sample.swift", "line": 3, '
    '"reason": "Lines should not have trailing semicolons", '
    '"rule_id": "trailing_semicolon", "severity": "Warning", '
    '"type": "Trailing Semicolon"}]'
)
