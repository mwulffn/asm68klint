"""Lint a set of assembly source files."""

from collections.abc import Iterable
from pathlib import Path
from typing import TypedDict, Unpack

from asm68klint.annotations import Annotation, ignored, parse_annotation
from asm68klint.checks import check_processor, check_routine, configurations
from asm68klint.findings import Finding
from asm68klint.m68k import read_registers, written_registers
from asm68klint.options import Settings, make_options
from asm68klint.reads import live_registers
from asm68klint.registers import REGISTERS, STACK, format_list
from asm68klint.routines import check_orphans
from asm68klint.style import check_style
from asm68klint.units import Unit, graphs, read_units


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


def lint_files(paths: Iterable[Path], **settings: Unpack[Settings]) -> list[Finding]:
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


def describe_files(paths: Iterable[Path], **settings: Unpack[Settings]) -> list[str]:
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


# What ``routine_effects`` says of one routine. ``header`` is False when the
# routine has none and the register lists were worked out from its code.
RoutineEffects = TypedDict(
    "RoutineEffects",
    {
        "file": str,
        "line": int,
        "name": str,
        "in": str,
        "out": str,
        "clobbers": str,
        "header": bool,
    },
)


def routine_effects(
    paths: Iterable[Path], **settings: Unpack[Settings]
) -> list[RoutineEffects]:
    """Return what every routine reads and changes, as data.

    Each routine is a dict: ``file``, ``line``, ``name``, the register lists
    ``in``, ``out`` and ``clobbers``, and ``header`` (False when the routine
    has none and the lists were worked out from its code).
    """
    units, _ = read_units(paths, make_options(**settings))
    rows: list[RoutineEffects] = []
    for unit in units:
        for routine in unit.routines:
            header = routine.header
            rows.append(
                {
                    "file": header.file,
                    "line": header.line,
                    "name": header.title,
                    "in": format_list(header.registers("In")),
                    "out": format_list(header.registers("Out")),
                    "clobbers": format_list(header.registers("Clobbers")),
                    "header": not routine.inferred,
                }
            )
    return rows


def free_registers(
    paths: Iterable[Path], file: Path, line: int, **settings: Unpack[Settings]
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
