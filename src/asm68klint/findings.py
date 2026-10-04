"""The findings a lint run reports."""

from dataclasses import dataclass

from asm68klint.rules import ERROR, RULES, WARNING

__all__ = ["ERROR", "WARNING", "Finding"]


@dataclass(frozen=True)
class Finding:
    """One problem found, printed as ``file:line: severity: code message``."""

    file: str
    line: int
    code: str
    message: str

    def __post_init__(self) -> None:
        if self.code not in RULES:
            raise ValueError(f"{self.code!r} is not a rule")

    @property
    def severity(self) -> str:
        """The severity of the rule the finding belongs to."""
        return RULES[self.code].severity

    def __str__(self) -> str:
        return f"{self.file}:{self.line}: {self.severity}: {self.code} {self.message}"
