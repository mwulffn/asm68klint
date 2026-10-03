"""Read source written for the GNU assembler, in its Motorola style.

That is ``move.l 8(%sp),%d0`` or, with its option for it, the same without the
percent signs: the operands of Motorola's syntax, with the GNU assembler's
labels, directives and comments, and often the C preprocessor's lines. (Its
other style, ``movel sp@(8),d0``, is not read.)

A statement is turned into the same form as one in Motorola's syntax, so that
the rest of the linter knows of one syntax only: registers without their
percent sign, ``jbsr`` as ``bsr``, ``.long`` as ``dc.l``, ``#ifdef`` as ``ifd``.
"""

import re

from asm68klint.directives import GAS_SILENT as SILENT
from asm68klint.source import NUMBERED, Statement, words

_SIGNS = (
    re.compile(
        r"^\s*(\w+:\s*)?\.(text|data|bss|globl|global|section|macro|align|long|equ"
        r"|set|include|word|byte|dc|ds|even)\b",
        re.IGNORECASE,
    ),
    re.compile(r"^\s*#\s*(include|define|ifdef|ifndef|if|endif)\b"),
    re.compile(r"%(d[0-7]|a[0-7]|sp)\b"),
    re.compile(r"^\s+(jbsr|jra|jeq|jne|movm)\b"),
    re.compile(r"^\s*(//|/\*|\|)"),
    re.compile(r"^\d+:"),
)
_LABEL = re.compile(r"\s*([A-Za-z_.$][\w.$]*|\d+)\s*:(?!:)")
_REGISTER = re.compile(
    r"%(d[0-7]|a[0-7]|fp[0-7]|sp|pc|fp|sr|ccr|usp|za\d|zd\d|fpcr|fpsr|fpiar|vbr|sfc|dfc|cacr|caar|msp|isp)\b",
    re.IGNORECASE,
)
_NUMBERED_USE = re.compile(r"(?<![\w.$])(\d+)([bf])(?![\w$])")
_ASSIGNMENT = re.compile(r"\s*([A-Za-z_.$][\w.$]*)\s*=(?!=)(.*)")
_DOT_LABEL = re.compile(r"(?<![\w.$)])\.(?=[A-Za-z_])")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/")

DATA = words(
    "byte ascii asciz string string8 word short hword long int quad octa float"
    " double single incbin dc dcb"
)
SPACE = words("space skip fill zero ds")
RENAMED = {
    "globl": "xdef",
    "global": "xdef",
    "ifdef": "ifd",
    "ifndef": "ifnd",
    "elseif": "elif",
    "endif": "endc",
    "set": "equ",
    "equiv": "equ",
    "movm": "movem",
    "movq": "moveq",
    "endc": "endc",
    "jbsr": "bsr",
    "jra": "bra",
    "jbra": "bra",
}
# Lines of the C preprocessor.
PREPROCESSOR = {
    "if": "if",
    "ifdef": "ifd",
    "ifndef": "ifnd",
    "elif": "elif",
    "else": "else",
    "endif": "endc",
    "include": "include",
}


def looks_like_gas(lines: list[str]) -> bool:
    """True when source looks written for the GNU assembler."""
    found = 0
    for line in lines[:2000]:
        found += any(sign.search(line) for sign in _SIGNS)
        if found >= 3:
            return True
    return False


def strip_block_comments(lines: list[str]) -> list[str]:
    """Blank out ``/* ... */`` comments, keeping every line where it was."""
    result = []
    inside = False
    for line in lines:
        if inside:
            end = line.find("*/")
            if end < 0:
                result.append("")
                continue
            line = line[end + 2 :]
            inside = False
        line = _BLOCK_COMMENT.sub(" ", line)
        start = line.find("/*")
        if start >= 0:
            line = line[:start]
            inside = True
        result.append(line)
    return result


def split_comment(text: str) -> tuple[str, str]:
    """Split a line into code and comment: ``|``, ``//`` or ``;`` starts one.

    So does ``*`` as the first thing on a line.
    """
    if text.lstrip().startswith("*"):
        return "", text.lstrip()[1:]
    quote = ""
    for position, char in enumerate(text):
        if quote:
            quote = "" if char == quote else quote
        elif char in "'\"":
            quote = char
        elif char in "|;" or text.startswith("//", position):
            return text[:position], text[position + 1 :]
    return text, ""


def split_operands(field: str) -> tuple[str, ...]:
    """Split an operand field at the commas outside brackets and quotes.

    Blanks around an operand are dropped; the field is the whole of the line.
    """
    operands: list[str] = []
    current = ""
    depth = 0
    quote = ""
    for char in field.strip():
        if quote:
            quote = "" if char == quote else quote
        elif char in "'\"":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            operands.append(current.strip())
            current = ""
            continue
        current += char
    if current.strip() or operands:
        operands.append(current.strip())
    return tuple(operands)


class Numbered:
    """Gives the labels ``1:``, ``2:`` ... of one file names of their own."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    def define(self, number: str) -> str:
        """Return the name of the label defined here."""
        self.counts[number] = self.counts.get(number, 0) + 1
        return f"{NUMBERED}{number}_{self.counts[number]}"

    def use(self, operand: str) -> str:
        """Replace ``1b`` and ``1f`` in an operand by the labels they mean."""

        def name(match: re.Match) -> str:
            number, way = match.groups()
            count = self.counts.get(number, 0) + (way.lower() == "f")
            return f"{NUMBERED}{number}_{count}"

        return _NUMBERED_USE.sub(name, operand)


def _preprocessor(statement: Statement, code: str) -> Statement:
    """Read a line of the C preprocessor: a conditional, an include, or nothing."""
    name, _, rest = code.lstrip()[1:].strip().partition(" ")
    statement.comment = code
    if name in PREPROCESSOR:
        statement.name = statement.mnemonic = PREPROCESSOR[name]
        rest = rest.strip()
        if name == "include":
            rest = rest.strip('<>"')
        statement.operands = (rest,) if rest else ()
    return statement


def parse_statement(
    file: str, line: int, text: str, numbered: Numbered | None = None
) -> Statement:
    """Split one line into label, mnemonic, size, operands and comment."""
    numbered = numbered or Numbered()
    code, comment = split_comment(text)
    statement = Statement(file, line, text, comment=comment)
    if code.lstrip().startswith("#"):
        return _preprocessor(statement, code)
    match = _LABEL.match(code)
    if match:
        label = match.group(1)
        statement.label = numbered.define(label) if label.isdigit() else label
        if label.startswith("."):
            statement.label = "." + label
        code = code[match.end() :]
    assignment = _ASSIGNMENT.fullmatch(code)
    if assignment:
        statement.label = assignment.group(1)
        statement.name = statement.mnemonic = "="
        statement.operands = (assignment.group(2).strip(),)
        return statement
    code = code.strip()
    if not code:
        return statement
    name = code.split()[0]
    field = _REGISTER.sub(lambda found: found.group(1), code[len(name) :])
    field = re.sub(r"(?<![\w$])fp(?![\w$])", "a6", field) if "%" in code else field
    field = _DOT_LABEL.sub("..", field)
    statement.operands = tuple(map(numbered.use, split_operands(field)))
    _set_mnemonic(statement, name)
    return statement


def _set_mnemonic(statement: Statement, name: str) -> None:
    """Turn a mnemonic or directive into the one Motorola's syntax has for it."""
    lowered = name.lower()
    directive = lowered.startswith(".")
    stem, _, size = lowered.lstrip(".").partition(".")
    statement.name = name
    if directive and lowered in SILENT:
        statement.mnemonic = lowered
    elif directive and stem in DATA:
        statement.mnemonic = "dc"
    elif directive and stem in SPACE:
        statement.mnemonic = "ds"
        statement.operands = statement.operands or ("1",)
    elif directive and stem == "equ" and statement.label:
        statement.mnemonic = "equ"  # Name: .equ value, as Atari's assembler has it
    elif directive and stem == "equ" and statement.operands:
        statement.label = statement.operands[0]
        statement.mnemonic = "equ"
        statement.operands = statement.operands[1:]
    elif directive and stem == "macro":
        # .macro name first, second: the name and the names of the arguments
        parts = " ".join(statement.operands).replace(",", " ").split()
        statement.mnemonic = "macro"
        statement.label = parts[0] if parts else ""
        statement.operands = tuple(part.partition("=")[0] for part in parts[1:])
    else:
        statement.mnemonic = RENAMED.get(stem, stem)
        statement.size = size or None
        if directive and stem not in RENAMED and stem not in KNOWN_AS_MOTOROLA:
            statement.mnemonic = lowered
    if not directive and re.fullmatch(
        r"jb?(cc|cs|eq|ne|ge|gt|le|lt|hi|ls|hs|lo|mi|pl|vc|vs)", stem
    ):
        statement.mnemonic = "b" + stem[-2:]


# Directives that are the same as Motorola's once their dot is gone.
KNOWN_AS_MOTOROLA = words(
    "if ifeq ifne ifgt ifge iflt ifle ifc ifnc ifb ifnb else endm rept endr"
    " include macro irp irpc"
)
