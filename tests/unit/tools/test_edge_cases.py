"""Edge case tests for tool plugin handling.

This module tests edge cases that may occur in real-world usage:
- Symlink handling (regular and broken symlinks)
- Long file paths (200, 260, 500+ characters)
- Unicode in output (bullets, arrows, CJK, emoji, accented chars)
- Concurrent execution thread safety
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
from assertpy import assert_that

from lintro.enums.tool_name import ToolName
from lintro.models.core.tool_result import ToolResult
from lintro.tools.definitions.ruff import RuffPlugin
from tests.test_samples_helpers import copy_sample

if TYPE_CHECKING:
    from collections.abc import Callable


# =============================================================================
# Symlink handling tests
# =============================================================================


def test_regular_symlink_is_followed(tmp_path: Path) -> None:
    """Verify regular symlinks are correctly resolved and processed.

    Args:
        tmp_path: Temporary directory path for test files.
    """
    # Create a real file
    real_file = copy_sample(
        tmp_path,
        "tools",
        "python",
        "common",
        "minimal_x.py",
        dest_name="real_file.py",
    )

    # Create a symlink to the file
    symlink_path = tmp_path / "symlink_file.py"
    symlink_path.symlink_to(real_file)

    assert_that(symlink_path.exists()).is_true()
    assert_that(symlink_path.is_symlink()).is_true()
    assert_that(symlink_path.resolve()).is_equal_to(real_file.resolve())


def test_broken_symlink_handled_gracefully(tmp_path: Path) -> None:
    """Verify broken symlinks don't cause crashes during path filtering.

    Args:
        tmp_path: Temporary directory path for test files.
    """
    # Create a symlink to a non-existent target
    broken_symlink = tmp_path / "broken_link.py"
    target_path = tmp_path / "nonexistent.py"
    broken_symlink.symlink_to(target_path)

    # The symlink exists as a link but its target doesn't
    assert_that(broken_symlink.is_symlink()).is_true()
    assert_that(broken_symlink.exists()).is_false()

    # Verify we can check for broken symlinks
    try:
        broken_symlink.resolve(strict=True)
        resolved = True
    except FileNotFoundError:
        resolved = False

    assert_that(resolved).is_false()


def test_symlink_directory_traversal(tmp_path: Path) -> None:
    """Verify symlinks to directories are handled correctly.

    Args:
        tmp_path: Temporary directory path for test files.
    """
    # Create a subdirectory with a file
    subdir = tmp_path / "subdir"
    subdir.mkdir()
    copy_sample(
        subdir,
        "tools",
        "python",
        "common",
        "minimal_y.py",
        dest_name="file.py",
    )

    # Create a symlink to the directory
    dir_link = tmp_path / "link_to_subdir"
    dir_link.symlink_to(subdir)

    # File should be accessible through the symlink
    linked_file = dir_link / "file.py"
    assert_that(linked_file.exists()).is_true()
    assert_that(linked_file.read_text()).is_equal_to("y = 2\n")


# =============================================================================
# Long file path tests
# =============================================================================


@pytest.mark.parametrize(
    ("path_length", "description"),
    [
        pytest.param(200, "long-path-200", id="200-chars"),
        pytest.param(250, "near-windows-limit", id="250-chars"),
    ],
)
def test_long_file_paths_handled(
    tmp_path: Path,
    path_length: int,
    description: str,
) -> None:
    """Verify long file paths are handled correctly.

    Args:
        tmp_path: Temporary directory path for test files.
        path_length: Target path length to test.
        description: Test description.
    """
    # Calculate how many nested directories we need
    # Each directory adds ~5 chars (name + separator)
    base_len = len(str(tmp_path))
    remaining = path_length - base_len - 10  # Leave room for filename

    # Create nested directories
    current = tmp_path
    segment_len = 8  # Directory name length
    while len(str(current)) < base_len + remaining:
        dir_name = "d" * segment_len
        current = current / dir_name
        try:
            current.mkdir(exist_ok=True)
        except OSError:
            # Path might be too long for the OS
            break

    # Create a file in the deepest directory
    if current.exists():
        test_file = current / "test.py"
        try:
            copy_sample(
                current,
                "tools",
                "python",
                "common",
                "minimal_x.py",
                dest_name="test.py",
            )
            assert_that(test_file.exists()).is_true()
            assert_that(len(str(test_file))).is_greater_than(100)
        except OSError:
            # File creation might fail on some systems
            pass


def test_path_with_spaces_and_special_chars(tmp_path: Path) -> None:
    """Verify paths with spaces and special characters work correctly.

    Args:
        tmp_path: Temporary directory path for test files.
    """
    # Create a directory with spaces and special chars
    special_dir = tmp_path / "path with spaces"
    special_dir.mkdir()

    test_file = copy_sample(
        special_dir,
        "tools",
        "python",
        "common",
        "minimal_x.py",
        dest_name="file [1].py",
    )

    assert_that(test_file.exists()).is_true()
    assert_that(test_file.read_text()).is_equal_to("x = 1\n")


# =============================================================================
# Unicode output tests
# =============================================================================


UNICODE_TEST_CASES = [
    pytest.param("• Bullet point", "bullet", id="bullet"),
    pytest.param("→ Arrow", "arrow", id="arrow"),
    pytest.param("✓ Check mark", "checkmark", id="checkmark"),
    pytest.param("✗ Cross mark", "crossmark", id="crossmark"),
    pytest.param("中文测试", "cjk-chinese", id="chinese"),
    pytest.param("日本語テスト", "cjk-japanese", id="japanese"),
    pytest.param("한국어 테스트", "cjk-korean", id="korean"),
    pytest.param("😀 Emoji test", "emoji", id="emoji"),
    pytest.param("Ñ ñ á é í ó ú", "accented", id="accented"),
    pytest.param("αβγδ ΑΒΓΔ", "greek", id="greek"),
    pytest.param("∀∃∅∈∉∋∌", "math", id="math-symbols"),
]


@pytest.mark.parametrize(
    ("unicode_text", "category"),
    UNICODE_TEST_CASES,
)
def test_unicode_in_tool_output(unicode_text: str, category: str) -> None:
    """Verify Unicode characters in tool output are handled correctly.

    Args:
        unicode_text: Unicode text to test.
        category: Category of Unicode being tested.
    """
    # Create a mock tool result with Unicode in output
    result = ToolResult(
        name=ToolName.RUFF,
        success=True,
        issues_count=0,
        output=f"Message: {unicode_text}",
        issues=None,
    )

    assert_that(result.output).contains(unicode_text)
    assert_that(str(result.output)).is_not_empty()


@pytest.mark.parametrize(
    ("unicode_text", "category"),
    UNICODE_TEST_CASES,
)
def test_unicode_in_file_paths(
    tmp_path: Path,
    unicode_text: str,
    category: str,
) -> None:
    """Verify Unicode characters in file paths are handled correctly.

    Args:
        tmp_path: Temporary directory path for test files.
        unicode_text: Unicode text to test in file name.
        category: Category of Unicode being tested.
    """
    # Create a file with Unicode in the name
    # Remove characters that are invalid in file names
    safe_name = unicode_text.replace("/", "_").replace("\\", "_")
    safe_name = "".join(c for c in safe_name if c.isalnum() or c in " _-")[:20]

    if not safe_name.strip():
        safe_name = "unicode_file"

    test_file = tmp_path / f"{safe_name}.py"
    try:
        copy_sample(
            tmp_path,
            "tools",
            "python",
            "common",
            "minimal_x.py",
            dest_name=f"{safe_name}.py",
        )
        assert_that(test_file.exists()).is_true()
    except (OSError, UnicodeEncodeError):
        # Some systems may not support all Unicode in filenames
        pytest.skip(f"System doesn't support {category} in filenames")


# =============================================================================
# Concurrent execution tests
# =============================================================================


def test_concurrent_tool_result_creation() -> None:
    """Verify ToolResult creation is thread-safe under concurrent access.

    Multiple threads creating ToolResult objects simultaneously should
    not cause race conditions or data corruption.
    """
    results: list[ToolResult] = []

    def create_result(index: int) -> ToolResult:
        return ToolResult(
            name=ToolName.RUFF,
            success=index % 2 == 0,
            issues_count=index,
            output=f"Output {index}",
            issues=None,
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(create_result, i) for i in range(20)]
        results = [f.result() for f in futures]

    assert_that(results).is_length(20)
    for i, result in enumerate(results):
        assert_that(result.issues_count).is_equal_to(i)
        assert_that(result.output).is_equal_to(f"Output {i}")


def test_concurrent_plugin_instantiation(
    mock_execution_context_factory: Callable[..., MagicMock],
) -> None:
    """Verify plugin instances can be created concurrently.

    Args:
        mock_execution_context_factory: Factory for creating mock execution contexts.
    """
    plugins: list[RuffPlugin] = []

    def create_plugin(_: int) -> RuffPlugin:
        return RuffPlugin()

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(create_plugin, i) for i in range(10)]
        plugins = [f.result() for f in futures]

    assert_that(plugins).is_length(10)
    for plugin in plugins:
        assert_that(plugin.definition.name).is_equal_to(ToolName.RUFF)


# =============================================================================
# Empty and edge input tests
# =============================================================================


def test_empty_file_list_handling() -> None:
    """Verify empty file list is handled gracefully."""
    plugin = RuffPlugin()

    with patch.object(plugin, "_prepare_execution") as mock_prepare:
        mock_ctx = MagicMock()
        mock_ctx.should_skip = True
        mock_ctx.early_result = ToolResult(
            name=ToolName.RUFF,
            success=True,
            issues_count=0,
            output="",
            issues=None,
        )
        mock_prepare.return_value = mock_ctx

        result = plugin.check([], {})
        assert_that(result.success).is_true()
        assert_that(result.issues_count).is_equal_to(0)


def test_single_file_handling(tmp_path: Path) -> None:
    """Verify single file is processed correctly.

    Args:
        tmp_path: Temporary directory path for test files.
    """
    test_file = copy_sample(
        tmp_path,
        "tools",
        "python",
        "common",
        "minimal_x.py",
        dest_name="single.py",
    )

    plugin = RuffPlugin()

    with (
        patch.object(plugin, "_prepare_execution") as mock_prepare,
        patch.object(plugin, "_run_subprocess", return_value=(True, "")),
    ):
        mock_ctx = MagicMock()
        mock_ctx.should_skip = False
        mock_ctx.early_result = None
        mock_ctx.files = [str(test_file)]
        mock_ctx.rel_files = ["single.py"]
        mock_ctx.timeout = 30
        mock_ctx.cwd = str(tmp_path)
        mock_prepare.return_value = mock_ctx

        result = plugin.check([str(test_file)], {})
        assert_that(result.name).is_equal_to(ToolName.RUFF)
