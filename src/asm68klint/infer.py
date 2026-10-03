"""Routines without headers: where they start.

Code that has no headers is still made of routines. A global label starts one
when it is called, exported, jumped to from another routine, or can only be
reached in a way the source does not show (nothing falls into it and nothing
in its routine branches to it: an entry of a jump table, an interrupt
handler). Every other global label is a place inside the routine it is in.
"""

from asm68klint.directives import code_label, is_data
from asm68klint.flow import direct_target
from asm68klint.m68k import BRANCHES, LOOPS, is_instruction
from asm68klint.routines import Routine, find_routines
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


def labels_of_code(statements: list[Statement]) -> set[str]:
    """Return the global labels that have an instruction after them, not data."""
    found: set[str] = set()
    waiting: list[str] = []
    for statement in statements:
        label = code_label(statement)
        if label:
            waiting.append(label)
        if is_instruction(statement.mnemonic):
            found.update(waiting)
            waiting = []
        elif is_data(statement):
            waiting = []
    return found


def find_starts(units: list[list[Statement]]) -> list[set[str]]:
    """Return, for each source file, the labels that start a routine."""
    wanted: set[str] = set()  # called or exported somewhere
    jumps: list[set[str]] = []  # the labels each file jumps to
    for statements in units:
        jumps.append(set())
        for statement in statements:
            name = target(statement)
            if name and statement.mnemonic in CALLS:
                wanted.add(name)
            elif name:
                jumps[-1].add(name)
            if statement.mnemonic in EXPORTS:
                wanted.update(statement.operands)
    result = []
    for jumped, statements in zip(jumps, units, strict=True):
        others = set().union(*(found for found in jumps if found is not jumped))
        code = labels_of_code(statements)
        starts = code & (wanted | others)
        while _add_missed(statements, starts, code):
            pass
        result.append(starts)
    return result


def _add_missed(statements: list[Statement], starts: set[str], code: set[str]) -> bool:
    """Add the labels that must start a routine as the routines now are.

    Those are the labels jumped to from another routine of the file, and the
    labels that nothing in their own routine reaches.
    """
    routines, _ = find_routines(statements, starts)
    home: dict[str, int] = {}
    for index, routine in enumerate(routines):
        for statement in routine.body:
            if code_label(statement):
                home[statement.label or ""] = index
    found: set[str] = set()
    for index, routine in enumerate(routines):
        for statement in routine.body:
            name = target(statement)
            if name in home and home[name] != index:
                found.add(name or "")
        found |= _unreached(routine)
    found = (found & code) - starts
    starts |= found
    return bool(found)


def _unreached(routine: Routine) -> set[str]:
    """Return the first global label in a routine that its code does not get to.

    This follows the code roughly: conditional assembly is ignored, and a
    branch goes both ways.
    """
    body = routine.body
    places: dict[str, int] = {}
    scope = ""
    scopes = []
    for index, statement in enumerate(body):
        if code_label(statement):
            scope = statement.label or ""
            places[scope] = index
        elif statement.label and is_local(statement.label):
            places[scope + statement.label] = index
        scopes.append(scope)
    reached = [False] * len(body)
    pending = [0] if body else []
    while pending:
        index = pending.pop()
        while index < len(body) and not reached[index]:
            reached[index] = True
            statement = body[index]
            mnemonic = statement.mnemonic
            if is_data(statement):
                break
            if mnemonic in JUMPS and statement.operands:
                name = direct_target(statement.operands[-1]) or ""
                if "\\" in name:
                    name = name.replace("\\", "")
                elif is_local(name):
                    name = scopes[index] + name
                if name in places:
                    pending.append(places[name])
            if mnemonic in NO_FALL_THROUGH:
                break
            index += 1
    for index, statement in enumerate(body):
        if code_label(statement) and not reached[index]:
            return {statement.label or ""}  # what follows it may be reached from it
    return set()
