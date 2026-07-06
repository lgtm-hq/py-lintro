"""Data-driven tests for workflow script-reference grouping.

Each :class:`WorkflowGroupingCase` describes a changed workflow plus the files
changed alongside it and the expected chunking outcome: which paths land in the
workflow group, which are split out, and whether an unreferenced-script warning
is emitted. The single irregular scenario (a composite action with mixed file
statuses) stays as a standalone test below the table.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from assertpy import assert_that

from lintro.ai.review.chunker import chunk_review_context
from lintro.ai.review.classifier import classify_changed_files
from lintro.ai.review.models.changed_file import ChangedFile
from tests.unit.ai.review.review_fixtures import (
    load_review_fixture,
    make_review_context,
)

_DEFAULT_WORKFLOW = ".github/workflows/ci.yml"


@dataclass(frozen=True)
class WorkflowGroupingCase:
    """One workflow/script grouping scenario.

    Attributes:
        id: Stable parametrization id (also the historical test-name suffix).
        fixture: Review diff fixture loaded for the scenario.
        files: Paths changed alongside the workflow (all ``modified``, +1/-0).
        anchor: Workflow path used to locate the produced chunk.
        contains: Paths expected to share the anchor's chunk.
        not_contains: Paths expected to be absent from the anchor's chunk.
        grouped_separately: Script that must own a lone chunk without the anchor.
        warning: Expected substring of an emitted warning, if ``check_warnings``.
        check_warnings: Whether warnings are asserted (empty when ``warning`` is None).
        post_image: ``(path, contents)`` pairs for post-image file bodies.
    """

    id: str
    fixture: str
    files: tuple[str, ...]
    anchor: str = _DEFAULT_WORKFLOW
    contains: tuple[str, ...] = ()
    not_contains: tuple[str, ...] = ()
    grouped_separately: str | None = None
    warning: str | None = None
    check_warnings: bool = True
    post_image: tuple[tuple[str, str], ...] = field(default_factory=tuple)


CASES: tuple[WorkflowGroupingCase, ...] = (
    WorkflowGroupingCase(
        id="groups_workflow_with_suffixless_bin_executable",
        fixture="chunk_groups_workflow_with_suffixless_bin_executable.diff",
        files=(".github/workflows/ci.yml", "bin/lintro"),
        contains=("bin/lintro",),
        check_warnings=False,
    ),
    WorkflowGroupingCase(
        id="groups_nested_workflow_with_referenced_script",
        fixture="chunk_groups_nested_workflow_with_referenced_script.diff",
        files=(".github/workflows/actions/reusable.yml", "scripts/ci/run.sh"),
        anchor=".github/workflows/actions/reusable.yml",
        contains=(".github/workflows/actions/reusable.yml",),
        check_warnings=False,
    ),
    WorkflowGroupingCase(
        id="groups_workflow_with_local_action_directory_reference",
        fixture="chunk_groups_workflow_with_local_action_directory_reference.diff",
        files=(".github/workflows/ci.yml", ".github/actions/setup/index.js"),
        contains=(".github/workflows/ci.yml",),
        check_warnings=False,
    ),
    WorkflowGroupingCase(
        id="groups_workflow_with_action_dist_implementation",
        fixture="chunk_groups_workflow_with_action_dist_implementation.diff",
        files=(".github/workflows/ci.yml", ".github/actions/setup/dist/index.js"),
        contains=(".github/workflows/ci.yml",),
    ),
    WorkflowGroupingCase(
        id="groups_workflow_with_action_dist_chunks_implementation",
        fixture="chunk_groups_workflow_with_action_dist_chunks_implementation.diff",
        files=(
            ".github/workflows/ci.yml",
            ".github/actions/setup/dist/chunks/index.js",
        ),
        contains=(".github/actions/setup/dist/chunks/index.js",),
    ),
    WorkflowGroupingCase(
        id="groups_workflow_with_action_dist_manifest",
        fixture="chunk_groups_workflow_with_action_dist_manifest.diff",
        files=(".github/workflows/ci.yml", ".github/actions/setup/dist/action.yml"),
        contains=(".github/actions/setup/dist/action.yml",),
    ),
    WorkflowGroupingCase(
        id="groups_workflow_with_node_script",
        fixture="chunk_groups_workflow_with_node_script.diff",
        files=(".github/workflows/ci.yml", "scripts/build.js"),
        contains=("scripts/build.js",),
    ),
    WorkflowGroupingCase(
        id="groups_workflow_with_bun_script",
        fixture="chunk_groups_workflow_with_bun_script.diff",
        files=(".github/workflows/ci.yml", "scripts/review.ts"),
        contains=("scripts/review.ts",),
    ),
    WorkflowGroupingCase(
        id="groups_workflow_with_bun_run_script",
        fixture="chunk_groups_workflow_with_bun_run_script.diff",
        files=(".github/workflows/ci.yml", "scripts/review.ts"),
        contains=("scripts/review.ts",),
    ),
    WorkflowGroupingCase(
        id="groups_workflow_with_node_loader_script",
        fixture="chunk_groups_workflow_with_node_loader_script.diff",
        files=(".github/workflows/ci.yml", "scripts/build.ts"),
        contains=("scripts/build.ts",),
    ),
    WorkflowGroupingCase(
        id="does_not_group_script_after_node_version_flag",
        fixture="chunk_does_not_group_script_after_node_version_flag.diff",
        files=(".github/workflows/ci.yml", "scripts/build.js"),
        not_contains=("scripts/build.js",),
        grouped_separately="scripts/build.js",
        check_warnings=False,
    ),
    WorkflowGroupingCase(
        id="groups_workflow_with_uv_run_with_editable_script",
        fixture="chunk_groups_workflow_with_uv_run_with_editable_script.diff",
        files=(".github/workflows/ci.yml", "scripts/review.py"),
        contains=("scripts/review.py",),
    ),
    WorkflowGroupingCase(
        id="groups_workflow_with_action_src_implementation",
        fixture="chunk_groups_workflow_with_action_src_implementation.diff",
        files=(".github/workflows/ci.yml", ".github/actions/setup/src/index.js"),
        contains=(".github/actions/setup/src/index.js",),
    ),
    WorkflowGroupingCase(
        id="groups_workflow_with_nested_action_src_commands",
        fixture="chunk_groups_workflow_with_nested_action_src_commands.diff",
        files=(".github/workflows/ci.yml", ".github/actions/setup/src/commands/run.js"),
        contains=(".github/actions/setup/src/commands/run.js",),
    ),
    WorkflowGroupingCase(
        id="groups_workflow_with_nested_team_setup_dist",
        fixture="chunk_groups_workflow_with_nested_team_setup_dist.diff",
        files=(".github/workflows/ci.yml", ".github/actions/team/setup/dist/index.js"),
        contains=(".github/actions/team/setup/dist/index.js",),
    ),
    WorkflowGroupingCase(
        id="does_not_group_internal_dist_with_parent_uses",
        fixture="chunk_does_not_group_internal_dist_with_parent_uses.diff",
        files=(
            ".github/workflows/ci.yml",
            ".github/actions/setup/internal/dist/index.js",
        ),
        not_contains=(".github/actions/setup/internal/dist/index.js",),
        grouped_separately=".github/actions/setup/internal/dist/index.js",
        check_warnings=False,
    ),
    WorkflowGroupingCase(
        id="groups_workflow_with_uv_run_quiet_script",
        fixture="chunk_groups_workflow_with_uv_run_quiet_script.diff",
        files=(".github/workflows/ci.yml", "scripts/review.py"),
        contains=("scripts/review.py",),
    ),
    WorkflowGroupingCase(
        id="groups_workflow_with_uv_run_python_short_script",
        fixture="chunk_groups_workflow_with_uv_run_python_short_script.diff",
        files=(".github/workflows/ci.yml", "scripts/review.py"),
        contains=("scripts/review.py",),
    ),
    WorkflowGroupingCase(
        id="groups_workflow_with_workspace_prefixed_script",
        fixture="chunk_groups_workflow_with_workspace_prefixed_script.diff",
        files=(".github/workflows/ci.yml", "scripts/ci/run.sh"),
        contains=("scripts/ci/run.sh",),
        check_warnings=False,
    ),
    WorkflowGroupingCase(
        id="groups_workflow_with_bash_wrapped_script",
        fixture="chunk_groups_workflow_with_bash_wrapped_script.diff",
        files=(".github/workflows/ci.yml", "scripts/deploy.sh"),
        contains=("scripts/deploy.sh",),
    ),
    WorkflowGroupingCase(
        id="groups_workflow_with_bash_flag_wrapped_script",
        fixture="chunk_groups_workflow_with_bash_flag_wrapped_script.diff",
        files=(".github/workflows/ci.yml", "scripts/deploy.sh"),
        contains=("scripts/deploy.sh",),
    ),
    WorkflowGroupingCase(
        id="groups_workflow_with_node_equals_flag_script",
        fixture="chunk_groups_workflow_with_node_equals_flag_script.diff",
        files=(".github/workflows/ci.yml", "scripts/build.js"),
        contains=("scripts/build.js",),
    ),
    WorkflowGroupingCase(
        id="groups_workflow_with_bash_pipefail_wrapped_script",
        fixture="chunk_groups_workflow_with_bash_pipefail_wrapped_script.diff",
        files=(".github/workflows/ci.yml", "scripts/deploy.sh"),
        contains=("scripts/deploy.sh",),
    ),
    WorkflowGroupingCase(
        id="groups_workflow_with_uv_run_python_script",
        fixture="chunk_groups_workflow_with_uv_run_python_script.diff",
        files=(".github/workflows/ci.yml", "scripts/review.py"),
        contains=("scripts/review.py",),
    ),
    WorkflowGroupingCase(
        id="groups_workflow_with_action_dockerfile",
        fixture="chunk_groups_workflow_with_action_dockerfile.diff",
        files=(".github/workflows/ci.yml", ".github/actions/build/Dockerfile"),
        contains=(".github/actions/build/Dockerfile",),
        check_warnings=False,
    ),
    WorkflowGroupingCase(
        id="groups_workflow_with_nested_local_action",
        fixture="chunk_groups_workflow_with_nested_local_action.diff",
        files=(".github/workflows/ci.yml", ".github/actions/docker/build/Dockerfile"),
        contains=(".github/actions/docker/build/Dockerfile",),
    ),
    WorkflowGroupingCase(
        id="does_not_match_script_path_prefix_collisions",
        fixture="chunk_does_not_match_script_path_prefix_collisions.diff",
        files=(".github/workflows/ci.yml", "scripts/deploy.sh-old"),
        not_contains=("scripts/deploy.sh-old",),
        warning="Script scripts/deploy.sh-old changed alongside workflows but is not referenced in any changed workflow diff; grouped separately.",
    ),
    WorkflowGroupingCase(
        id="uses_full_workflow_text_for_unchanged_script_reference",
        fixture="chunk_uses_full_workflow_text_for_unchanged_script_reference.diff",
        files=(".github/workflows/ci.yml", "scripts/deploy.sh"),
        contains=("scripts/deploy.sh",),
        post_image=(
            (
                ".github/workflows/ci.yml",
                "name: CI\nenv:\n  CI: true\nrun: scripts/deploy.sh\n",
            ),
        ),
    ),
    WorkflowGroupingCase(
        id="ignores_script_paths_in_workflow_comments",
        fixture="chunk_ignores_script_paths_in_workflow_comments.diff",
        files=(".github/workflows/ci.yml", "scripts/old-deploy.sh"),
        not_contains=("scripts/old-deploy.sh",),
        warning="Script scripts/old-deploy.sh changed alongside workflows but is not referenced in any changed workflow diff; grouped separately.",
    ),
    WorkflowGroupingCase(
        id="groups_workflow_with_multiline_run_block",
        fixture="chunk_groups_workflow_with_multiline_run_block.diff",
        files=(".github/workflows/ci.yml", "scripts/deploy.sh"),
        contains=("scripts/deploy.sh",),
        post_image=(
            (".github/workflows/ci.yml", "name: CI\nrun: |\n  scripts/deploy.sh\n"),
        ),
    ),
    WorkflowGroupingCase(
        id="groups_workflow_with_adjacent_multiline_run_blocks",
        fixture="chunk_groups_workflow_with_adjacent_multiline_run_blocks.diff",
        files=(".github/workflows/ci.yml", "scripts/deploy.sh"),
        contains=("scripts/deploy.sh",),
        post_image=(
            (
                ".github/workflows/ci.yml",
                "name: CI\nrun: |\n  echo setup\nrun: |\n  scripts/deploy.sh\n",
            ),
        ),
    ),
    WorkflowGroupingCase(
        id="groups_workflow_with_multiline_workspace_script",
        fixture="chunk_groups_workflow_with_multiline_workspace_script.diff",
        files=(".github/workflows/ci.yml", "scripts/deploy.sh"),
        contains=("scripts/deploy.sh",),
        post_image=(
            (
                ".github/workflows/ci.yml",
                "name: CI\nrun: |\n  ${{ github.workspace }}/scripts/deploy.sh\n",
            ),
        ),
    ),
    WorkflowGroupingCase(
        id="does_not_match_run_prefix_collision",
        fixture="chunk_does_not_match_run_prefix_collision.diff",
        files=(".github/workflows/ci.yml", "scripts/deploy.sh"),
        not_contains=("scripts/deploy.sh",),
        warning="Script scripts/deploy.sh changed alongside workflows but is not referenced in any changed workflow diff; grouped separately.",
    ),
    WorkflowGroupingCase(
        id="ignores_commented_run_reference",
        fixture="chunk_ignores_commented_run_reference.diff",
        files=(".github/workflows/ci.yml", "scripts/deploy.sh"),
        not_contains=("scripts/deploy.sh",),
        warning="Script scripts/deploy.sh changed alongside workflows but is not referenced in any changed workflow diff; grouped separately.",
    ),
    WorkflowGroupingCase(
        id="ignores_echo_mention_in_multiline_run_block",
        fixture="chunk_multiline_echo_mention_not_reference.diff",
        files=(".github/workflows/ci.yml", "scripts/deploy.sh"),
        not_contains=("scripts/deploy.sh",),
        warning="Script scripts/deploy.sh changed alongside workflows but is not referenced in any changed workflow diff; grouped separately.",
        post_image=(
            (
                ".github/workflows/ci.yml",
                'name: CI\nrun: |\n  echo "see scripts/deploy.sh"\n',
            ),
        ),
    ),
    WorkflowGroupingCase(
        id="groups_workflow_with_uv_run_with_options",
        fixture="chunk_groups_workflow_with_uv_run_with_options.diff",
        files=(".github/workflows/ci.yml", "scripts/review.py"),
        contains=("scripts/review.py",),
    ),
    WorkflowGroupingCase(
        id="groups_workflow_with_multiline_cd_relative_script",
        fixture="chunk_groups_workflow_with_multiline_cd_relative_script.diff",
        files=(".github/workflows/ci.yml", "scripts/deploy.sh"),
        contains=("scripts/deploy.sh",),
        post_image=(
            (
                ".github/workflows/ci.yml",
                "name: CI\nrun: |\n  cd scripts\n  ./deploy.sh\n",
            ),
        ),
    ),
    WorkflowGroupingCase(
        id="groups_workflow_with_quoted_run_script",
        fixture="chunk_groups_workflow_with_quoted_run_script.diff",
        files=(".github/workflows/ci.yml", "scripts/deploy.sh"),
        contains=("scripts/deploy.sh",),
    ),
    WorkflowGroupingCase(
        id="groups_workflow_with_multiline_quoted_script",
        fixture="chunk_groups_workflow_with_multiline_quoted_script.diff",
        files=(".github/workflows/ci.yml", "scripts/deploy.sh"),
        contains=("scripts/deploy.sh",),
        post_image=(
            (
                ".github/workflows/ci.yml",
                'name: CI\nrun: |\n  bash "scripts/deploy.sh"\n',
            ),
        ),
    ),
    WorkflowGroupingCase(
        id="groups_workflow_with_inline_cd_run_script",
        fixture="chunk_groups_workflow_with_inline_cd_run_script.diff",
        files=(".github/workflows/ci.yml", "scripts/deploy.sh"),
        contains=("scripts/deploy.sh",),
    ),
    WorkflowGroupingCase(
        id="groups_deploy_before_cd_on_same_line",
        fixture="chunk_groups_workflow_with_inline_cd_run_script.diff",
        files=(".github/workflows/ci.yml", "scripts/deploy.sh"),
        not_contains=("scripts/deploy.sh",),
        warning="Script scripts/deploy.sh changed alongside workflows but is not referenced in any changed workflow diff; grouped separately.",
        post_image=(
            (".github/workflows/ci.yml", "name: CI\nrun: ./deploy.sh && cd scripts\n"),
        ),
    ),
    WorkflowGroupingCase(
        id="resets_cwd_between_multiline_run_blocks",
        fixture="chunk_separate_run_blocks_do_not_share_cwd.diff",
        files=(".github/workflows/ci.yml", "scripts/deploy.sh"),
        not_contains=("scripts/deploy.sh",),
        warning="Script scripts/deploy.sh changed alongside workflows but is not referenced in any changed workflow diff; grouped separately.",
        post_image=(
            (
                ".github/workflows/ci.yml",
                "name: CI\nrun: |\n  cd scripts\nrun: |\n  ./deploy.sh\n",
            ),
        ),
    ),
    WorkflowGroupingCase(
        id="or_cd_does_not_persist_cwd_for_later_commands",
        fixture="chunk_multiline_or_cd_does_not_persist_cwd.diff",
        files=(".github/workflows/ci.yml", "scripts/deploy.sh"),
        not_contains=("scripts/deploy.sh",),
        warning="Script scripts/deploy.sh changed alongside workflows but is not referenced in any changed workflow diff; grouped separately.",
        post_image=(
            (
                ".github/workflows/ci.yml",
                "name: CI\nrun: |\n  cd missing || true\n  ./deploy.sh\n",
            ),
        ),
    ),
    WorkflowGroupingCase(
        id="does_not_match_quoted_absolute_run_path",
        fixture="chunk_does_not_match_quoted_absolute_run_path.diff",
        files=(".github/workflows/ci.yml", "scripts/deploy.sh"),
        not_contains=("scripts/deploy.sh",),
        warning="Script scripts/deploy.sh changed alongside workflows but is not referenced in any changed workflow diff; grouped separately.",
    ),
    WorkflowGroupingCase(
        id="ignores_grep_script_mention",
        fixture="chunk_ignores_grep_script_mention.diff",
        files=(".github/workflows/ci.yml", "scripts/deploy.sh"),
        not_contains=("scripts/deploy.sh",),
        warning="Script scripts/deploy.sh changed alongside workflows but is not referenced in any changed workflow diff; grouped separately.",
    ),
    WorkflowGroupingCase(
        id="ignores_bash_c_script_mention",
        fixture="chunk_ignores_bash_c_script_mention.diff",
        files=(".github/workflows/ci.yml", "scripts/deploy.sh"),
        not_contains=("scripts/deploy.sh",),
        warning="Script scripts/deploy.sh changed alongside workflows but is not referenced in any changed workflow diff; grouped separately.",
    ),
    WorkflowGroupingCase(
        id="groups_workflow_with_uv_run_isolated_python",
        fixture="chunk_groups_workflow_with_uv_run_isolated_python.diff",
        files=(".github/workflows/ci.yml", "scripts/review.py"),
        contains=("scripts/review.py",),
    ),
    WorkflowGroupingCase(
        id="groups_indented_list_run_step",
        fixture="chunk_groups_indented_list_run_step.diff",
        files=(".github/workflows/ci.yml", "scripts/deploy.sh"),
        contains=("scripts/deploy.sh",),
    ),
    WorkflowGroupingCase(
        id="persists_cd_before_pipeline_in_multiline_block",
        fixture="chunk_groups_workflow_with_multiline_cd_relative_script.diff",
        files=(".github/workflows/ci.yml", "scripts/deploy.sh"),
        contains=("scripts/deploy.sh",),
        post_image=(
            (
                ".github/workflows/ci.yml",
                "name: CI\nrun: |\n  cd scripts && cat README | wc -l\n  ./deploy.sh\n",
            ),
        ),
    ),
    WorkflowGroupingCase(
        id="groups_workflow_with_python_warning_flag",
        fixture="chunk_groups_workflow_with_python_warning_flag.diff",
        files=(".github/workflows/ci.yml", "scripts/review.py"),
        contains=("scripts/review.py",),
    ),
    WorkflowGroupingCase(
        id="groups_workflow_with_bash_c_unquoted_script",
        fixture="chunk_groups_workflow_with_bash_c_unquoted_script.diff",
        files=(".github/workflows/ci.yml", "scripts/deploy.sh"),
        contains=("scripts/deploy.sh",),
    ),
    WorkflowGroupingCase(
        id="ignores_chmod_script_mention",
        fixture="chunk_ignores_chmod_script_mention.diff",
        files=(".github/workflows/ci.yml", "scripts/deploy.sh"),
        not_contains=("scripts/deploy.sh",),
        grouped_separately="scripts/deploy.sh",
        warning="Script scripts/deploy.sh changed alongside workflows but is not referenced in any changed workflow diff; grouped separately.",
    ),
    WorkflowGroupingCase(
        id="does_not_match_parent_relative_run_path",
        fixture="chunk_does_not_match_parent_relative_run_path.diff",
        files=(".github/workflows/ci.yml", "scripts/deploy.sh"),
        not_contains=("scripts/deploy.sh",),
        grouped_separately="scripts/deploy.sh",
        warning="Script scripts/deploy.sh changed alongside workflows but is not referenced in any changed workflow diff; grouped separately.",
    ),
    WorkflowGroupingCase(
        id="does_not_match_double_parent_relative_run_path",
        fixture="chunk_does_not_match_double_parent_relative_run_path.diff",
        files=(".github/workflows/ci.yml", "scripts/deploy.sh"),
        not_contains=("scripts/deploy.sh",),
        grouped_separately="scripts/deploy.sh",
        warning="Script scripts/deploy.sh changed alongside workflows but is not referenced in any changed workflow diff; grouped separately.",
    ),
    WorkflowGroupingCase(
        id="ignores_commented_multiline_run_block",
        fixture="chunk_ignores_commented_multiline_run_block.diff",
        files=(".github/workflows/ci.yml", "scripts/deploy.sh"),
        not_contains=("scripts/deploy.sh",),
        grouped_separately="scripts/deploy.sh",
        warning="Script scripts/deploy.sh changed alongside workflows but is not referenced in any changed workflow diff; grouped separately.",
        post_image=(
            (".github/workflows/ci.yml", "name: CI\n# run: |\n#   scripts/deploy.sh\n"),
        ),
    ),
    WorkflowGroupingCase(
        id="groups_workflow_with_env_assignment_prefixed_script",
        fixture="chunk_groups_workflow_with_env_prefixed_script.diff",
        files=(".github/workflows/ci.yml", "scripts/deploy.sh"),
        contains=("scripts/deploy.sh",),
    ),
    WorkflowGroupingCase(
        id="groups_workflow_with_env_command_prefixed_script",
        fixture="chunk_groups_workflow_with_env_command_prefixed_script.diff",
        files=(".github/workflows/ci.yml", "scripts/deploy.sh"),
        contains=("scripts/deploy.sh",),
    ),
    WorkflowGroupingCase(
        id="does_not_match_bash_s_script_argument",
        fixture="chunk_does_not_match_bash_s_script_argument.diff",
        files=(".github/workflows/ci.yml", "scripts/deploy.sh"),
        not_contains=("scripts/deploy.sh",),
        grouped_separately="scripts/deploy.sh",
        warning="Script scripts/deploy.sh changed alongside workflows but is not referenced in any changed workflow diff; grouped separately.",
    ),
    WorkflowGroupingCase(
        id="groups_workflow_with_bash_script_c_flag_argument",
        fixture="chunk_groups_workflow_with_bash_script_c_flag_argument.diff",
        files=(".github/workflows/ci.yml", "scripts/deploy.sh"),
        contains=("scripts/deploy.sh",),
    ),
    WorkflowGroupingCase(
        id="ignores_dry_run_multiline_block",
        fixture="chunk_ignores_dry_run_multiline_block.diff",
        files=(".github/workflows/ci.yml", "scripts/deploy.sh"),
        not_contains=("scripts/deploy.sh",),
        warning="Script scripts/deploy.sh changed alongside workflows but is not referenced in any changed workflow diff; grouped separately.",
    ),
    WorkflowGroupingCase(
        id="ignores_uses_in_if_expression",
        fixture="chunk_ignores_uses_in_if_expression.diff",
        files=(".github/workflows/ci.yml", "scripts/deploy.sh"),
        not_contains=("scripts/deploy.sh",),
        warning="Script scripts/deploy.sh changed alongside workflows but is not referenced in any changed workflow diff; grouped separately.",
    ),
    WorkflowGroupingCase(
        id="groups_indented_run_from_post_image_text",
        fixture="chunk_uses_post_image_indented_run_command.diff",
        files=(".github/workflows/ci.yml", "scripts/deploy.sh"),
        contains=("scripts/deploy.sh",),
        post_image=(
            (
                ".github/workflows/ci.yml",
                "name: CI\nsteps:\n      - run: bash -c 'scripts/deploy.sh'\n",
            ),
        ),
    ),
    WorkflowGroupingCase(
        id="groups_multiline_cd_fail_fast_script",
        fixture="chunk_groups_multiline_cd_fail_fast_script.diff",
        files=(".github/workflows/ci.yml", "scripts/deploy.sh"),
        contains=("scripts/deploy.sh",),
    ),
    WorkflowGroupingCase(
        id="groups_workflow_with_env_unset_prefixed_script",
        fixture="chunk_groups_workflow_with_env_unset_prefixed_script.diff",
        files=(".github/workflows/ci.yml", "scripts/deploy.sh"),
        contains=("scripts/deploy.sh",),
    ),
    WorkflowGroupingCase(
        id="groups_workflow_with_uv_run_direct_script",
        fixture="chunk_groups_workflow_with_uv_run_direct_script.diff",
        files=(".github/workflows/ci.yml", "scripts/review.py"),
        contains=("scripts/review.py",),
    ),
    WorkflowGroupingCase(
        id="groups_single_line_cd_fail_fast_script",
        fixture="chunk_groups_single_line_cd_fail_fast_script.diff",
        files=(".github/workflows/ci.yml", "scripts/deploy.sh"),
        contains=("scripts/deploy.sh",),
    ),
    WorkflowGroupingCase(
        id="ignores_run_text_in_name_field",
        fixture="chunk_ignores_run_text_in_name_field.diff",
        files=(".github/workflows/ci.yml", "scripts/deploy.sh"),
        not_contains=("scripts/deploy.sh",),
        warning="Script scripts/deploy.sh changed alongside workflows but is not referenced in any changed workflow diff; grouped separately.",
    ),
    WorkflowGroupingCase(
        id="groups_workflow_with_exec_prefixed_script",
        fixture="chunk_groups_workflow_with_exec_prefixed_script.diff",
        files=(".github/workflows/ci.yml", "scripts/deploy.sh"),
        contains=("scripts/deploy.sh",),
    ),
    WorkflowGroupingCase(
        id="groups_workflow_with_if_then_script",
        fixture="chunk_groups_workflow_with_if_then_script.diff",
        files=(".github/workflows/ci.yml", "scripts/deploy.sh"),
        contains=("scripts/deploy.sh",),
    ),
    WorkflowGroupingCase(
        id="groups_workflow_with_env_split_string_prefixed_script",
        fixture="chunk_groups_workflow_with_env_split_string_prefixed_script.diff",
        files=(".github/workflows/ci.yml", "scripts/deploy.sh"),
        contains=("scripts/deploy.sh",),
    ),
    WorkflowGroupingCase(
        id="groups_workflow_with_uv_run_python_option_script",
        fixture="chunk_groups_workflow_with_uv_run_python_option_script.diff",
        files=(".github/workflows/ci.yml", "scripts/review.py"),
        contains=("scripts/review.py",),
    ),
    WorkflowGroupingCase(
        id="does_not_group_nested_action_with_parent_uses",
        fixture="chunk_does_not_group_nested_action_with_parent_uses.diff",
        files=(".github/workflows/ci.yml", ".github/actions/setup/internal/run"),
        not_contains=(".github/actions/setup/internal/run",),
        grouped_separately=".github/actions/setup/internal/run",
        warning="Script .github/actions/setup/internal/run changed alongside workflows but is not referenced in any changed workflow diff; grouped separately.",
    ),
    WorkflowGroupingCase(
        id="groups_workflow_with_command_option_script",
        fixture="chunk_groups_workflow_with_command_option_script.diff",
        files=(".github/workflows/ci.yml", "scripts/deploy.sh"),
        contains=("scripts/deploy.sh",),
    ),
    WorkflowGroupingCase(
        id="groups_workflow_with_stacked_shell_wrappers",
        fixture="chunk_groups_workflow_with_stacked_shell_wrappers.diff",
        files=(".github/workflows/ci.yml", "scripts/deploy.sh"),
        contains=("scripts/deploy.sh",),
    ),
    WorkflowGroupingCase(
        id="groups_workflow_with_sudo_wrapped_script",
        fixture="chunk_groups_workflow_with_sudo_wrapped_script.diff",
        files=(".github/workflows/ci.yml", "scripts/deploy.sh"),
        contains=("scripts/deploy.sh",),
    ),
    WorkflowGroupingCase(
        id="groups_workflow_with_timeout_wrapped_script",
        fixture="chunk_groups_workflow_with_timeout_wrapped_script.diff",
        files=(".github/workflows/ci.yml", "scripts/deploy.sh"),
        contains=("scripts/deploy.sh",),
    ),
    WorkflowGroupingCase(
        id="groups_workflow_with_action_package_manifest",
        fixture="chunk_groups_workflow_with_action_package_manifest.diff",
        files=(".github/workflows/ci.yml", ".github/actions/setup/package.json"),
        contains=(".github/actions/setup/package.json",),
    ),
    WorkflowGroupingCase(
        id="groups_workflow_with_env_bash_c_wrapped_script",
        fixture="chunk_groups_workflow_with_env_bash_c_wrapped_script.diff",
        files=(".github/workflows/ci.yml", "scripts/deploy.sh"),
        contains=("scripts/deploy.sh",),
    ),
    WorkflowGroupingCase(
        id="groups_workflow_with_bash_clustered_c_flag_script",
        fixture="chunk_groups_workflow_with_bash_clustered_c_flag_script.diff",
        files=(".github/workflows/ci.yml", "scripts/deploy.sh"),
        contains=("scripts/deploy.sh",),
    ),
    WorkflowGroupingCase(
        id="groups_workflow_with_python_verbose_flag",
        fixture="chunk_groups_workflow_with_python_verbose_flag.diff",
        files=(".github/workflows/ci.yml", "scripts/review.py"),
        contains=("scripts/review.py",),
    ),
    WorkflowGroupingCase(
        id="groups_workflow_with_python_werror_flag",
        fixture="chunk_groups_workflow_with_python_werror_flag.diff",
        files=(".github/workflows/ci.yml", "scripts/review.py"),
        contains=("scripts/review.py",),
    ),
    WorkflowGroupingCase(
        id="groups_workflow_with_node_attached_preload_script",
        fixture="chunk_groups_workflow_with_node_attached_preload_script.diff",
        files=(".github/workflows/ci.yml", "scripts/build.ts"),
        contains=("scripts/build.ts",),
    ),
    WorkflowGroupingCase(
        id="groups_workflow_with_uv_run_bash_c_script",
        fixture="chunk_groups_workflow_with_uv_run_bash_c_script.diff",
        files=(".github/workflows/ci.yml", "scripts/deploy.sh"),
        contains=("scripts/deploy.sh",),
    ),
    WorkflowGroupingCase(
        id="does_not_group_script_after_python_command_string",
        fixture="chunk_does_not_group_script_after_python_command_string.diff",
        files=(".github/workflows/ci.yml", "scripts/review.py"),
        not_contains=("scripts/review.py",),
        grouped_separately="scripts/review.py",
        warning="Script scripts/review.py changed alongside workflows but is not referenced in any changed workflow diff; grouped separately.",
    ),
    WorkflowGroupingCase(
        id="does_not_group_script_after_node_eval_string",
        fixture="chunk_does_not_group_script_after_node_eval_string.diff",
        files=(".github/workflows/ci.yml", "scripts/build.js"),
        not_contains=("scripts/build.js",),
        grouped_separately="scripts/build.js",
        warning="Script scripts/build.js changed alongside workflows but is not referenced in any changed workflow diff; grouped separately.",
    ),
    WorkflowGroupingCase(
        id="does_not_group_script_after_node_eval_long_string",
        fixture="chunk_does_not_group_script_after_node_eval_long_string.diff",
        files=(".github/workflows/ci.yml", "scripts/build.js"),
        not_contains=("scripts/build.js",),
        grouped_separately="scripts/build.js",
        warning="Script scripts/build.js changed alongside workflows but is not referenced in any changed workflow diff; grouped separately.",
    ),
    WorkflowGroupingCase(
        id="does_not_group_script_after_bun_eval_string",
        fixture="chunk_does_not_group_script_after_bun_eval_string.diff",
        files=(".github/workflows/ci.yml", "scripts/review.ts"),
        not_contains=("scripts/review.ts",),
        grouped_separately="scripts/review.ts",
        warning="Script scripts/review.ts changed alongside workflows but is not referenced in any changed workflow diff; grouped separately.",
    ),
    WorkflowGroupingCase(
        id="does_not_group_script_after_python_attached_command_string",
        fixture="chunk_does_not_group_script_after_python_attached_command_string.diff",
        files=(".github/workflows/ci.yml", "scripts/review.py"),
        not_contains=("scripts/review.py",),
        grouped_separately="scripts/review.py",
        warning="Script scripts/review.py changed alongside workflows but is not referenced in any changed workflow diff; grouped separately.",
    ),
    WorkflowGroupingCase(
        id="does_not_group_script_after_node_attached_eval_string",
        fixture="chunk_does_not_group_script_after_node_attached_eval_string.diff",
        files=(".github/workflows/ci.yml", "scripts/build.js"),
        not_contains=("scripts/build.js",),
        grouped_separately="scripts/build.js",
        warning="Script scripts/build.js changed alongside workflows but is not referenced in any changed workflow diff; grouped separately.",
    ),
    WorkflowGroupingCase(
        id="groups_workflow_with_python_script",
        fixture="chunk_groups_workflow_with_python_script.diff",
        files=(".github/workflows/ci.yml", "scripts/review.py"),
        contains=("scripts/review.py",),
    ),
    WorkflowGroupingCase(
        id="ignores_folded_run_echo_script_mention",
        fixture="chunk_ignores_folded_run_echo_script_mention.diff",
        files=(".github/workflows/ci.yml", "scripts/deploy.sh"),
        not_contains=("scripts/deploy.sh",),
        warning="Script scripts/deploy.sh changed alongside workflows but is not referenced in any changed workflow diff; grouped separately.",
        post_image=(
            (
                ".github/workflows/ci.yml",
                "name: CI\n- run: >\n    echo\n    scripts/deploy.sh\n",
            ),
        ),
    ),
)


@pytest.mark.parametrize("case", CASES, ids=[case.id for case in CASES])
def test_workflow_script_grouping(case: WorkflowGroupingCase) -> None:
    """Changed scripts group with, or split from, the workflows that run them."""
    post_image_files = dict(case.post_image) if case.post_image else None
    context = make_review_context(
        unified_diff=load_review_fixture(case.fixture),
        changed_files=[
            ChangedFile(path=path, status="modified", additions=1, deletions=0)
            for path in case.files
        ],
        post_image_files=post_image_files,
    )
    classifications = classify_changed_files(files=context.changed_files)

    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    if case.grouped_separately is not None:
        owning = [
            chunk for chunk in result.chunks if case.grouped_separately in chunk.files
        ]
        assert_that(owning).is_length(1)
        assert_that(owning[0].files).does_not_contain(case.anchor)

    if case.contains or case.not_contains:
        group = next(chunk for chunk in result.chunks if case.anchor in chunk.files)
        for path in case.contains:
            assert_that(group.files).contains(path)
        for path in case.not_contains:
            assert_that(group.files).does_not_contain(path)

    if case.check_warnings:
        if case.warning is None:
            assert_that(result.warnings).is_empty()
        else:
            assert_that(result.warnings).contains(case.warning)


def test_workflow_groups_action_entrypoint_with_mixed_statuses() -> None:
    """Composite action metadata that points at ``run`` groups with the entrypoint."""
    context = make_review_context(
        unified_diff=load_review_fixture(
            "chunk_groups_workflow_with_action_entrypoint_script.diff",
        ),
        changed_files=[
            ChangedFile(
                path=".github/workflows/ci.yml",
                status="modified",
                additions=1,
                deletions=0,
            ),
            ChangedFile(
                path=".github/actions/setup/action.yml",
                status="added",
                additions=7,
                deletions=0,
            ),
            ChangedFile(
                path=".github/actions/setup/run",
                status="modified",
                additions=1,
                deletions=0,
            ),
        ],
    )
    classifications = classify_changed_files(files=context.changed_files)

    result = chunk_review_context(
        context=context,
        max_tokens=4096,
        classifications=classifications,
    )

    workflow_group = next(
        chunk for chunk in result.chunks if ".github/workflows/ci.yml" in chunk.files
    )
    assert_that(workflow_group.files).contains(".github/actions/setup/run")
    assert_that(workflow_group.files).contains(".github/actions/setup/action.yml")
    assert_that(result.warnings).is_empty()
