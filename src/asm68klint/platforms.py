"""What the linter knows about the machines 68000 programs are written for.

A platform says what a call into its operating system does to the registers,
and names the macros of its include files that put nothing into the program,
so that source can be checked without those files.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass

from asm68klint.flow import Effect
from asm68klint.source import Statement, words

_LIBRARY_CALL = re.compile(
    r"(_LVO\w+|-\$?\w+)\(a6\)|\((_LVO\w+|-\$?\w+),a6\)", re.IGNORECASE
)
_TRAP = re.compile(r"#\$?0*([0-9a-f]+)", re.IGNORECASE)


# Functions of the Amiga's libraries that keep every register: the system's
# documentation says so of WaitBlit, and since Kickstart 2.04 of the other four.
KEEP_ALL = ("Forbid", "Permit", "Disable", "Enable", "WaitBlit")


def registers(text: str) -> frozenset[str]:
    """Return a set of registers from their names."""
    return frozenset(text.split())


def amiga_call(statement: Statement) -> Effect | None:
    """A call through a library base in a6: ``jsr _LVOOpen(a6)``, ``jsr -30(a6)``.

    The system's functions may change d0, d1, a0 and a1 and keep the rest; d0
    is where a result comes back.
    """
    if statement.mnemonic not in ("jsr", "jmp") or len(statement.operands) != 1:
        return None
    match = _LIBRARY_CALL.fullmatch(statement.operands[0])
    if not match:
        return None
    name = (match.group(1) or match.group(2)).removeprefix("_LVO")
    if name in KEEP_ALL:
        return Effect(frozenset(), registers("a6"))
    return Effect(registers("d0 d1 a0 a1"), registers("a6"), registers("d1 a0 a1"))


def atari_call(statement: Statement) -> Effect | None:
    """A call of GEMDOS, the BIOS or the XBIOS: ``trap #1``, ``#13``, ``#14``.

    They may change d0 to d2 and a0 to a2; d0 is where a result comes back.
    """
    match = _TRAP.fullmatch("".join(statement.operands))
    if statement.mnemonic != "trap" or not match:
        return None
    number = "".join(statement.operands).lstrip("#")
    value = int(number[1:], 16) if number.startswith("$") else int(number)
    if value not in (1, 13, 14):
        return None
    return Effect(
        registers("d0 d1 d2 a0 a1 a2"), frozenset(), registers("d1 d2 a0 a1 a2")
    )


@dataclass(frozen=True)
class Platform:
    """One machine: its name and what is known about it."""

    name: str
    summary: str
    call: Callable[[Statement], Effect | None]
    # Macros of the system's include files that define names and emit nothing.
    silent: frozenset[str] = frozenset()


PLATFORMS = {
    platform.name: platform
    for platform in (
        Platform(
            "amiga",
            "library calls through a6; the macros of exec/types.i",
            amiga_call,
            frozenset(
                words(
                    "structure label struct aptr bptr cptr fptr rptr bstr"
                    " long ulong word uword byte ubyte short ushort bool char"
                    " float double enum eitem bitdef libinit libdef"
                    " funcdef fd_data alignword alignlong"
                )
            ),
        ),
        Platform("atari", "the traps of GEMDOS, the BIOS and the XBIOS", atari_call),
    )
}
