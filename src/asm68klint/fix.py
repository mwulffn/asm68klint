"""Write headers: correct the ``Clobbers`` of those there, add those missing.

A header's ``Clobbers`` field is rewritten to say what the routine's code
changes (less what it lists under ``Out``). With ``infer``, a routine that has
no header is given one: what it reads under ``In``, everything it changes
under ``Clobbers``, for a person to move the results to ``Out``.
"""

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Unpack

from asm68klint import gas
from asm68klint.checks import examine
from asm68klint.options import Settings, make_options
from asm68klint.registers import STACK, format_list
from asm68klint.routines import Routine
from asm68klint.source import UNSCOPED, read_file, write_file
from asm68klint.units import graphs, read_units

_FIELD = re.compile(r"(.*?Clobbers\s*:\s*)(.*)", re.IGNORECASE)
# An edit: the file, the line, the new text of the line, and whether the text
# goes in before the line instead of in its place.
Edit = tuple[str, int, str, bool]


def new_header(routine: Routine, mark: str) -> str:
    """Return the text of a header for a routine that has none."""
    fields = routine.header.fields
    return (
        f"{mark}--\n{mark} {routine.header.name}\n"
        f"{mark} In:       {format_list(fields['In'].registers)}\n"
        f"{mark} Out:      -\n"
        f"{mark} Clobbers: {format_list(fields['Clobbers'].registers)}"
    )


def fix_files(paths: Iterable[Path], **settings: Unpack[Settings]) -> dict[str, int]:
    """Correct and add headers in the source files; return the changes per file.

    The settings are those of ``make_options``. A routine whose code could not
    be followed is left as it is.
    """
    options = make_options(**settings)
    units, _ = read_units(paths, options)
    edits: list[Edit] = []
    texts: dict[str, list[str]] = {}
    endings: dict[str, str] = {}

    def lines_of(file: str) -> list[str]:
        if file not in texts:
            text, endings[file] = read_file(Path(file))
            texts[file] = text.splitlines()
        return texts[file]

    for routine, graph in graphs(units, options):
        header = routine.header
        name = header.name or ""
        if routine.inferred:
            if name.startswith(("(", UNSCOPED)):
                continue
            lines = lines_of(header.file)
            forced = options.syntax == "gas"
            mark = "|" if forced or gas.looks_like_gas(lines) else ";"
            if options.syntax == "motorola":
                mark = ";"
            edits.append((header.file, header.line, new_header(routine, mark), True))
            continue
        summary, _ = examine(routine, graph, set(options.reserved))
        field = header.fields.get("Clobbers")
        if header.problems or not field or not (summary.returns and summary.analysed):
            continue
        actual = summary.changed - header.registers("Out") - {STACK}
        if summary.is_interrupt or actual == set(field.registers):
            continue
        match = _FIELD.fullmatch(lines_of(header.file)[field.line - 1])
        if match:
            text = match.group(1) + format_list(actual)
            edits.append((header.file, field.line, text, False))
    changed: dict[str, int] = {}
    for file, line, text, before in sorted(set(edits), reverse=True):
        lines = lines_of(file)
        lines[line - 1 : line - 1 + (not before)] = text.split("\n")
        changed[file] = changed.get(file, 0) + 1
    for file in changed:
        write_file(Path(file), "\n".join(texts[file]) + "\n", endings[file])
    return changed
