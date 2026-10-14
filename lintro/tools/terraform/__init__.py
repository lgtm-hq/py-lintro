"""Terraform tool package.

Everything the ``terraform`` tool owns lives here: the plugin and its
:class:`~lintro.plugins.protocol.ToolDefinition` in
:mod:`lintro.tools.terraform.definition`. Plugin discovery enters the package
through that module (#2311).
"""

from lintro.tools.terraform.definition import (
    TERRAFORM_DEFAULT_TIMEOUT,
    TERRAFORM_FILE_PATTERNS,
    TerraformPlugin,
)

__all__ = [
    "TERRAFORM_DEFAULT_TIMEOUT",
    "TERRAFORM_FILE_PATTERNS",
    "TerraformPlugin",
]
