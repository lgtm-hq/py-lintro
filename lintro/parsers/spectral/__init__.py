"""Parser package for Spectral OpenAPI/AsyncAPI linter output."""

from lintro.parsers.spectral.spectral_issue import SpectralIssue
from lintro.parsers.spectral.spectral_parser import parse_spectral_output

__all__ = ["SpectralIssue", "parse_spectral_output"]
