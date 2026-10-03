"""Read a source file with its include files and expand its macros.

The result is one flat list of statements, as the assembler would see them,
except that conditional assembly is left in: the linter checks every branch.
"""

import os
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from asm68klint import gas
from asm68klint.findings import Finding
from asm68klint.m68k import is_instruction, normalise
from asm68klint.source import Statement, parse_statement, problem

# Directives that are taken as such in the first column, where a label goes.
FIRST_COLUMN = ("dc", "ds", "dcb", "include", "incbin", "section", "even")
Parser = Callable[[str, int, str], Statement]

MAX_DEPTH = 50
_ESCAPE = re.compile(r"\\([@#0-9])")


def find_file(directory: Path, name: str) -> Path | None:
    """Find a file by a name written for a file system that ignores case.

    The name may use either slash, and what comes before a colon (an Amiga
    volume or assign) is dropped.
    """
    parts = name.replace("\\", "/").rpartition(":")[2].split("/")
    if (directory / "/".join(parts)).is_file():
        return directory / "/".join(parts)
    for part in parts:
        if not directory.is_dir():
            return None
        names = {entry.name.lower(): entry for entry in directory.iterdir()}
        if part.lower() not in names:
            return None
        directory = names[part.lower()]
    return directory if directory.is_file() else None


def parse_motorola(file: str, line: int, text: str) -> Statement:
    """Read a line of Motorola syntax.

    Some assemblers take an instruction that starts in the first column, where
    others want a label: a "label" there that is an instruction or ``dc`` and
    has no colon is read as what it is.
    """
    statement = parse_statement(file, line, text)
    label = statement.label
    if label and text.startswith(label) and not text.startswith(label + ":"):
        stem = label.lower().partition(".")[0]
        if is_instruction(stem) or stem in FIRST_COLUMN:
            statement = parse_statement(file, line, " " + text)
    return normalise(statement)


@dataclass
class Macro:
    """A macro: its lines, the names of its arguments, and how to read it."""

    lines: list[str]
    parameters: tuple[str, ...]
    parse: Parser


class Reader:
    """Reads one source file given on the command line and all it includes."""

    def __init__(self, include_dirs: Iterable[Path] = (), syntax: str = "auto") -> None:
        self.statements: list[Statement] = []
        self.findings: list[Finding] = []
        self.include_dirs = [Path(directory) for directory in include_dirs]
        self.syntax = syntax
        self.macros: dict[str, Macro] = {}
        # Names defined with equr or reg, and the register (list) they stand for.
        self.aliases: dict[str, str] = {}
        self.alias_pattern: re.Pattern | None = None
        self.files_read: set[Path] = set()
        self.expansions = 0
        self.home = Path()  # the directory of the file given on the command line

    def parser(self, lines: list[str]) -> tuple[Parser, list[str]]:
        """Return how to read a file's lines, and the lines to read."""
        if self.syntax == "gas" or (
            self.syntax == "auto" and gas.looks_like_gas(lines)
        ):
            numbered = gas.Numbered()

            def parse(file: str, line: int, text: str) -> Statement:
                return normalise(gas.parse_statement(file, line, text, numbered))

            return parse, gas.strip_block_comments(lines)
        return parse_motorola, lines

    def read_file(self, path: Path) -> None:
        """Read a file, unless it has been read already."""
        if path.resolve() in self.files_read:
            return
        self.files_read.add(path.resolve())
        parse, texts = self.parser(path.read_text(errors="replace").splitlines())
        lines = iter(enumerate(texts, 1))
        for number, text in lines:
            statement = parse(str(path), number, text)
            if statement.mnemonic == "end":
                break
            if statement.mnemonic == "rem":
                self._skip(lines, "erem", statement, "rem has no erem", parse)
            elif statement.mnemonic == "macro":
                name, parameters = statement.label, statement.operands
                if not name:  # macro NAME, as vasm also has it
                    name, parameters = "".join(parameters[:1]), ()
                missing = f"macro {name} has no endm"
                skipped = self._skip(lines, "endm", statement, missing, parse)
                self.macros[name] = Macro(skipped, parameters, parse)
            else:
                self._add(statement, path.parent)

    def _skip(
        self,
        lines: Iterable[tuple[int, str]],
        last: str,
        start: Statement,
        missing: str,
        parse: Parser,
    ) -> list[str]:
        """Consume lines up to the one with the given directive; return them.

        Reports ``missing`` at the starting statement if the file ends first.
        """
        skipped: list[str] = []
        for number, text in lines:
            if parse("", number, text).mnemonic == last:
                return skipped
            skipped.append(text)
        self.findings.append(problem(start, "S003", missing))
        return skipped

    def _add(self, statement: Statement, directory: Path, depth: int = 0) -> None:
        """Add a statement, following an include or expanding a macro."""
        mnemonic, operands = statement.mnemonic, statement.operands
        if statement.name in self.macros:
            self._expand(statement, directory, depth)
            return
        self.statements.append(statement)
        if mnemonic == "include" and operands:
            self._include(statement, directory)
        elif mnemonic == "incdir" and operands:
            name = operands[0].strip("\"'").rpartition(":")[2]
            self.include_dirs.append(directory / name)
        elif mnemonic in ("equr", "reg") and statement.label and operands:
            self.aliases[statement.label] = operands[0]
            names = "|".join(map(re.escape, self.aliases))
            self.alias_pattern = re.compile(rf"(?<![\w.$])({names})(?![\w$])")
        elif self.alias_pattern:
            pattern = self.alias_pattern
            statement.operands = tuple(
                pattern.sub(lambda match: self.aliases[match.group(1)], operand)
                for operand in operands
            )

    def _include(self, statement: Statement, directory: Path) -> None:
        """Read an include file, looking next to the including file first."""
        name = statement.operands[0].strip("\"'")
        for candidate in [directory, self.home, Path(), *self.include_dirs]:
            found = find_file(candidate, name)
            if found:
                self.read_file(Path(os.path.normpath(found)))
                return
        self.findings.append(
            problem(statement, "S001", f"cannot find the include file {name!r}")
        )

    def _expand(self, call: Statement, directory: Path, depth: int) -> None:
        """Replace a macro call by the macro's lines with the arguments filled in."""
        if depth > MAX_DEPTH:
            message = (
                f"macro {call.name} is expanded more than {MAX_DEPTH} levels deep;"
                " recursive macros are not supported"
            )
            self.findings.append(Finding(call.file, call.line, "S003", message))
            return
        self.expansions += 1
        unique = f"_{self.expansions:06d}"
        if not call.expansion:
            call.macro = call.name
            call.expansion = self.expansions
        call.is_macro_call = True
        self.statements.append(call)
        macro = self.macros[call.name]
        arguments = [
            argument[1:-1] if argument[:1] == "<" else argument
            for argument in call.operands
        ]
        # An argument may have a value for when it is left out: name=value.
        named = {}
        for position, parameter in enumerate(macro.parameters):
            name, _, default = parameter.partition("=")
            given = arguments[position] if position < len(arguments) else ""
            named[name.strip()] = given or default
        names = "|".join(sorted(map(re.escape, named), key=len, reverse=True))
        by_name = (
            re.compile(rf"\\(?:({names})(?![\w$])|\{{({names})\}})") if named else None
        )

        def fill(match: re.Match) -> str:
            key = match.group(1)
            if key == "@":
                return unique
            if key == "#":
                return str(len(arguments))
            if key == "0":
                return call.size or "w"
            return arguments[int(key) - 1] if int(key) <= len(arguments) else ""

        for text in macro.lines:
            if by_name:
                text = by_name.sub(
                    lambda match: named[match.group(1) or match.group(2)], text
                )
                text = text.replace("\\()", "")
            line = macro.parse(call.file, call.line, _ESCAPE.sub(fill, text))
            line.macro = call.macro
            line.expansion = call.expansion
            unknown = re.search(r"\\.", line.text[: len(line.text) - len(line.comment)])
            if unknown:
                message = f"cannot expand '{unknown.group()}' in macro {call.name}"
                self.findings.append(Finding(call.file, call.line, "S003", message))
            self._add(line, directory, depth + 1)


def read_source(
    path: Path, include_dirs: Iterable[Path] = (), syntax: str = "auto"
) -> tuple[list[Statement], list[Finding]]:
    """Read a source file into statements; also return what went wrong.

    ``syntax`` is ``motorola``, ``gas`` (the GNU assembler's Motorola style) or
    ``auto``, which decides for each file by what is in it.
    """
    reader = Reader(include_dirs, syntax)
    reader.home = Path(path).parent
    reader.read_file(Path(path))
    return reader.statements, reader.findings
