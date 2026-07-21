"""Ecosystem adapters that collect package license information."""

from lintro.licenses.ecosystems.npm import NpmLicenseAdapter
from lintro.licenses.ecosystems.python import PythonLicenseAdapter

__all__ = [
    "NpmLicenseAdapter",
    "PythonLicenseAdapter",
]
