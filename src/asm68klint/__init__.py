"""Check 68000 assembly routine headers against what the code does to registers."""

from asm68klint.findings import ERROR, WARNING, Finding
from asm68klint.linter import lint_files

__all__ = ["ERROR", "WARNING", "Finding", "lint_files"]
