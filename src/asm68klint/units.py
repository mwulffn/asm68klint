"""The source files of a lint run, their routines and the graphs of those."""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from asm68klint.checks import examine
from asm68klint.directives import EXPORTS
from asm68klint.findings import Finding
from asm68klint.graph import Graph, build_graph, refresh
from asm68klint.infer import find_starts, place_label
from asm68klint.model import Effect
from asm68klint.options import Options
from asm68klint.reader import read_source
from asm68klint.registers import STACK
from asm68klint.routines import Routine, find_routines
from asm68klint.source import Statement, is_local
from asm68klint.tables import find_tables


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
            if statement.mnemonic in EXPORTS:
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
    names: tuple[Literal["Clobbers", "In"], ...] = ("Clobbers", "In")
    for name in names:
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
