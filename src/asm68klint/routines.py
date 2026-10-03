"""Find the routines in a source file and check how their headers are placed."""

from dataclasses import dataclass, field

from asm68klint.annotations import parse_annotation
from asm68klint.findings import Finding
from asm68klint.flow import Effect
from asm68klint.header import FIELDS, Field, Header, is_header_start, parse_header
from asm68klint.m68k import is_instruction
from asm68klint.source import Statement, is_local


@dataclass
class Routine:
    """A routine: its header and the statements up to the next header."""

    header: Header
    body: list[Statement] = field(default_factory=list)
    # Reserved registers the header's annotations allow the routine to write.
    allowed: set[str] = field(default_factory=set)
    problems: list[Finding] = field(default_factory=list)
    # True for a routine without a header: what its fields hold was worked
    # out from its code.
    inferred: bool = False

    @property
    def declared(self) -> set[str]:
        """The registers the header says the routine changes."""
        return self.header.registers("Out", "Clobbers")

    @property
    def effect(self) -> Effect:
        """What a call of the routine does, as its header has it."""
        results = self.header.registers("Out")
        if self.inferred:  # which of the registers it changes are results is not known
            results = self.header.registers("Clobbers")
        return Effect(
            frozenset(self.declared),
            frozenset(self.header.registers("In")),
            frozenset(self.header.registers("Clobbers") - results),
        )


def headerless(statement: Statement) -> Routine:
    """Return a routine without a header that starts at a statement."""
    name = statement.label or "(the start of the file)"
    header = Header(statement.file, statement.line, name)
    for name in FIELDS:
        header.fields[name] = Field(statement.line, "-")
    return Routine(header, inferred=True)


def find_routines(
    statements: list[Statement], starts: set[str] | None = None
) -> tuple[list[Routine], list[Statement]]:
    """Split a file into routines, one per header.

    Also returns the statements that come before the first header. With
    ``starts``, code need not have a header: a routine without one begins at
    each of those labels, and at the first instruction of the file.
    """
    routines: list[Routine] = []
    orphans: list[Statement] = []
    position = 0
    labelled = True  # the last routine has had its label
    while position < len(statements):
        statement = statements[position]
        is_global = bool(statement.label) and not is_local(statement.label or "")
        if is_header_start(statement):
            labelled = False
            end = position + 1
            while end < len(statements) and statements[end].is_comment:
                end += 1
            routine = Routine(parse_header(statements[position:end]))
            _read_header_annotations(routine, statements[position:end])
            routines.append(routine)
            position = end
            continue
        if starts is not None and (
            (statement.label in starts and labelled)
            or (not routines and is_instruction(statement.mnemonic))
        ):
            routines.append(headerless(statement))
        labelled = labelled or is_global
        (routines[-1].body if routines else orphans).append(statement)
        position += 1
    return routines, orphans


def _read_header_annotations(routine: Routine, lines: list[Statement]) -> None:
    """Apply the lint annotations written in a routine's header."""
    for statement in lines:
        annotation = parse_annotation(statement)
        if isinstance(annotation, Finding):
            routine.problems.append(annotation)
        elif annotation and annotation.keyword == "allow":
            routine.allowed |= annotation.registers
        elif annotation:
            message = (
                f"the lint annotation {annotation.keyword!r} cannot be used in a header"
            )
            routine.problems.append(
                Finding(statement.file, statement.line, "S005", message)
            )


def check_orphans(orphans: list[Statement]) -> list[Finding]:
    """Report code that comes before the first header of a file."""
    findings = []
    label: Statement | None = None
    for statement in orphans:
        if statement.label and not is_local(statement.label):
            label = statement
        if not is_instruction(statement.mnemonic):
            continue
        if label:
            message = f"{label.label} has code but no routine header"
            findings.append(Finding(label.file, label.line, "H001", message))
        else:
            message = "code outside a routine: no routine header"
            findings.append(Finding(statement.file, statement.line, "H001", message))
    return findings


def check_label(routine: Routine) -> list[Finding]:
    """Check that the header is followed by the label it names."""
    header = routine.header
    line = header.line
    if routine.inferred:
        return []
    for statement in routine.body:
        line = statement.line
        if statement.label and not is_local(statement.label):
            if not header.name or statement.label == header.name:
                return []
            message = (
                f"header names {header.name} but the label that follows is"
                f" {statement.label}"
            )
            return [Finding(header.file, line, "H006", message)]
        if statement.label or is_instruction(statement.mnemonic):
            break
    message = f"header of {header.title} is not followed by a label"
    return [Finding(header.file, line, "H006", message)]
