"""The values of names: equates, and the fields laid out with ``rs``.

Only what the source itself says is worked out, for the build that is told
nothing on the assembler's command line but what the user gave the linter. A
name whose value depends on conditional assembly that cannot be decided, or
on anything this does not read, has no value here, and nothing is concluded
from it.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from asm68klint.directives import ELSE, ELSE_IF, END_IF, IF, decided
from asm68klint.source import Statement

_TOKEN = re.compile(
    r"\s*(\$[0-9a-fA-F]+|%[01]+|0[xX][0-9a-fA-F]+|\d+|'[^']{1,4}'|\"[^\"]{1,4}\""
    r"|[A-Za-z_.@][\w.@]*|<<|>>|[-+*/()&|^~!])"
)
EQUATES = ("equ", "=", "set")
SIZES = {"b": 1, "w": 2, "l": 4}
COUNTER = "__RS"


@dataclass
class Field:
    """A field laid out with ``rs``: where it is, if that is known."""

    statement: Statement
    name: str
    size: str
    offset: int | None
    odd: bool | None


@dataclass
class Values:
    """The names of a source file with their values, and its fields."""

    symbols: dict[str, int | None] = field(default_factory=dict)
    fields: list[Field] = field(default_factory=list)


def evaluate(text: str, symbols: Mapping[str, int | None]) -> int | None:
    """Return the value of an expression, or None if it cannot be worked out."""
    parts = []
    position = 0
    text = text.strip()
    while position < len(text):
        match = _TOKEN.match(text, position)
        if not match:
            return None
        token = match.group(1)
        position = match.end()
        if token[0] == "$":
            parts.append(str(int(token[1:], 16)))
        elif token[0] == "%":
            parts.append(str(int(token[1:], 2)))
        elif token[0] in "'\"":
            parts.append(str(int.from_bytes(token[1:-1].encode("latin-1"), "big")))
        elif token[0].isdigit():
            parts.append(
                str(int(token, 0) if token[:2].lower() == "0x" else int(token))
            )
        elif token[0].isalpha() or token[0] in "_.@":
            value = symbols.get(token)
            if value is None:
                return None
            parts.append(f"({value})")
        else:
            parts.append({"/": "//", "!": "|"}.get(token, token))
    try:
        value = eval(" ".join(parts), {"__builtins__": {}}) if parts else None
    except (SyntaxError, ArithmeticError, TypeError, ValueError):
        return None
    return value if isinstance(value, int) else None


class _Scanner:
    """Reads statements in order and keeps the values and the ``rs`` counter."""

    def __init__(self, given: Mapping[str, str | None]) -> None:
        self.values = Values()
        self.given = given
        self.counter: int | None = 0
        self.odd: bool | None = False
        # Open conditionals. One the text decides: [a branch was taken, this
        # one is]. Any other: the counter before it and at the end of each of
        # its branches so far.
        self.open: list[list] = []

    def unsure(self) -> bool:
        """True inside a conditional that goes both ways."""
        return any(len(item) == 3 for item in self.open)

    def skipping(self) -> bool:
        """True inside a branch that a decided conditional does not take."""
        return any(len(item) == 2 and not item[1] for item in self.open)

    def outcome(self, statement: Statement) -> bool | None:
        """Decide a conditional by the names given and the values worked out.

        A name that neither the user nor the source so far has defined is not
        defined: ``ifnd SIZE`` / ``SIZE equ 4`` / ``endc`` gives SIZE the value
        it has in a build that is told nothing else.
        """
        known = {
            name: str(value)
            for name, value in self.values.symbols.items()
            if value is not None
        }
        found = decided(statement, {**known, **self.given})
        name = "".join(statement.operands)
        if found is None and statement.mnemonic in ("ifd", "ifnd"):
            return (name in self.values.symbols) == (statement.mnemonic == "ifd")
        return found

    def conditional(self, statement: Statement) -> None:
        """Follow conditional assembly."""
        mnemonic = statement.mnemonic
        here = (self.counter, self.odd)
        if mnemonic in IF:
            outcome = self.outcome(statement)
            if self.skipping():
                self.open.append([True, False])
            elif outcome is None:
                self.open.append([here, [], False])
            else:
                self.open.append([outcome, outcome])
        elif not self.open:
            return
        elif len(self.open[-1]) == 2:
            item = self.open[-1]
            if mnemonic in END_IF:
                self.open.pop()
            else:
                holds = mnemonic in ELSE or self.outcome(statement) is not False
                item[1] = not item[0] and holds
                item[0] = item[0] or item[1]
        else:
            before, ends, has_else = self.open[-1]
            ends.append(here)
            if mnemonic in END_IF:
                self.open.pop()
                ends = ends if has_else else [*ends, before]
                counters = {end[0] for end in ends}
                parities = {end[1] for end in ends}
                self.counter = counters.pop() if len(counters) == 1 else None
                self.odd = parities.pop() if len(parities) == 1 else None
            else:
                self.open[-1][2] = self.open[-1][2] or mnemonic in ELSE
                self.counter, self.odd = before

    def define(self, name: str, value: int | None) -> None:
        """Give a name its value; none if it has two, or may not be defined."""
        symbols = self.values.symbols
        if self.unsure() or (name in symbols and symbols[name] != value):
            value = None
        symbols[name] = value

    def add(self, statement: Statement) -> None:
        """Take in the next statement."""
        mnemonic = statement.mnemonic
        if mnemonic in IF | ELSE | ELSE_IF | END_IF:
            self.conditional(statement)
            return
        if self.skipping() or mnemonic is None:
            return
        symbols = {**self.values.symbols, COUNTER: self.counter}
        operand = ",".join(statement.operands)
        if mnemonic in EQUATES and statement.label:
            self.define(statement.label, evaluate(operand, symbols))
        elif mnemonic in ("rsreset", "rsset"):
            self.counter = evaluate(operand, symbols) if mnemonic == "rsset" else 0
            self.odd = None if self.counter is None else bool(self.counter & 1)
        elif mnemonic == "rs":
            size = statement.size or "w"
            if statement.label:
                self.define(statement.label, self.counter)
                self.values.fields.append(
                    Field(statement, statement.label, size, self.counter, self.odd)
                )
            count = evaluate(operand or "1", symbols)
            step = None if count is None else count * SIZES.get(size, 2)
            self.counter = None if None in (step, self.counter) else self.counter + step
            if size == "b":  # only bytes can make the place odd
                self.odd = (
                    None if None in (count, self.odd) else self.odd ^ bool(count & 1)
                )


def scan(statements: list[Statement], given: Mapping[str, str | None]) -> Values:
    """Work out the values of the names in a source file.

    ``given`` are the names the user has defined or said are not: they decide
    conditional assembly, as everywhere.
    """
    scanner = _Scanner(given)
    for statement in statements:
        if not statement.is_macro_call:
            scanner.add(statement)
    return scanner.values
