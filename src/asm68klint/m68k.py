"""What the instructions of the 68000 family do to registers.

The instructions are those of the 68000, 68010, 68020, 68030, 68040 and 68060
and of the floating point unit (the 68881 and 68882, and the one built into
the 68040 and 68060). Everything that depends on the processor is here and in
``registers.py``.
"""

import re

from asm68klint.registers import REGISTER_PATTERN, STACK, canonical, parse_list
from asm68klint.source import Statement, words

CPUS = ("68000", "68010", "68020", "68030", "68040", "68060")

CONDITIONS = words("hi ls cc hs cs lo ne eq vc vs pl mi ge lt gt le")
FLOAT_CONDITIONS = words(
    "f eq ogt oge olt ole ogl or un ueq ugt uge ult ule ne t"
    " sf seq gt ge lt le gl gle ngle ngl nle nlt nge ngt sne st"
)
BRANCHES = {f"b{condition}" for condition in CONDITIONS}
BRANCHES |= {f"fb{condition}" for condition in FLOAT_CONDITIONS}
SET_ON_CONDITION = {f"s{condition}" for condition in CONDITIONS | {"t", "f"}}
SET_ON_CONDITION |= {f"fs{condition}" for condition in FLOAT_CONDITIONS}
LOOPS = {f"db{condition}" for condition in CONDITIONS | {"t", "f"}} | {"dbra"}
LOOPS |= {f"fdb{condition}" for condition in FLOAT_CONDITIONS}
TRAPS = {f"trap{condition}" for condition in CONDITIONS | {"t", "f"}}
TRAPS |= {f"ftrap{condition}" for condition in FLOAT_CONDITIONS}

# Floating point instructions that put their result in their last operand.
FLOAT_ARITHMETIC = words(
    "fabs facos fadd fasin fatan fatanh fcos fcosh fdiv fetox fetoxm1 fgetexp"
    " fgetman fint fintrz flog10 flog2 flogn flognp1 fmod fmove fmul fneg frem"
    " fscale fsgldiv fsglmul fsin fsinh fsqrt fsub ftan ftanh ftentox ftwotox"
    " fsadd fdadd fsmove fdmove fsmul fdmul fsdiv fddiv fssub fdsub fsabs fdabs"
    " fsneg fdneg fssqrt fdsqrt fsincos fmovecr fmovem"
)
FLOAT = FLOAT_ARITHMETIC | words("fcmp ftst fnop fsave frestore")
FLOAT |= {
    name for name in BRANCHES | SET_ON_CONDITION | LOOPS | TRAPS if name[0] == "f"
}

# Instructions whose last operand is the destination.
WRITES_LAST = words(
    "move movea moveq movep movem lea"
    " add adda addi addq addx sub suba subi subq subx"
    " and andi or ori eor eori mulu muls divu divs abcd sbcd"
    " asl asr lsl lsr rol ror roxl roxr bset bclr bchg"
    " movec moves bfexts bfextu bfffo bfins divul divsl move16"
    " pmove pmovefd"
)
WRITES_LAST |= FLOAT_ARITHMETIC
# Instructions that write their only operand.
WRITES_ONLY = words("clr neg negx not nbcd swap ext extb tas unlk bfchg bfclr bfset")
WRITES_ONLY |= SET_ON_CONDITION
# Instructions that write their first operand.
WRITES_FIRST = LOOPS | words("link cas cas2")
# Instructions that write their second operand of three.
WRITES_SECOND = words("pack unpk")
# Instructions that write both operands.
WRITES_BOTH = {"exg"}
# Instructions that change no register operand.
WRITES_NONE = words(
    "tst cmp cmpa cmpi cmpm btst chk pea nop stop reset trap trapv illegal"
    " bra bsr jmp jsr rts rte rtr"
    " rtd bkpt bftst chk2 cmp2 callm rtm"
    " pflush pflusha pflushn pflushan pflushr pload ploadr ploadw ptest ptestr"
    " ptestw cinvl cinvp cinva cpushl cpushp cpusha plpar plpaw lpstop"
    " fcmp ftst fnop fsave frestore"
)
WRITES_NONE |= BRANCHES | TRAPS

# Instructions that write their destination without using what was in it.
OVERWRITES = words("move movea moveq lea clr movem movec moves bfexts bfextu bfffo")
OVERWRITES |= SET_ON_CONDITION | words("fmove fmovem fmovecr")
# Instructions that clear a register when both operands are that register.
CLEARS_ITSELF = words("sub suba eor")

INSTRUCTIONS = (
    WRITES_LAST | WRITES_ONLY | WRITES_FIRST | WRITES_SECOND | WRITES_BOTH | WRITES_NONE
)
# Instructions that take a size.
SIZED = (
    (WRITES_LAST | WRITES_ONLY | words("tst cmp cmpa cmpi cmpm chk fcmp ftst"))
    - words("lea moveq swap unlk nbcd tas abcd sbcd")
    - SET_ON_CONDITION
)

# The first processor that has each instruction the 68000 has not.
SINCE = {
    **dict.fromkeys(words("movec moves rtd bkpt"), "68010"),
    **dict.fromkeys(
        words(
            "bfchg bfclr bfexts bfextu bfffo bfins bfset bftst callm rtm cas cas2"
            " chk2 cmp2 extb pack unpk divul divsl"
        )
        | {name for name in TRAPS if name[0] != "f"},
        "68020",
    ),
    **dict.fromkeys(
        words("pmove pmovefd pflush pflusha pflushr pload ploadr ploadw ptest"),
        "68030",
    ),
    **dict.fromkeys(
        words("move16 cinvl cinvp cinva cpushl cpushp cpusha pflushn pflushan"),
        "68040",
    ),
    **dict.fromkeys(words("plpar plpaw lpstop"), "68060"),
}
# In these the 68030's and the 68040's forms have the same name.
SINCE.update(dict.fromkeys(words("ptestr ptestw"), "68030"))

# How many bytes an operand of each size takes on the stack.
WIDTHS = {"b": 2, "w": 2, "l": 4, "s": 4, "d": 8, "x": 12, "p": 12}

_POSTINCREMENT = re.compile(r"\(\s*(\w+)\s*\)\+")
_PREDECREMENT = re.compile(r"-\(\s*(\w+)\s*\)")
_REGISTER = re.compile(rf"(?<![\w.$])({REGISTER_PATTERN})(?![\w$])", re.IGNORECASE)
_BIT_FIELD = re.compile(r"\{[^}]*\}$")


def is_instruction(mnemonic: str | None) -> bool:
    """True when the mnemonic is an instruction of the 68000 family."""
    return mnemonic in INSTRUCTIONS


def needs(mnemonic: str | None, cpu: str, fpu: bool) -> str | None:
    """Return what an instruction needs that the chosen processor has not.

    ``fpu`` says there is a floating point unit; the 68040 and 68060 have one.
    """
    if mnemonic in FLOAT:
        return None if fpu or cpu in ("68040", "68060") else "a floating point unit"
    first = SINCE.get(mnemonic or "", "68000")
    return None if CPUS.index(first) <= CPUS.index(cpu) else f"a {first}"


def width(statement: Statement) -> int:
    """Return the bytes an instruction's operand takes on the stack."""
    default = "x" if statement.mnemonic in FLOAT else "w"
    return WIDTHS.get(statement.size or default, 2)


def saves_whole(statement: Statement) -> bool:
    """True when an instruction moves all of a register, so that it can be saved."""
    if statement.mnemonic in ("fmove", "fmovem"):
        return statement.size in (None, "x")
    return statement.size == "l"


def stepped_register(operand: str) -> str | None:
    """Return the address register that ``(an)+`` or ``-(an)`` changes."""
    match = _POSTINCREMENT.fullmatch(operand) or _PREDECREMENT.fullmatch(operand)
    return canonical(match.group(1)) if match else None


def destinations(statement: Statement) -> tuple[str, ...]:
    """Return the operands an instruction writes to."""
    mnemonic, operands = statement.mnemonic, statement.operands
    if not operands:
        return ()
    if mnemonic in WRITES_LAST or mnemonic in WRITES_ONLY:
        return operands[-1:]
    if mnemonic in WRITES_FIRST:
        return operands[:1]
    if mnemonic in WRITES_SECOND:
        return operands[1:2]
    if mnemonic in WRITES_BOTH:
        return operands
    return ()


def written_registers(statement: Statement) -> set[str]:
    """Return the registers an instruction writes.

    That is every register it has as a destination, plus the address registers
    stepped by ``(an)+`` and ``-(an)`` in any operand. A destination may be a
    pair (``d2:d3``, of a long division) or have a bit field (``d1{4:8}``).
    Pushes and pops through the stack pointer are not included.
    """
    kept = statement.__dict__.get("_written")
    if kept is not None:
        return set(kept)
    written: set[str] = set()
    lists = statement.mnemonic in ("movem", "fmovem")
    for operand in destinations(statement):
        for part in _BIT_FIELD.sub("", operand).split(":"):
            register = canonical(part)
            if register:
                written.add(register)
            elif lists:
                written.update(parse_list(part) or [])
    for operand in statement.operands:
        register = stepped_register(operand)
        if register and register != STACK:
            written.add(register)
    statement.__dict__["_written"] = frozenset(written)
    return written


def read_registers(statement: Statement) -> set[str]:
    """Return the registers whose value an instruction uses.

    That is every register in a source operand or inside an address, and a
    destination register unless the instruction only overwrites it. The stack
    pointer is included like any other.
    """
    kept = statement.__dict__.get("_read")
    if kept is None:
        kept = statement.__dict__["_read"] = frozenset(_read_registers(statement))
    return set(kept)


def _read_registers(statement: Statement) -> set[str]:
    mnemonic, operands = statement.mnemonic, statement.operands
    names = [canonical(operand) for operand in operands]
    if mnemonic in CLEARS_ITSELF and len(names) == 2 and names[0] == names[1]:
        return set()
    read: set[str] = set()
    for position, operand in enumerate(operands):
        overwritten = mnemonic in OVERWRITES and position == len(operands) - 1
        if names[position]:
            if not overwritten:
                read.add(names[position])
        elif mnemonic in ("movem", "fmovem") and parse_list(operand) is not None:
            if not overwritten:
                read.update(parse_list(operand) or [])
        else:
            for name in _REGISTER.findall(operand):
                read.add(canonical(name) or "")
    return read


def normalise(statement: Statement) -> Statement:
    """Give an instruction written in another way its usual mnemonic.

    ``movel`` is ``move.l``: some assemblers take the size without its dot.
    """
    mnemonic = statement.mnemonic
    if mnemonic and mnemonic not in INSTRUCTIONS and statement.size is None:
        stem, size = mnemonic[:-1], mnemonic[-1]
        sizes = "bwlsdxp" if stem in FLOAT else "bwl"
        if size in sizes and stem in SIZED:
            statement.mnemonic, statement.size = stem, size
    return statement


# Instructions that have a small number in the instruction word itself.
QUICK = words("moveq addq subq asl asr lsl lsr rol ror roxl roxr trap")
_PLAIN = re.compile(rf"-?\(\s*{REGISTER_PATTERN}\s*\)\+?", re.IGNORECASE)


def is_one_word(statement: Statement) -> bool:
    """True when an instruction is certain to take one word of the program.

    That is one whose operands are registers, ``(an)``, ``(an)+`` or
    ``-(an)``, or the small number of ``moveq``, ``addq`` and the shifts.
    """
    mnemonic = statement.mnemonic
    if mnemonic not in INSTRUCTIONS or mnemonic in FLOAT | LOOPS | BRANCHES:
        return False
    if mnemonic in ("bsr", "jsr", "jmp", "bra", "movem", "link", "stop", "rtd"):
        return False
    for position, operand in enumerate(statement.operands):
        quick = mnemonic in QUICK and position == 0 and operand.startswith("#")
        if not (canonical(operand) or _PLAIN.fullmatch(operand) or quick):
            return False
    return True
