"""Register names and register lists."""

import re
from collections.abc import Iterable

DATA = tuple(f"d{n}" for n in range(8))
ADDRESS = tuple(f"a{n}" for n in range(8))
FLOAT = tuple(f"fp{n}" for n in range(8))
REGISTERS = DATA + ADDRESS + FLOAT
STACK = "a7"

REGISTER_PATTERN = r"(?:[da][0-7]|sp|fp[0-7])"
LIST_PATTERN = rf"{REGISTER_PATTERN}(?:\s*[-/]\s*{REGISTER_PATTERN})*"
_LIST = re.compile(LIST_PATTERN, re.IGNORECASE)


def canonical(name: str) -> str | None:
    """Return the register a name stands for (``SP`` is ``a7``), or None."""
    name = name.strip().lower()
    if name == "sp":
        return STACK
    return name if name in REGISTERS else None


def parse_list(text: str) -> list[str] | None:
    """Parse a register list such as ``d0-d2/a0`` into register names, in order.

    Returns None when the text is not a register list.
    """
    text = text.strip()
    if not _LIST.fullmatch(text):
        return None
    found: set[str] = set()
    for part in text.split("/"):
        ends = [REGISTERS.index(canonical(name) or "") for name in part.split("-")]
        if sorted(ends) != ends or (ends[0] < 16) != (ends[-1] < 16):
            return None
        found.update(REGISTERS[ends[0] : ends[-1] + 1])
    return [register for register in REGISTERS if register in found]


def format_list(registers: Iterable[str]) -> str:
    """Write registers as a list such as ``d0-d2/a0``; ``-`` for none."""
    parts = []
    for group in (DATA, ADDRESS, FLOAT):
        run: list[str] = []
        for register in [*group, ""]:
            if register in registers:
                run.append(register)
            elif run:
                parts.append(run[0] if len(run) == 1 else f"{run[0]}-{run[-1]}")
                run = []
    return "/".join(parts) or "-"
