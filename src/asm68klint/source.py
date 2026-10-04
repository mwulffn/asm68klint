"""Read assembly source into statements."""

import re
from dataclasses import dataclass, field
from pathlib import Path

from asm68klint.findings import Finding

_LABEL_AT_START = re.compile(r"([^\s:=;]+)(::?)?")
_LABEL_INDENTED = re.compile(r"\s+([^\s:=;]+)::?")


@dataclass
class Statement:
    """One source line, split into its fields."""

    file: str
    line: int
    text: str
    label: str | None = None
    mnemonic: str | None = None  # lower case, without the size
    name: str = ""  # the mnemonic as written, without the size
    size: str | None = None
    operands: tuple[str, ...] = ()
    comment: str = ""
    # For lines that come from a macro: the macro used in the source, and a
    # number shared by every line of that one use. The line and file are
    # those of the use, not of the macro definition.
    macro: str | None = None
    expansion: int = 0
    is_macro_call: bool = False
    # What ``m68k`` has worked out that the instruction writes and reads, kept
    # for the next time it is asked.
    written: frozenset[str] | None = field(default=None, repr=False, compare=False)
    read: frozenset[str] | None = field(default=None, repr=False, compare=False)

    @property
    def is_comment(self) -> bool:
        """True for a line that holds nothing but a comment."""
        return not self.label and not self.mnemonic and bool(self.text.strip())


def words(text: str) -> set[str]:
    """Split a list of names separated by blanks into a set."""
    return set(text.split())


def problem(statement: Statement, code: str, message: str) -> Finding:
    """Return a finding located at a statement."""
    if statement.macro:
        message += f" (in macro {statement.macro})"
    return Finding(statement.file, statement.line, code, message)


# A label as an operand: a name, a local label (``.name``, ``.1``, ``name$``,
# ``1$``), or a local label of another routine (``Global\.local``).
LABEL_PATTERN = r"(?:[A-Za-z_]\w*\\)?(?:[A-Za-z_.@][\w.@?]*\$?|\d+\$)"


# A label whose name starts with this is neither the label of a routine nor
# one that belongs to the global label before it: it is found from anywhere in
# its file. The GNU assembler's labels are given such names: ``.done`` (to it a
# label like any other) is kept as ``..done``, and ``1:``, found again as ``1b``
# and ``1f``, as ``..n1_`` and a count.
UNSCOPED = ".."
NUMBERED = UNSCOPED + "n"


def label_key(scope: str, label: str) -> str:
    """Return the name a label is kept under, given the global label before it."""
    if label.startswith(UNSCOPED):
        return label
    if "\\" in label:  # Global\.local
        return label.replace("\\", "")
    return scope + label if is_local(label) else label


def is_local(label: str) -> bool:
    """True for a local label (``.name``, ``name$`` or, in asm68k, ``@name``)."""
    return label.startswith((".", "@")) or label.endswith("$")


def split_comment(text: str) -> tuple[str, str]:
    """Split a line into code and the comment that follows the first ``;``."""
    if text.lstrip().startswith("*"):
        return "", text.lstrip()[1:]
    quote = ""
    for position, char in enumerate(text):
        if quote:
            if char == quote:
                quote = ""
        elif char in "'\"":
            quote = char
        elif char == ";":
            return text[:position], text[position + 1 :]
    return text, ""


def split_operands(field: str) -> tuple[str, ...]:
    """Split an operand field at the commas outside brackets and quotes.

    The field ends at the first blank, as in vasm, where the rest of the line
    is a comment. A blank straight after a comma is tolerated.
    """
    operands: list[str] = []
    current = ""
    depth = 0
    quote = ""
    angle = False
    for char in field.strip():
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
            operands.append(current)
            current = ""
            continue
        elif char.isspace():
            if current:
                break
            continue
        current += char
    if current or operands:
        operands.append(current)
    return tuple(operands)


def parse_statement(file: str, line: int, text: str) -> Statement:
    """Split one source line into label, mnemonic, size, operands and comment."""
    code, comment = split_comment(text)
    statement = Statement(file, line, text, comment=comment)
    match = _LABEL_AT_START.match(code) or _LABEL_INDENTED.match(code)
    if match:
        statement.label = match.group(1)
        code = code[match.end() :]
    code = code.strip()
    if code.startswith("*"):  # a comment after a label, as Devpac has it
        statement.comment = code[1:] + statement.comment
        return statement
    if not code:
        return statement
    if code.startswith("="):
        name, field = "=", code[1:]
    else:
        name = code.split()[0]
        field = code[len(name) :]
    name, _, size = name.partition(".")
    statement.name = name
    statement.mnemonic = name.lower()
    statement.size = size.lower() or None
    statement.operands = split_operands(field)
    return statement


def read_file(path: Path) -> tuple[str, str]:
    """Read a source file to be written back: its text and its line ending.

    The text has plain newlines. Bytes that are not UTF-8 (a Latin-1 comment,
    a text in an Amiga's character set) are kept as they are.
    """
    text = Path(path).read_bytes().decode("utf-8", "surrogateescape")
    ending = "\r\n" if "\r\n" in text else "\n"
    return text.replace("\r\n", "\n"), ending


def write_file(path: Path, text: str, ending: str) -> None:
    """Write a source file back with the line ending and the bytes it had."""
    data = text.replace("\n", ending).encode("utf-8", "surrogateescape")
    Path(path).write_bytes(data)
