"""Follow a routine's code and find registers read while they hold nothing.

A register holds nothing of use on entry unless the header lists it under
``In``, and after a call to a routine that lists it under ``Clobbers``. Reading
it before something has been written to it is reported, and so is returning
with an ``Out`` register in that state.
"""

import re
from dataclasses import dataclass, field

from asm68klint.findings import Finding
from asm68klint.flow import Node, successors
from asm68klint.graph import Graph
from asm68klint.m68k import read_registers, written_registers
from asm68klint.registers import REGISTERS, STACK
from asm68klint.routines import Routine
from asm68klint.source import problem

# Why a register holds nothing of use is the index of the node whose call
# clobbered it, or one of these.
ENTRY = -1  # it is not an input of the routine
INPUT = -2  # not a reason: it holds an input that nothing has read yet
Marks = frozenset[tuple[str, int]]

_PUSH = re.compile(r"-\(\s*(sp|a7)\s*\)", re.IGNORECASE)
SAVES = ("move", "movea", "movem")


@dataclass
class Reads:
    """What the configurations of a routine checked so far have shown."""

    findings: set[Finding] = field(default_factory=set)
    used: set[str] = field(default_factory=set)  # inputs that are read
    # Registers read that were not written first and are not listed as inputs.
    missing: set[str] = field(default_factory=set)


def is_save(node: Node) -> bool:
    """True for an instruction that only puts registers on the stack."""
    statement = node.statement
    if statement.mnemonic == "link":
        return True
    pushes = bool(statement.operands) and _PUSH.fullmatch(statement.operands[-1])
    return statement.mnemonic in SAVES and bool(pushes)


def uses(node: Node, routine: Routine) -> dict[str, str]:
    """Return the registers a node needs a value in, and what for."""
    needed: dict[str, str] = {}
    if node.leaves in ("rts", "tail"):
        # An Out register that is under Clobbers too is not always a result.
        header = routine.header
        results = header.registers("Out") - header.registers("Clobbers")
        needed.update(dict.fromkeys(results, "returned"))
    for call in node.calls:
        outputs = call.effect.changed - call.effect.garbage
        for register in outputs:
            needed.pop(register, None)
        # What the instruction itself writes, it writes before the other code
        # runs: the last instruction before falling into the next routine.
        inputs = call.effect.inputs - written_registers(node.statement)
        needed.update(dict.fromkeys(inputs, f"needed by {call.via}"))
    if not is_save(node):
        needed.update(dict.fromkeys(read_registers(node.statement), "read"))
    needed.pop(STACK, None)
    return needed


def step(marks: Marks, node: Node) -> Marks:
    """Return what holds nothing of use after a node."""
    if node.kind != "code":
        return marks
    lost: set[str] = set()
    written = written_registers(node.statement)
    for call in node.calls:
        if not call.tail and call.effect.returns:  # a jump does not come back
            written |= call.effect.changed
            lost |= call.effect.garbage
    kept = {(register, why) for register, why in marks if register not in written}
    return frozenset(kept | {(register, node.index) for register in lost})


def lost_values(
    nodes: list[Node], node: Node, routine: Routine, marks: Marks
) -> list[tuple[str, int, Finding]]:
    """Report the registers a node uses that hold nothing of use.

    Each finding comes with the register and the reason it holds nothing.
    """
    title = routine.header.title
    needed = uses(node, routine)
    found = []
    for call in node.calls:
        header = routine.header
        results = header.registers("Out") - header.registers("Clobbers")
        wanted = results & call.effect.garbage
        for register in sorted(wanted if call.tail and call.effect.returns else ()):
            message = f"{register} is returned by {title} after {call.via} clobbers it"
            found.append((register, node.index, ("R007", message)))
    for register, why in sorted(marks):
        how = needed.get(register)
        if how is None or why == INPUT:
            continue
        if why != ENTRY:
            calls = [c for c in nodes[why].calls if register in c.effect.garbage]
            message = f"{register} is {how} after {calls[0].via} clobbered it"
            found.append((register, why, ("R007", message)))
        elif how == "returned":
            message = f"Out register {register} of {title} is not set on every path"
            found.append((register, why, ("R009", message)))
        else:
            message = f"{register} is {how} but not listed under In of {title}"
            found.append((register, why, ("R008", message)))
    return [
        (register, why, problem(node.statement, code, message))
        for register, why, (code, message) in found
    ]


def check_reads(
    routine: Routine,
    graph: Graph,
    choices: dict[str, bool],
    reserved: set[str],
    reads: Reads,
) -> None:
    """Check one configuration of a routine for reads of what holds nothing.

    Only the first read of a register is reported for each reason it holds
    nothing: the rest follow from it.
    """
    nodes = graph.nodes
    if not nodes:
        return
    inputs = routine.header.registers("In")
    always = inputs | reserved | {STACK}
    start = {(register, ENTRY) for register in REGISTERS if register not in always}
    start |= {(register, INPUT) for register in inputs}
    states: list[Marks | None] = [None] * len(nodes)
    states[0] = frozenset(start)
    pending = [0]
    while pending:
        index = pending.pop()
        after = step(states[index] or frozenset(), nodes[index])
        for successor in successors(nodes[index], choices):
            before = states[successor]
            merged = after if before is None else before | after
            if merged != before:
                states[successor] = merged
                pending.append(successor)
    seen: set[tuple[str, int]] = set()
    for node, marks in zip(nodes, states, strict=True):
        if marks is None or node.kind != "code":
            continue
        needed = uses(node, routine)
        reads.used |= {register for register, why in marks if why == INPUT} & set(
            needed
        )
        for register, why, finding in lost_values(nodes, node, routine, marks):
            if why == ENTRY and finding.code == "R008":
                reads.missing.add(register)
            if (register, why) not in seen:
                seen.add((register, why))
                reads.findings.add(finding)


def live_registers(
    routine: Routine, graph: Graph, choices: dict[str, bool]
) -> list[set[str]]:
    """Return, for each node, the registers whose value is still needed there.

    A value is needed when something later reads it before writing the
    register: an instruction, a call that takes it, or the return, which needs
    the ``Out`` registers and every register the routine may not change. A
    register that is not needed is free: new code there may use it. A call
    that may read more than is known of it needs every register.
    """
    nodes = graph.nodes
    header = routine.header
    kept = set(REGISTERS) - (header.registers("Clobbers") - header.registers("Out"))
    needed: list[set[str]] = []
    written: list[set[str]] = []
    for node in nodes:
        reads = set(uses(node, routine)) if node.kind == "code" else set()
        changed = written_registers(node.statement) if node.kind == "code" else set()
        if node.kind == "code" and is_save(node):
            reads |= read_registers(node.statement)
        for call in node.calls:
            reads |= set() if call.effect.inputs_known else set(REGISTERS)
            comes_back = not call.tail and call.effect.returns
            changed |= call.effect.changed if comes_back else set()
        if node.leaves:
            reads |= kept if node.leaves != "tail" else kept - _results(node)
        needed.append(reads - {STACK})
        written.append(changed)
    live: list[set[str]] = [set() for _ in nodes]
    moving = True
    while moving:
        moving = False
        for node in reversed(nodes):
            after: set[str] = set()
            for successor in successors(node, choices):
                after |= live[successor]
            before = needed[node.index] | (after - written[node.index])
            if before != live[node.index]:
                live[node.index] = before
                moving = True
    return live


def _results(node: Node) -> set[str]:
    """Return the registers that the code a node jumps to leaves changed."""
    changed: set[str] = set()
    for call in node.calls:
        comes_back = call.tail and call.effect.returns
        changed |= call.effect.changed if comes_back else set()
    return changed
