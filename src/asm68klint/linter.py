"""Lint a set of assembly source files."""

import itertools
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from asm68klint.findings import Finding
from asm68klint.flow import Effect, Node, State, analyse, step
from asm68klint.graph import Graph, build_graph
from asm68klint.header import FIELDS, Field
from asm68klint.infer import find_starts
from asm68klint.options import Options, make_options
from asm68klint.reader import read_source
from asm68klint.reads import Reads, check_reads
from asm68klint.registers import STACK, format_list
from asm68klint.routines import Routine, check_label, check_orphans, find_routines
from asm68klint.source import Statement, is_local, problem

# With more different conditions of conditional assembly than this in one
# routine, their combinations are no longer checked one by one.
MAX_CONDITIONS = 8


@dataclass
class Unit:
    """One source file given on the command line, with all it includes."""

    statements: list[Statement]
    # The labels that start a routine without a header; None if there may be
    # no such routines.
    starts: set[str] | None = None
    routines: list[Routine] = field(default_factory=list)
    orphans: list[Statement] = field(default_factory=list)
    labels: set[str] = field(default_factory=set)
    exports: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.routines, self.orphans = find_routines(self.statements, self.starts)
        for statement in self.statements:
            if statement.label:
                self.labels.add(statement.label)
            if statement.mnemonic in ("xdef", "public", "global"):
                self.exports.update(statement.operands)


def resolve(
    name: str, unit: Unit, units: list[Unit], outside: Effect | None = None
) -> Effect | str:
    """Find the routine a call in ``unit`` refers to.

    Returns what its header says a call of it does, or the reason the call
    cannot be analysed. A routine in the same unit comes first, then
    exported routines of the other units, then their other global labels.
    ``outside`` is what a routine that is in none of the files does.
    """
    for routine in unit.routines:
        if routine.header.name == name:
            return routine.effect
    if is_local(name) or name in unit.labels:
        return "it has no routine header"
    found: dict[bool, dict[tuple[str, int], Routine]] = {True: {}, False: {}}
    for other in units:
        for routine in other.routines:
            if routine.header.name == name:
                place = (routine.header.file, routine.header.line)
                found[name in other.exports][place] = routine
    candidates = list((found[True] or found[False]).values())
    if not candidates and outside is not None:
        return outside
    if not candidates:
        return "no routine of that name in the files given"
    if len(candidates) > 1:
        return "several files define a routine of that name"
    return candidates[0].effect


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
    conditions = sorted({node.condition for node in nodes if node.kind == "fork"})
    if len(conditions) > MAX_CONDITIONS:
        return [{}]
    outcomes = itertools.product((False, True), repeat=len(conditions))
    return [dict(zip(conditions, values, strict=True)) for values in outcomes]


def check_unreachable(
    routine: Routine, graph: Graph, reached: set[int]
) -> list[Finding]:
    """Report code that no path from the routine's entry reaches."""
    findings = []
    reachable = True
    for index, node in enumerate(graph.nodes):
        was_reachable = reachable
        if node.kind in ("fork", "skip"):
            continue
        reachable = index in reached or node.is_data
        if reachable:
            continue
        where = node.statement
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
        if before is None or node.is_data:
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
        if before is None or not node.exit:
            continue
        exits.returns = True
        after = step(before, node)
        marks = set(after.dirty)
        for call in node.calls:
            if call.tail:
                marks |= {(register, node.index) for register in call.registers}
        if after.stack != () or (STACK, node.index) in marks:
            marks.add((STACK, node.index))
            exits.unbalanced.append(node)
        if node.exit == "rte":
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
        if node.is_data:
            report(node.statement, "F003", f"execution runs into data in {title}")
        for code, error in node.errors:
            report(node.statement, code, error)
        if node.falls_off:
            report(node.statement, "F004", f"execution runs off the end of {title}")
        if node.is_data or node.errors or node.falls_off:
            summary.analysed = False

    exits = collect_exits(nodes, states)
    if inside:
        for node in exits.unbalanced:
            message = f"the stack is not balanced when code called in {title} returns"
            report(node.statement, "R006", message)
        return
    declared = routine.declared
    changed = {register for register, _ in exits.changed}
    unpreserved = {register for register, _ in exits.unpreserved}
    summary.changed |= changed | unpreserved
    summary.returns = summary.returns or exits.returns
    summary.is_interrupt = summary.is_interrupt or exits.is_interrupt
    for node in exits.unbalanced:
        if STACK not in declared or node.exit == "rte":
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
    routine: Routine, graph: Graph, reserved: set[str]
) -> tuple[Summary, Reads]:
    """Follow a routine's code in every configuration."""
    summary = Summary()
    reads = Reads()
    for choices in configurations(graph.nodes):
        states = analyse(graph.nodes, choices)
        check_paths(routine, graph, states, reserved, summary)
        for entry in sorted(graph.local_entries):
            states = analyse(graph.nodes, choices, entry)
            check_paths(routine, graph, states, reserved, summary, inside=True)
        if not routine.header.problems:
            check_reads(routine, graph, choices, reserved, reads)
    return summary, reads


def check_routine(routine: Routine, graph: Graph, reserved: set[str]) -> list[Finding]:
    """Compare what a routine does to registers with what its header declares."""
    header = routine.header
    title = header.title
    summary, reads = examine(routine, graph, reserved)
    findings = header.problems + routine.problems + check_label(routine)
    findings += (
        graph.problems
        + summary.findings
        + sorted(reads.findings, key=lambda finding: finding.line)
    )
    findings += check_unreachable(routine, graph, summary.reached)

    def report(where: Field, code: str, message: str) -> None:
        findings.append(Finding(header.file, where.line, code, message))

    clobbers = header.fields.get("Clobbers")
    if summary.is_interrupt and clobbers.registers and not routine.inferred:
        message = f"the header of interrupt handler {title} must say Clobbers: -"
        report(clobbers, "R005", message)
    if not (summary.returns and summary.analysed):
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


def graphs(
    units: list[Unit], options: Options, inferred: bool = False
) -> Iterator[tuple[Routine, Graph]]:
    """Build the graph of every routine, or of those without a header."""
    for unit in units:
        for routine, following in zip(unit.routines, [*unit.routines[1:], None]):
            if routine.inferred or not inferred:
                yield (
                    routine,
                    build_graph(
                        routine,
                        following,
                        lambda name, unit=unit: resolve(
                            name, unit, units, options.extern
                        ),
                        options.platform,
                    ),
                )


def settle(units: list[Unit], options: Options) -> None:
    """Work out what the routines without headers read and change.

    What one does depends on what the routines it calls do, so this goes round
    until nothing is added. The stack pointer is left out: a routine that
    leaves the stack unbalanced is reported itself, and its callers are
    checked as if it did not.
    """
    reserved = set(options.reserved)
    # What is changed first: what a routine reads of what it was given
    # depends on what the routines it calls change, and not the other way.
    for name in ("Clobbers", "In"):
        growing = True
        while growing:
            growing = False
            for routine, graph in graphs(units, options, inferred=True):
                summary, reads = examine(routine, graph, reserved)
                found = reads.missing if name == "In" else summary.changed - {STACK}
                field = routine.header.fields[name]
                if not found <= set(field.registers):
                    field.registers = sorted(found | set(field.registers))
                    growing = True


def read_units(
    paths: Iterable[Path], options: Options
) -> tuple[list[Unit], set[Finding]]:
    """Read the source files and find their routines."""
    findings: set[Finding] = set()
    sources = []
    for path in paths:
        statements, problems = read_source(
            Path(path), options.include_dirs, options.syntax
        )
        findings.update(problems)
        sources.append(statements)
    starts = find_starts(sources) if options.infer else [None] * len(sources)
    units = [Unit(*pair) for pair in zip(sources, starts, strict=True)]
    if options.infer:
        settle(units, options)
    return units, findings


def lint_files(paths: Iterable[Path], **settings: Any) -> list[Finding]:
    """Lint the given source files together and return the sorted findings.

    The settings are those of ``make_options``.
    """
    options = make_options(**settings)
    reserved = set(options.reserved)
    units, findings = read_units(paths, options)
    for unit in units:
        if not options.infer:
            findings.update(check_orphans(unit.orphans))
    for routine, graph in graphs(units, options):
        findings.update(check_routine(routine, graph, reserved))
    return sorted(
        (finding for finding in findings if finding.code in options.codes),
        key=lambda finding: (
            finding.file,
            finding.line,
            finding.severity,
            finding.message,
        ),
    )


def describe_files(paths: Iterable[Path], **settings: Any) -> list[str]:
    """Return a line for every routine saying what it reads and changes.

    For a routine with a header that is what the header says; for one without
    (with ``infer``), what its code does. The settings are those of
    ``make_options``.
    """
    units, _ = read_units(paths, make_options(**settings))
    lines = []
    for unit in units:
        for routine in unit.routines:
            header = routine.header
            fields = {name: format_list(header.registers(name)) for name in FIELDS}
            text = (
                f"In {fields['In']}; Out {fields['Out']}; Clobbers {fields['Clobbers']}"
            )
            if routine.inferred:
                text = f"In {fields['In']}; changes {fields['Clobbers']} (no header)"
            lines.append(f"{header.file}:{header.line}: {header.title}: {text}")
    return lines
