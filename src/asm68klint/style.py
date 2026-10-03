"""The style rules: the ``T`` group, which is off unless selected."""

import re
from pathlib import Path

from asm68klint.findings import Finding
from asm68klint.m68k import BRANCHES, FLOAT, OVERWRITES, SIZED, is_instruction
from asm68klint.options import Options
from asm68klint.platforms import PLATFORMS
from asm68klint.source import Statement, problem, words
from asm68klint.values import Values, evaluate, scan

# Instructions that are to have a size written: those that take one, less the
# bit operations and the few with one size only.
NEEDS_SIZE = (
    SIZED - FLOAT - words("bset bclr bchg movec moves movep mulu muls divu divs")
) | words("tst cmp cmpa cmpi cmpm")
SIZED_BRANCHES = BRANCHES | {"bra", "bsr"}
_INDEXED = re.compile(
    r"(?:(?P<before>[^()]*)\((?P<base>a[0-7]|sp),|\((?P<inside>[^(),]+),(?P<base2>a[0-7]|sp),)"
    r"\s*[da][0-7]",
    re.IGNORECASE,
)
_NAME = re.compile(r"[A-Za-z_.@][\w.@]*")

# The Amiga's custom chip registers that can only be written, by Commodore's
# names; a register that is read has another name (dmaconr) and is not here.
_WRITE_ONLY = re.compile(
    r"dskpt[hl]?|dsklen|dskdat|refptr|vposw|vhposw|copcon|serdat|serper|potgo|joytest"
    r"|str(equ|vbl|hor|long)|bltcon[01]l?|blta[fl]wm|blt[abcd]pt[hl]?|bltsiz[ehv]"
    r"|blt[abcd]mod|blt[abc]dat|sprhdat|dsksync|cop[12]lc[hl]?|copjmp[12]|copins"
    r"|diwstrt|diwstop|diwhigh|ddfstrt|ddfstop|dmacon|clxcon|intena|intreq|adkcon"
    r"|aud[0-3](lc[hl]?|len|per|vol|dat)|bpl[1-8]pt[hl]?|bplcon[0-4]|bpl[12]mod"
    r"|bpl[1-8]dat|spr[0-7](pt[hl]?|pos|ctl|data|datb)|color[0-3]\d|beamcon0|fmode"
    r"|htotal|hsstop|hbstrt|hbstop|vtotal|vsstop|vbstrt|vbstop|hsstrt|vsstrt|hcenter",
    re.IGNORECASE,
)
# By address: $dff000 and up, less the registers that are read.
_ADDRESS = re.compile(r"\$dff([0-9a-f]{3})\b", re.IGNORECASE)
_READABLE = range(0x020)
NO_VALUE_NEEDED = ("lea", "pea")


def write_only(operand: str) -> str | None:
    """Return the write-only hardware register an operand is, if it is one."""
    if operand.startswith("#"):
        return None
    for name in _NAME.findall(operand):
        if _WRITE_ONLY.fullmatch(name.lstrip("_")):
            return name
    match = _ADDRESS.search(operand)
    if match and int(match.group(1), 16) not in _READABLE and match.group(1) != "07c":
        return match.group(0)
    return None


def check_statement(
    statement: Statement, values: Values, options: Options
) -> list[Finding]:
    """Check one instruction against the style rules."""
    found = []
    mnemonic, operands = statement.mnemonic or "", statement.operands
    if mnemonic in NEEDS_SIZE and statement.size is None:
        found.append(("T003", f"{statement.name} has no size: write {mnemonic}.w"))
    if mnemonic in SIZED_BRANCHES and statement.size is not None:
        message = (
            f"{statement.name}.{statement.size} has a size: leave it to the assembler"
        )
        found.append(("T004", message))
    if options.cpu in (None, "68000", "68010"):
        for operand in operands:
            match = _INDEXED.match(operand)
            text = match and (match.group("before") or match.group("inside") or "0")
            value = evaluate(text, values.symbols) if text else None
            if value is not None and not -128 <= value <= 127:
                message = (
                    f"the displacement {text} is {value}: with an index register"
                    " it must be -128 to 127"
                )
                found.append(("T005", message))
    if options.platform is PLATFORMS["amiga"] and mnemonic not in NO_VALUE_NEEDED:
        reads_last = mnemonic not in OVERWRITES or mnemonic == "clr"
        old = options.cpu in (None, "68000")  # a later processor's clr does not read
        for position, operand in enumerate(operands):
            name = write_only(operand)
            last = position == len(operands) - 1
            if not name or (last and not reads_last) or (mnemonic == "clr" and not old):
                continue
            message = f"{mnemonic} reads {name}, which can only be written"
            if mnemonic == "clr":
                message = (
                    f"clr reads {name} before it writes it, and it can only be"
                    " written: write move #0"
                )
            found.append(("T001", message))
    return [problem(statement, code, message) for code, message in found]


def check_fields(values: Values) -> list[Finding]:
    """Report the word and long fields that are at an odd offset."""
    findings = []
    for item in values.fields:
        if item.odd and item.size in ("w", "l"):
            kind = "word" if item.size == "w" else "long"
            where = "" if item.offset is None else f" ({item.offset})"
            message = (
                f"{item.name} is a {kind} field at an odd offset{where}: a 68000"
                " cannot read or write it"
            )
            findings.append(problem(item.statement, "T002", message))
    return findings


def names_used(statements: list[Statement], but: tuple[str, ...]) -> set[str]:
    """Return every name in the operands of a file, but those of some directives."""
    used: set[str] = set()
    for statement in statements:
        if statement.mnemonic not in but:
            for operand in statement.operands:
                used.update(_NAME.findall(operand))
    return used


def check_imports(paths: list[Path], sources: list[list[Statement]]) -> list[Finding]:
    """Report names imported and not used, and names exported that nobody imports."""
    findings = []
    imports = ("xref", "nref")
    exports = ("xdef", "public", "global")
    used = [names_used(statements, imports + exports) for statements in sources]
    wanted = [names_used(statements, exports) for statements in sources]
    for index, (path, statements) in enumerate(zip(paths, sources, strict=True)):
        others: set[str] = set().union(*wanted[:index], *wanted[index + 1 :])
        for statement in statements:
            own = statement.file == str(path) and not statement.macro
            if not own or statement.mnemonic not in imports + exports:
                continue
            for name in statement.operands:
                if statement.mnemonic in imports and name not in used[index]:
                    message = f"{name} is imported and not used"
                    findings.append(problem(statement, "T006", message))
                elif statement.mnemonic in exports and name not in others:
                    message = f"{name} is exported and no other file given uses it"
                    if len(sources) > 1:
                        findings.append(problem(statement, "T007", message))
    return findings


def check_style(
    paths: list[Path], sources: list[list[Statement]], options: Options
) -> set[Finding]:
    """Check the source files against the style rules."""
    findings: set[Finding] = set()
    for statements in sources:
        values = scan(statements, options.symbols)
        findings.update(check_fields(values))
        for statement in statements:
            if is_instruction(statement.mnemonic) and not statement.is_macro_call:
                findings.update(check_statement(statement, values, options))
    findings.update(check_imports(paths, sources))
    return findings
