"""The checks of one routine: its code against its header."""

import itertools
from dataclasses import dataclass, field
from typing import Literal

from asm68klint.findings import Finding
from asm68klint.flow import State, analyse, step
from asm68klint.graph import Graph
from asm68klint.header import Field
from asm68klint.m68k import JUMPS, needs
from asm68klint.model import Exit, Kind, Node
from asm68klint.reads import Reads, check_reads
from asm68klint.registers import STACK
from asm68klint.routines import Routine, check_label
from asm68klint.source import Statement, problem

# With more different conditions of conditional assembly than this in one
# routine, their combinations are no longer checked one by one.
MAX_CONDITIONS = 8


@dataclass
class Summary:
    """What the configurations of a routine checked so far have shown."""

    findings: list[Finding] = field(default_factory=list)
    reached: set[int] = field(default_factory=set)  # nodes some path reaches
    changed: set[str] = field(default_factory=set)  # registers changed at an exit
    returns: bool = False
    is_interrupt: bool = False
    # False when some code could not be analysed, so that what the routine
    # changes is not fully known.
    analysed: bool = True


def configurations(nodes: list[Node]) -> list[dict[str, bool]]:
    """Return every combination of outcomes of a routine's conditional assembly.

    Each is checked on its own, so that two conditionals testing the same
    thing are taken the same way. With too many different conditions there is
    a single configuration in which every conditional goes both ways.
    """
    conditions = sorted({node.condition for node in nodes if node.kind == Kind.FORK})
    if len(conditions) > MAX_CONDITIONS:
        return [{}]
    outcomes = itertools.product((False, True), repeat=len(conditions))
    return [dict(zip(conditions, values, strict=True)) for values in outcomes]


def check_unreachable(
    routine: Routine, graph: Graph, reached: set[int], lenient: bool
) -> list[Finding]:
    """Report code that no path from the routine's entry reaches.

    With ``lenient``, code need not have a header, and a global label that
    nothing in the routine reaches is one that other routines jump to: its
    code is followed from there.
    """
    findings = []
    reachable = True
    for index, node in enumerate(graph.nodes):
        was_reachable = reachable
        if node.kind in (Kind.FORK, Kind.SKIP, Kind.END) or node.borrowed:
            continue
        reachable = index in reached or node.is_data
        if reachable:
            continue
        where = node.statement
        if index in graph.entries and lenient:
            continue
        if index in graph.entries:
            where = graph.entries[index]
            message = f"{where.label} has code but no routine header"
            findings.append(Finding(where.file, where.line, "H001", message))
        elif was_reachable:
            message = f"unreachable code in {routine.header.title} is not checked"
            findings.append(problem(where, "F005", message))
    return findings


def check_reserved(
    routine: Routine, graph: Graph, states: list[State | None], reserved: set[str]
) -> list[Finding]:
    """Report writes to reserved registers that no annotation allows.

    Popping a saved register back is not a write, but any other write is
    reported even when the register is restored afterwards.
    """
    findings = []
    for node, before in zip(graph.nodes, states, strict=True):
        if before is None or node.is_data or node.borrowed:
            continue
        allowed = set(routine.allowed)
        if "allow" in node.annotations:
            allowed |= node.annotations["allow"].registers
        written = step(State(stack=before.stack), node).registers
        causes = dict.fromkeys(written, "written")
        for call in node.calls:
            causes.update(dict.fromkeys(call.registers, f"clobbered by {call.via}"))
        for register in sorted((causes.keys() & reserved) - allowed):
            message = (
                f"reserved register {register} is {causes[register]}"
                f" in {routine.header.title}"
            )
            findings.append(problem(node.statement, "R003", message))
    return findings


@dataclass
class Exits:
    """What a routine has changed at the points where it ends.

    The marks are (register, node index) pairs naming the instructions that
    changed a register. ``changed`` is for normal returns and tail calls,
    ``unpreserved`` for returns from an interrupt handler.
    """

    changed: set[tuple[str, int]] = field(default_factory=set)
    unpreserved: set[tuple[str, int]] = field(default_factory=set)
    unbalanced: list[Node] = field(default_factory=list)
    returns: bool = False
    is_interrupt: bool = False


def collect_exits(nodes: list[Node], states: list[State | None]) -> Exits:
    """Gather the state at every reachable exit of a routine."""
    exits = Exits()
    for node, before in zip(nodes, states, strict=True):
        if before is None or not node.leaves:
            continue
        exits.returns = True
        after = step(before, node)
        marks = set(after.dirty)
        for call in node.calls:
            if call.tail and call.effect.returns:
                marks |= {(register, node.index) for register in call.registers}
        if after.stack != () or (STACK, node.index) in marks:
            marks.add((STACK, node.index))
            exits.unbalanced.append(node)
        if node.exit == Exit.RTE:
            exits.is_interrupt = True
            exits.unpreserved |= marks
        else:
            exits.changed |= marks
    return exits


def first_cause(nodes: list[Node], marks: set[tuple[str, int]], register: str) -> Node:
    """Return the first instruction that the marks blame for changing a register."""
    return nodes[min(index for name, index in marks if name == register)]


def check_paths(
    routine: Routine,
    graph: Graph,
    states: list[State | None],
    reserved: set[str],
    summary: Summary,
    inside: bool = False,
) -> None:
    """Check the paths through a routine in one configuration.

    ``states`` holds the state before each node the configuration reaches.
    With ``inside`` they are the paths of code the routine calls in itself:
    what that changes counts where it is called.
    """
    header = routine.header
    title = header.title
    nodes = graph.nodes

    def report(statement: Statement, code: str, message: str) -> None:
        summary.findings.append(problem(statement, code, message))

    summary.findings += check_reserved(routine, graph, states, reserved)
    for node, before in zip(nodes, states, strict=True):
        if before is None:
            continue
        summary.reached.add(node.index)
        if node.is_data or node.errors or node.falls_off:
            summary.analysed = False
        if node.borrowed:
            continue  # reported in the routine the code is in
        if node.is_data:
            report(node.statement, "F003", f"execution runs into data in {title}")
        for code, error in node.errors:
            report(node.statement, code, error)
        if node.falls_off:
            report(node.statement, "F004", f"execution runs off the end of {title}")

    exits = collect_exits(nodes, states)
    if inside:
        for node in exits.unbalanced:
            message = f"the stack is not balanced when code called in {title} returns"
            report(node.statement, "R006", message)
        return
    if routine.noreturn:
        return  # however it ends, it does not give its registers back
    declared = routine.declared
    changed = {register for register, _ in exits.changed}
    unpreserved = {register for register, _ in exits.unpreserved}
    summary.changed |= changed | unpreserved
    summary.returns = summary.returns or exits.returns
    summary.is_interrupt = summary.is_interrupt or exits.is_interrupt
    for node in exits.unbalanced:
        if STACK not in declared or node.exit == Exit.RTE:
            message = f"the stack is not balanced when {title} returns here"
            report(node.statement, "R006", message)
    for register in sorted(changed - declared - {STACK}):
        culprit = first_cause(nodes, exits.changed, register)
        subject = register
        if register in header.registers("In"):
            subject = f"In register {register}"
        cause = "written"
        for call in culprit.calls:
            if register in call.registers:
                cause = f"clobbered by {call.via}"
        message = (
            f"{subject} is {cause} but not listed under Out or Clobbers of {title}"
        )
        report(culprit.statement, "R001", message)
    for register in sorted(unpreserved - {STACK} - header.registers("Out")):
        culprit = first_cause(nodes, exits.unpreserved, register)
        message = f"interrupt handler {title} must preserve {register}"
        report(culprit.statement, "R004", message)


def examine(
    routine: Routine,
    graph: Graph,
    reserved: set[str],
    roughly: Literal["In", "Clobbers"] | None = None,
) -> tuple[Summary, Reads]:
    """Follow a routine's code in every configuration.

    With ``roughly`` it is followed once, every conditional going both ways,
    and only for what the routine changes (``Clobbers``) or reads (``In``):
    that is enough to work out what a routine without a header does.
    """
    summary = Summary()
    reads = Reads()
    for choices in [{}] if roughly else configurations(graph.nodes):
        if roughly != "In":
            states = analyse(graph.nodes, choices)
            check_paths(routine, graph, states, reserved, summary)
        for entry in sorted(graph.local_entries if not roughly else ()):
            states = analyse(graph.nodes, choices, entry)
            check_paths(routine, graph, states, reserved, summary, inside=True)
        if not routine.header.problems and roughly != "Clobbers":
            check_reads(routine, graph, choices, reserved, reads)
    return summary, reads


def hidden_entry(graph: Graph, reached: set[int]) -> int | None:
    """Return the first node that nothing reaches but that looks like an entry.

    That is code with a label (its address is taken somewhere), and a jump
    straight after a jump or after data: a row of them is a table of entries,
    which may have gaps.
    """
    labelled = set(graph.labels.values())
    for node in graph.nodes:
        if node.index in reached or node.kind != Kind.CODE or node.borrowed:
            continue
        before = graph.nodes[node.index - 1] if node.index else node
        if node.index in labelled:
            return node.index
        in_row = before.is_data or before.statement.mnemonic in JUMPS
        if node.statement.mnemonic in JUMPS and in_row:
            return node.index
    return None


def check_routine(
    routine: Routine, graph: Graph, reserved: set[str], lenient: bool = False
) -> list[Finding]:
    """Compare what a routine does to registers with what its header declares.

    With ``lenient`` (code need not have headers), code in the routine that
    nothing reaches but that looks like an entry is checked as one: what it
    changes is not counted as the routine's.
    """
    header = routine.header
    title = header.title
    summary, reads = examine(routine, graph, reserved)
    entry = hidden_entry(graph, summary.reached) if lenient else None
    while entry is not None:
        for choices in configurations(graph.nodes):
            states = analyse(graph.nodes, choices, entry)
            check_paths(routine, graph, states, reserved, summary, inside=True)
        summary.reached.add(entry)
        entry = hidden_entry(graph, summary.reached)
    findings = header.problems + routine.problems + check_label(routine)
    findings += (
        graph.problems
        + summary.findings
        + sorted(reads.findings, key=lambda finding: finding.line)
    )
    findings += check_unreachable(routine, graph, summary.reached, lenient)

    def report(where: Field, code: str, message: str) -> None:
        findings.append(Finding(header.file, where.line, code, message))

    clobbers = header.fields.get("Clobbers")
    stated = clobbers is not None and bool(clobbers.registers)
    if summary.is_interrupt and stated and not routine.inferred:
        message = f"the header of interrupt handler {title} must say Clobbers: -"
        report(clobbers, "R005", message)
    if not (summary.returns and summary.analysed) or routine.inferred:
        return findings  # nothing can be called stale
    for name in ("Out", "Clobbers"):
        if name == "Clobbers" and summary.is_interrupt:
            continue
        for register in sorted(header.registers(name) - summary.changed):
            message = (
                f"{register} is listed under {name} of {title} but is never changed"
            )
            report(header.fields[name], "R002", message)
    for register in sorted(header.registers("In") - reads.used - reserved):
        message = f"{register} is listed under In of {title} but is never read"
        report(header.fields["In"], "R010", message)
    return findings


def check_processor(graph: Graph, cpu: str, fpu: bool) -> list[Finding]:
    """Report the instructions that the chosen processor does not have."""
    findings = []
    for node in graph.nodes:
        missing = node.kind == Kind.CODE and needs(node.statement.mnemonic, cpu, fpu)
        if missing:
            message = f"{node.statement.name} needs {missing}, and this is for a {cpu}"
            findings.append(problem(node.statement, "S006", message))
    return findings
