"""What 68000 instructions do to registers."""

import re

from asm68klint.registers import REGISTER_PATTERN, STACK, canonical, parse_list
from asm68klint.source import Statement, words

CONDITIONS = words("hi ls cc hs cs lo ne eq vc vs pl mi ge lt gt le")
BRANCHES = {f"b{condition}" for condition in CONDITIONS}
SET_ON_CONDITION = {f"s{condition}" for condition in CONDITIONS | {"t", "f"}}
LOOPS = {f"db{condition}" for condition in CONDITIONS | {"t", "f"}} | {"dbra"}

# Instructions whose last operand is the destination.
WRITES_LAST = words(
    "move movea moveq movep movem lea"
    " add adda addi addq addx sub suba subi subq subx"
    " and andi or ori eor eori mulu muls divu divs abcd sbcd"
    " asl asr lsl lsr rol ror roxl roxr bset bclr bchg"
)
# Instructions that write their only operand.
WRITES_ONLY = words("clr neg negx not nbcd swap ext tas unlk")
WRITES_ONLY |= SET_ON_CONDITION
# Instructions that write their first operand.
WRITES_FIRST = LOOPS | {"link"}
# Instructions that write both operands.
WRITES_BOTH = {"exg"}
# Instructions that change no register operand.
WRITES_NONE = words(
    "tst cmp cmpa cmpi cmpm btst chk pea nop stop reset trap trapv illegal"
    " bra bsr jmp jsr rts rte rtr"
)
WRITES_NONE |= BRANCHES

# Instructions that write their destination without using what was in it.
OVERWRITES = words("move movea moveq lea clr movem") | SET_ON_CONDITION
# Instructions that clear a register when both operands are that register.
CLEARS_ITSELF = words("sub suba eor")

# Instructions that take a size.
SIZED = (
    (WRITES_LAST | WRITES_ONLY | words("tst cmp cmpa cmpi cmpm chk"))
    - words("lea moveq swap unlk nbcd tas abcd sbcd")
    - SET_ON_CONDITION
)

INSTRUCTIONS = WRITES_LAST | WRITES_ONLY | WRITES_FIRST | WRITES_BOTH | WRITES_NONE

_POSTINCREMENT = re.compile(r"\(\s*(\w+)\s*\)\+")
_PREDECREMENT = re.compile(r"-\(\s*(\w+)\s*\)")
_REGISTER = re.compile(rf"(?<![\w.$])({REGISTER_PATTERN})(?![\w$])", re.IGNORECASE)


def is_instruction(mnemonic: str | None) -> bool:
    """True when the mnemonic is a 68000 instruction."""
    return mnemonic in INSTRUCTIONS


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
    if mnemonic in WRITES_BOTH:
        return operands
    return ()


def written_registers(statement: Statement) -> set[str]:
    """Return the registers an instruction writes.

    That is every data or address register it has as a destination, plus the
    address registers stepped by ``(an)+`` and ``-(an)`` in any operand. Pushes
    and pops through the stack pointer are not included.
    """
    written: set[str] = set()
    for operand in destinations(statement):
        register = canonical(operand)
        if register:
            written.add(register)
        elif statement.mnemonic == "movem":
            written.update(parse_list(operand) or [])
    for operand in statement.operands:
        register = stepped_register(operand)
        if register and register != STACK:
            written.add(register)
    return written


def read_registers(statement: Statement) -> set[str]:
    """Return the registers whose value an instruction uses.

    That is every register in a source operand or inside an address, and a
    destination register unless the instruction only overwrites it. The stack
    pointer is included like any other.
    """
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
        elif mnemonic == "movem" and parse_list(operand) is not None:
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
        if size in "bwl" and stem in SIZED:
            statement.mnemonic, statement.size = stem, size
    return statement
