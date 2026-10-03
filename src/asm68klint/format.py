"""Lay source out: one column each for label, instruction, operands, comment.

Only blanks and the case of mnemonics and register names are changed, and a
line is left as it was unless it reads the same before and after. Source for
the GNU assembler is not formatted.
"""

import difflib
import re
from collections.abc import Iterable
from pathlib import Path

from asm68klint import gas
from asm68klint.directives import DATA, ELSE, ELSE_IF, END_IF, IF, OTHER, SYMBOLS
from asm68klint.m68k import INSTRUCTIONS
from asm68klint.reader import parse_motorola
from asm68klint.source import read_file, split_comment, words, write_file

TAB = 8
COMMENT_COLUMN = 48
DEFINITION_COLUMN = 16  # where equ, = and rs go, after the name they define
# What takes no operands: anything after it on the line is a comment.
NO_OPERANDS = words(
    "rts rte rtr nop reset trapv illegal endm endc endif else even odd rsreset"
    " endr erem mexit"
)
DEFINITIONS = SYMBOLS - {"macro", "set"}
DIRECTIVES = DATA | IF | ELSE | ELSE_IF | END_IF | OTHER
_LABEL = re.compile(r"([^\s:=;]+::?|[^\s:=;]+(?=\s|$)|\s+[^\s:=;]+::?)")
_REGISTER = re.compile(
    r"(?<![\w.$@])([da][0-7]|sp|pc|sr|ccr|usp|fp[0-7])(\.[wl])?(?![\w$@(])",
    re.IGNORECASE,
)
_QUOTED = re.compile(r"""('[^']*'|"[^"]*")""")


def width(text: str) -> int:
    """Return the column a text ends in, with tab stops every eight."""
    column = 0
    for char in text:
        column = column + TAB - column % TAB if char == "\t" else column + 1
    return column


def operand_end(field: str) -> int:
    """Return where the operands end in what follows a mnemonic.

    They end at the first blank outside brackets and quotes; a blank straight
    after a comma is part of them. What comes after is a comment.
    """
    depth = 0
    quote = ""
    angle = False
    current = False  # the operand being read has something in it
    for position, char in enumerate(field):
        if quote:
            quote = "" if char == quote else quote
        elif angle:
            angle = char != ">"
        elif char in "'\"":
            quote = char
        elif char == "<" and not current:
            angle = True
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            current = False
            continue
        elif char.isspace():
            if current:
                return position
            continue
        current = True
    return len(field)


def lower_registers(operands: str) -> str:
    """Write the register names in an operand field in lower case."""
    parts = _QUOTED.split(operands)
    for index in range(0, len(parts), 2):  # the parts outside quotes
        parts[index] = _REGISTER.sub(lambda found: found.group().lower(), parts[index])
    return "".join(parts)


def same(first: str, second: str) -> bool:
    """True when two lines read the same: label, instruction, operands, comment."""
    one, two = parse_motorola("", 0, first), parse_motorola("", 0, second)

    def read(statement) -> tuple:
        operands = tuple(operand.lower() for operand in statement.operands)
        if statement.mnemonic not in INSTRUCTIONS:
            operands = statement.operands
        comment = statement.comment.strip()
        return statement.label, statement.mnemonic, statement.size, operands, comment

    return read(one) == read(two)


def lay_out(text: str) -> tuple[str, str] | None:
    """Lay the code of a line out; return it and the line's comment.

    None for a line that is left as it is: a blank one, one that is all
    comment, one that cannot be read with certainty.
    """
    stripped = text.rstrip()
    code, comment = split_comment(stripped)
    if not code.strip():
        return None
    has_comment = len(code) < len(stripped)
    comment = ";" + comment if has_comment else ""
    statement = parse_motorola("", 0, stripped)
    label = ""
    rest = code
    if statement.label:
        match = _LABEL.match(code)
        if not match:
            return None
        label, rest = match.group(1).strip(), code[match.end() :]
    rest = rest.strip()
    line = label
    if rest.startswith("*"):
        comment = rest  # a comment after a label, as Devpac has it
    elif rest:
        name = "=" if rest.startswith("=") else rest.split()[0]
        field = rest[len(name) :]
        end = 0 if statement.mnemonic in NO_OPERANDS else operand_end(field)
        operands, tail = field[:end].strip(), field[end:].strip()
        mnemonic = statement.mnemonic or ""
        if mnemonic in INSTRUCTIONS or mnemonic in DIRECTIVES:
            name = name.lower()
        if mnemonic in INSTRUCTIONS:
            operands = lower_registers(operands)
        gap = "\t"
        if mnemonic in DEFINITIONS and label:  # a name and its value: further in
            tabs = -(-(DEFINITION_COLUMN - width(label)) // TAB)
            gap = "\t" * tabs if tabs > 0 else " "
        line += gap + name + ("\t" + operands if operands else "")
        if tail and has_comment:
            return None  # words after the operands and a comment as well
        comment = comment or tail
    return line, comment


def format_text(text: str, comment_column: int = COMMENT_COLUMN) -> str | None:
    """Lay a source text out; None if it is for the GNU assembler.

    The comments of lines that follow each other start in one column: the
    one asked for, or the next tab stop that the longest of the lines leaves
    free, up to two stops further. A line longer still has its comment after
    one tab, and so has a label that stands alone.
    """
    lines = [line.rstrip() for line in text.splitlines()]
    if gas.looks_like_gas(lines):
        return None
    laid: list[tuple[str, str] | None] = []
    skipping = False  # inside rem ... erem, which is all comment
    for line in lines:
        mnemonic = parse_motorola("", 0, line).mnemonic
        if skipping or mnemonic == "rem":
            skipping = mnemonic != "erem"
            laid.append(None)
        else:
            laid.append(lay_out(line))
    result = list(lines)
    position = 0
    while position < len(lines):
        end = position
        while end < len(lines) and laid[end] is not None:
            end += 1
        block = [item for item in laid[position:end] if item and item[1]]
        widths = [width(code) + 1 for code, _ in block if "\t" in code]
        fitting = [w for w in widths if w <= comment_column + 2 * TAB]
        column = max([comment_column, *(-(-w // TAB) * TAB for w in fitting)])
        for index in range(position, end):
            code, comment = laid[index] or ("", "")
            line = code
            if comment:
                tabs = -(-(column - width(code)) // TAB)
                line += "\t" * (tabs if tabs > 0 and "\t" in code else 1) + comment
            if same(lines[index], line):
                result[index] = line
        position = end + 1
    return "\n".join(result) + "\n" if result else ""


def format_line(text: str, comment_column: int = COMMENT_COLUMN) -> str:
    """Lay one line out on its own."""
    return (format_text(text, comment_column) or text + "\n")[:-1]


def format_files(
    paths: Iterable[Path],
    comment_column: int = COMMENT_COLUMN,
    write: bool = True,
) -> tuple[list[str], list[str], list[str]]:
    """Lay source files out.

    Returns the files that change (and are rewritten, with ``write``), the
    files passed over as being for the GNU assembler, and a diff of all the
    changes.
    """
    changed, passed, diff = [], [], []
    for path in map(Path, paths):
        before, ending = read_file(path)
        after = format_text(before, comment_column)
        if after is None:
            passed.append(str(path))
        elif after != before:
            changed.append(str(path))
            diff += difflib.unified_diff(
                before.splitlines(),
                after.splitlines(),
                str(path),
                str(path),
                lineterm="",
            )
            if write:
                write_file(path, after, ending)
    return changed, passed, diff
