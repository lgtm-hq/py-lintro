"""Unit tests for rustc version handling.

Rustc is not a standalone Lintro tool plugin; it is the compiler toolchain
whose version gates Clippy (see ``lintro.tools.definitions.clippy``). These
tests cover rustc's version parsing and install-hint wiring in isolation
from Clippy's own execution tests.
"""
