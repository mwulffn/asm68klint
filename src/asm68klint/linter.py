"""Lint a set of assembly source files."""

import itertools
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from asm68klint.annotations import Annotation, ignored, parse_annotation
from asm68klint.findings import Finding
from asm68klint.flow import Effect, Node, State, analyse, step
from asm68klint.graph import Graph, build_graph, refresh
from asm68klint.header import FIELDS, Field
from asm68klint.infer import find_starts, place_label
from asm68klint.m68k import needs, read_registers, written_registers
from asm68klint.options import Options, make_options
from asm68klint.reader import read_source
from asm68klint.reads import Reads, check_reads, live_registers
from asm68klint.registers import REGISTERS, STACK, format_list
from asm68klint.routines import Routine, check_label, check_orphans, find_routines
from asm68klint.source import Statement, is_local, problem
from asm68klint.style import check_style
from asm68klint.tables import find_tables

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
    tables: dict[str, list[str]] = field(default_factory=dict)
    # The labels inside routines (not those they start at): where each is.
    homes: dict[str, int] = field(default_factory=dict)
    # The graphs of the routines without headers, kept once they are built.
    graphs: dict[int, Graph] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.routines, self.orphans = find_routines(self.statements, self.starts)
        self.tables = find_tables(self.statements)
        for index, routine in enumerate(self.routines):
            for statement in routine.body:
                label = statement.label
                if label and place_label(label) and label != routine.header.name:
                    self.homes[label] = index
        for statement in self.statements:
            if statement.label:
                self.labels.add(statement.label)
            if statement.mnemonic in ("xdef", "public", "global"):
                self.exports.update(statement.operands)

    def borrow(self, label: str) -> tuple[Routine, Routine | None] | None:
        """Return the routine a label is inside, and the routine after it."""
        if label not in self.homes:
            return None
        index = self.homes[label]
        return self.routines[index], (self.routines[index + 1 :] or [None])[0]


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
        if node.kind in ("fork", "skip", "end") or node.borrowed:
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
    routine: Routine, graph: Graph, reserved: set[str], roughly: str = ""
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
    jumps = ("jmp", "bra")
    for node in graph.nodes:
        if node.index in reached or node.kind != "code" or node.borrowed:
            continue
        before = graph.nodes[node.index - 1] if node.index else node
        if node.index in labelled:
            return node.index
        in_row = before.is_data or before.statement.mnemonic in jumps
        if node.statement.mnemonic in jumps and in_row:
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
        missing = node.kind == "code" and needs(node.statement.mnemonic, cpu, fpu)
        if missing:
            message = f"{node.statement.name} needs {missing}, and this is for a {cpu}"
            findings.append(problem(node.statement, "S006", message))
    return findings


def graphs(
    units: list[Unit],
    options: Options,
    inferred: bool = False,
    only: set[str] | None = None,
    used: dict[str, set[str]] | None = None,
) -> Iterator[tuple[Routine, Graph]]:
    """Build the graph of every routine, or of those without a header.

    ``only`` names the routines wanted. ``used`` is filled in with the names
    of the routines that each routine's code depends on.
    """
    for unit in units:
        for routine, following in zip(unit.routines, [*unit.routines[1:], None]):
            name = routine.header.name or ""
            if (inferred and not routine.inferred) or (only and name not in only):
                continue
            if id(routine) in unit.graphs:  # built when it was worked out
                refresh(unit.graphs[id(routine)])
                yield routine, unit.graphs[id(routine)]
                continue
            needs: set[str] = set()

            def find(
                name: str, unit: Unit = unit, needs: set[str] = needs
            ) -> Effect | str:
                needs.add(name)
                return resolve(name, unit, units, options.extern)

            graph = build_graph(
                routine,
                following,
                find,
                options.platform,
                options.symbols,
                unit.tables,
                unit.borrow if options.infer else None,
            )
            if used is not None:
                after = {r.header.name or "" for r in graph.after.values() if r}
                used[name] = needs | after
            if routine.inferred:
                unit.graphs[id(routine)] = graph
            yield routine, graph


def settle(units: list[Unit], options: Options) -> None:
    """Work out what the routines without headers read and change.

    What one does depends on what the routines it calls do, so this goes round
    until nothing is added; a routine is looked at again only when one it
    depends on has changed. The stack pointer is left out: a routine that
    leaves the stack unbalanced is reported itself, and its callers are
    checked as if it did not.
    """
    reserved = set(options.reserved)
    used: dict[str, set[str]] = {}
    # What is changed first: what a routine reads of what it was given
    # depends on what the routines it calls change, and not the other way.
    built = list(graphs(units, options, inferred=True, used=used))
    for name in ("Clobbers", "In"):
        stale: set[str] | None = None  # the routines to look at: None for all
        while stale is None or stale:
            grown: set[str] = set()
            for routine, graph in built:
                if stale is not None and routine.header.name not in stale:
                    continue
                refresh(graph)
                summary, reads = examine(routine, graph, reserved, name)
                found = reads.missing if name == "In" else summary.changed - {STACK}
                field = routine.header.fields[name]
                if not found <= set(field.registers):
                    field.registers = sorted(found | set(field.registers))
                    grown.add(routine.header.name or "")
            stale = {user for user, needs in used.items() if needs & grown}


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


def ignored_lines(units: list[Unit]) -> dict[tuple[str, int], set[str]]:
    """Return the rules that ``lint: ignore`` says are not to be reported, by line.

    An annotation at the end of a line holds for that line, whatever is on
    it; one on a comment line of its own, for the next line that is not a
    comment; one in a routine's header, for every line of the routine.
    """
    quiet: dict[tuple[str, int], set[str]] = {}

    def add(file: str, line: int, codes: Iterable[str]) -> None:
        quiet.setdefault((file, line), set()).update(codes)

    for unit in units:
        waiting: set[str] = set()
        for statement in unit.statements:
            found = parse_annotation(statement)
            named = isinstance(found, Annotation) and found.keyword == "ignore"
            codes = (
                set(found.labels) if isinstance(found, Annotation) and named else set()
            )
            if statement.mnemonic is None:
                waiting |= codes
            elif codes | waiting:
                add(statement.file, statement.line, codes | waiting)
                waiting = set()
        for routine in unit.routines:
            header = routine.header
            lines = {(header.file, header.line)}
            lines |= {(header.file, field.line) for field in header.fields.values()}
            lines |= {(statement.file, statement.line) for statement in routine.body}
            for file, line in lines if routine.ignored else ():
                add(file, line, routine.ignored)
    return quiet


def lint_files(paths: Iterable[Path], **settings: Any) -> list[Finding]:
    """Lint the given source files together and return the sorted findings.

    The settings are those of ``make_options``.
    """
    options = make_options(**settings)
    reserved = set(options.reserved)
    paths = list(paths)
    units, findings = read_units(paths, options)
    for unit in units:
        if not options.infer:
            findings.update(check_orphans(unit.orphans))
    for routine, graph in graphs(units, options):
        findings.update(check_routine(routine, graph, reserved, options.infer))
        if options.cpu:
            findings.update(check_processor(graph, options.cpu, options.fpu))
    quiet = ignored_lines(units)
    if any(code.startswith("T") for code in options.codes):
        sources = [unit.statements for unit in units]
        findings.update(check_style(list(map(Path, paths)), sources, options))
    return sorted(
        (
            finding
            for finding in findings
            if finding.code in options.codes
            and not ignored(finding.code, quiet.get((finding.file, finding.line), ()))
        ),
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
    lines = []
    for row in routine_effects(paths, **settings):
        text = f"In {row['in']}; Out {row['out']}; Clobbers {row['clobbers']}"
        if not row["header"]:
            text = f"In {row['in']}; changes {row['clobbers']} (no header)"
        lines.append(f"{row['file']}:{row['line']}: {row['name']}: {text}")
    return lines


def routine_effects(paths: Iterable[Path], **settings: Any) -> list[dict]:
    """Return what every routine reads and changes, as data.

    Each routine is a dict: ``file``, ``line``, ``name``, the register lists
    ``in``, ``out`` and ``clobbers``, and ``header`` (False when the routine
    has none and the lists were worked out from its code).
    """
    units, _ = read_units(paths, make_options(**settings))
    rows = []
    for unit in units:
        for routine in unit.routines:
            header = routine.header
            row = {"file": header.file, "line": header.line, "name": header.title}
            for name in FIELDS:
                row[name.lower()] = format_list(header.registers(name))
            rows.append({**row, "header": not routine.inferred})
    return rows


def free_registers(
    paths: Iterable[Path], file: Path, line: int, **settings: Any
) -> tuple[str, set[str], set[str]] | None:
    """Say which registers new code at a line of a source file may use.

    Returns the name of the routine the line is in, the registers that are
    free there and the ones that are in use, or None if the line is in no
    routine. A register is free when nothing later needs what is in it (see
    ``live_registers``); reserved registers and the stack pointer never are.
    The settings are those of ``make_options``.
    """
    options = make_options(**settings)
    units, _ = read_units(paths, options)
    wanted = Path(file).resolve()
    for routine, graph in graphs(units, options):
        own = [
            node
            for node in graph.nodes
            if not node.borrowed and Path(node.statement.file).resolve() == wanted
        ]
        first = routine.header.line
        if Path(routine.header.file).resolve() != wanted or not own:
            first = own[0].statement.line if own else 0
        if not own or not first <= line <= own[-1].statement.line:
            continue
        index = next(node.index for node in own if node.statement.line >= line)
        live: set[str] = set()
        for choices in configurations(graph.nodes):
            live |= live_registers(routine, graph, choices)[index]
        busy = live | set(options.reserved) | {STACK}
        used = {
            register
            for node in graph.nodes
            for register in written_registers(node.statement)
            | read_registers(node.statement)
        }
        shown = {r for r in REGISTERS if not r.startswith("fp") or r in used}
        return routine.header.title, shown - busy, shown & busy
    return None
