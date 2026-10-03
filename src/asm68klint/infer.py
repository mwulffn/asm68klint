"""Routines without headers: where they start.

Code that has no headers is still made of routines. A global label starts one
when it is called, exported, jumped to from another routine, or can only be
reached in a way the source does not show (nothing falls into it and nothing
branches to it: an entry of a jump table, an interrupt handler). Every other
global label is a place inside the routine it is in.
"""

from asm68klint.directives import is_data
from asm68klint.flow import direct_target
from asm68klint.m68k import BRANCHES, LOOPS, is_instruction
from asm68klint.routines import find_routines
from asm68klint.source import Statement, is_local

CALLS = {"bsr", "jsr"}
JUMPS = {"bra", "jmp"} | BRANCHES | LOOPS
NO_FALL_THROUGH = {"bra", "jmp", "rts", "rte", "rtr"}
EXPORTS = ("xdef", "public", "global")


def target(statement: Statement) -> str | None:
    """Return the global label a call or jump goes to, if it names one."""
    if not statement.operands or statement.mnemonic not in CALLS | JUMPS:
        return None
    name = direct_target(statement.operands[-1])
    return name if name and not is_local(name) else None


def find_starts(units: list[list[Statement]]) -> list[set[str]]:
    """Return, for each source file, the labels that start a routine."""
    called: set[str] = set()
    exported: set[str] = set()
    jumps: list[set[str]] = []  # the labels each file jumps to
    for statements in units:
        jumps.append(set())
        for statement in statements:
            name = target(statement)
            if name and statement.mnemonic in CALLS:
                called.add(name)
            elif name:
                jumps[-1].add(name)
            if statement.mnemonic in EXPORTS:
                exported.update(statement.operands)
    result = []
    for jumped, statements in zip(jumps, units, strict=True):
        others = set().union(*(found for found in jumps if found is not jumped))
        starts: set[str] = set()
        falls = False  # execution can run from the statement before into this one
        waiting: list[str] = []  # labels whose code or data is still to come
        for statement in statements:
            label = statement.label
            if label and not is_local(label):
                hidden = not falls and label not in jumped
                if label in called | exported | others or hidden:
                    waiting.append(label)
            if is_instruction(statement.mnemonic):
                falls = statement.mnemonic not in NO_FALL_THROUGH
                starts.update(waiting)
                waiting = []
            elif is_data(statement):
                falls = False
                waiting = []  # labels of data
        while _add_crossings(statements, starts):
            pass
        result.append(starts)
    return result


def _add_crossings(statements: list[Statement], starts: set[str]) -> bool:
    """Make a start of every label jumped to from another routine of the file."""
    routines, _ = find_routines(statements, starts)
    home: dict[str, int] = {}
    for index, routine in enumerate(routines):
        for statement in routine.body:
            if statement.label and not is_local(statement.label):
                home[statement.label] = index
    found = False
    for index, routine in enumerate(routines):
        for statement in routine.body:
            name = target(statement)
            if name in home and home[name] != index and name not in starts:
                starts.add(name)
                found = True
    return found
