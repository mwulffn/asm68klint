"""Jump tables: where ``jmp Table(pc,d0.w)`` may go.

Two kinds of table are understood. A table of offsets, read and then jumped
through::

    Table:  dc.w    First-Table, Second-Table

and a row of branches that is jumped into::

    Table:  bra.w   First
            bra.w   Second
"""

import re

from asm68klint.directives import code_label, is_data
from asm68klint.flow import direct_target
from asm68klint.source import LABEL_PATTERN, UNSCOPED, Statement, is_local, label_key

_OFFSET = re.compile(rf"({LABEL_PATTERN})\s*-\s*({LABEL_PATTERN})")
_THROUGH = re.compile(
    rf"({LABEL_PATTERN})\(pc,[^()]*\)|\(({LABEL_PATTERN}),pc,[^()]*\)", re.IGNORECASE
)


def table_used(operand: str) -> str | None:
    """Return the label of the table an operand like ``Table(pc,d0.w)`` goes through."""
    match = _THROUGH.fullmatch(operand)
    return match.group(1) or match.group(2) if match else None


def find_tables(statements: list[Statement]) -> dict[str, list[str]]:
    """Return the tables of a source file: the labels each one leads to.

    A table is kept under the name of its label (see ``label_key``). A local
    label it leads to is given as ``Global\\.local``, so that it can be found
    from anywhere.
    """
    tables: dict[str, list[str]] = {}
    scope = ""
    label = ""  # the label of the table being read, as written
    entries: list[str] = []
    for statement in statements:
        if statement.label:
            if code_label(statement):
                scope = statement.label
            label = statement.label
            entries = tables.setdefault(label_key(scope, label), [])
        if not label or statement.mnemonic is None:
            continue
        found = _entries(statement, label)
        if found is None:
            label = ""
            continue
        for name in found:
            if is_local(name) and not name.startswith(UNSCOPED) and "\\" not in name:
                name = f"{scope}\\{name}"
            if name not in entries:
                entries.append(name)
    return {name: found for name, found in tables.items() if found}


def _entries(statement: Statement, label: str) -> list[str] | None:
    """Return where one line of a table leads, or None if it is not part of one."""
    if statement.mnemonic in ("bra", "jmp") and statement.operands:
        name = direct_target(statement.operands[-1])
        return [name] if name else None
    if not is_data(statement) or statement.mnemonic != "dc":
        return None
    found = []
    for operand in statement.operands:
        match = _OFFSET.fullmatch(operand)
        if not match or match.group(2) != label:
            return None
        found.append(match.group(1))
    return found
