"""Routines without headers: where they start.

Code that has no headers is still made of routines. A global label starts one
when it is called, exported, jumped to from another file, or can only be
reached in a way the source does not show (nothing falls into it and nothing
branches to it: an entry of a jump table, an interrupt handler). Every other
global label is a place inside the routine it is in. Such a place may be
jumped to from another routine: its code is then followed from there too
(see ``build_graph``).
"""

from asm68klint.directives import EXPORTS, code_label, is_data
from asm68klint.m68k import (
    BRANCHES,
    CALLS,
    JUMPS,
    LOOPS,
    RETURNS,
    direct_target,
    is_instruction,
)
from asm68klint.routines import Routine, find_routines
from asm68klint.source import UNSCOPED, Statement, is_local, label_key

BRANCHING = JUMPS | BRANCHES | LOOPS  # all that may go to a label
NO_FALL_THROUGH = JUMPS | set(RETURNS)


def target(statement: Statement) -> str | None:
    """Return the global label a call or jump goes to, if it names one."""
    if not statement.operands or statement.mnemonic not in CALLS | BRANCHING:
        return None
    name = direct_target(statement.operands[-1])
    return name if name and place_label(name) else None


def place_label(label: str) -> bool:
    """True for a label that may start a routine: not one of a global label's."""
    return not is_local(label) or label.startswith(UNSCOPED)


def labels_of_code(statements: list[Statement]) -> set[str]:
    """Return the global labels that have an instruction after them, not data."""
    found: set[str] = set()
    waiting: list[str] = []
    for statement in statements:
        label = code_label(statement)
        if label:
            waiting.append(label)
        elif (statement.label or "").startswith(UNSCOPED):
            waiting.append(statement.label or "")
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

    Those are the labels that nothing reaches: not their own routine, and no
    jump from another.
    """
    routines, _ = find_routines(statements, starts)
    home: dict[str, int] = {}
    for index, routine in enumerate(routines):
        for statement in routine.body:
            if statement.label and place_label(statement.label):
                home[statement.label] = index
    # Labels that another routine jumps to: their code is followed from there.
    foreign: set[str] = set()
    for index, routine in enumerate(routines):
        for statement in routine.body:
            name = target(statement)
            if name in home and home[name] != index:
                foreign.add(name or "")
    found: set[str] = set()
    for routine in routines:
        found |= _unreached(routine, code, foreign)
    found = (found & code) - starts
    starts |= found
    return bool(found)


def _unreached(routine: Routine, code: set[str], foreign: set[str]) -> set[str]:
    """Return the global labels in a routine that must start routines of their own.

    Those are the labels its code does not get to: the first of them, then the
    first that neither the routine nor the code from that label gets to, and
    so on. This follows the code roughly: conditional assembly is ignored, and
    a branch goes both ways.
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
            places[label_key(scope, statement.label)] = index
        scopes.append(scope)
    candidates = [
        index for index, statement in enumerate(body) if code_label(statement) in code
    ]
    reached = [False] * len(body)
    found: set[str] = set()
    pending = [0] if body else []
    while True:
        while pending:
            index = pending.pop()
            while index < len(body) and not reached[index]:
                reached[index] = True
                statement = body[index]
                mnemonic = statement.mnemonic
                if is_data(statement):
                    break
                if mnemonic in BRANCHING and statement.operands:
                    name = direct_target(statement.operands[-1]) or ""
                    name = label_key(scopes[index], name)
                    if name in places:
                        pending.append(places[name])
                if mnemonic in NO_FALL_THROUGH:
                    break
                index += 1
        missed = next((index for index in candidates if not reached[index]), None)
        if missed is None:
            return found
        if body[missed].label not in foreign:
            found.add(body[missed].label or "")
        pending.append(missed)
