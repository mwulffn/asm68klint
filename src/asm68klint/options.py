"""The settings of a lint run."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

from asm68klint.m68k import CPUS
from asm68klint.model import Effect
from asm68klint.platforms import PLATFORMS, Platform
from asm68klint.registers import canonical, parse_list
from asm68klint.rules import chosen


class Settings(TypedDict, total=False):
    """The settings of a lint run as they are given: see ``make_options``."""

    reserved: Iterable[str]
    include_dirs: Iterable[Path]
    select: Iterable[str]
    ignore: Iterable[str]
    infer: bool
    platform: str | None
    syntax: str
    extern: str | None
    cpu: str | None
    fpu: bool
    define: Iterable[str]
    undefine: Iterable[str]
    extend_select: Iterable[str]


DEFAULT_RESERVED = ("a5", "a6")
SYNTAXES = ("auto", "motorola", "gas")


@dataclass(frozen=True)
class Options:
    """How to lint: see ``make_options`` for what each setting means."""

    reserved: frozenset[str]
    include_dirs: tuple[Path, ...]
    codes: frozenset[str]
    infer: bool
    platform: Platform | None
    syntax: str
    extern: Effect | None
    cpu: str | None
    fpu: bool
    symbols: Mapping[str, str | None]


def make_options(
    reserved: Iterable[str] = DEFAULT_RESERVED,
    include_dirs: Iterable[Path] = (),
    select: Iterable[str] = (),
    ignore: Iterable[str] = (),
    infer: bool = False,
    platform: str | None = None,
    syntax: str = "auto",
    extern: str | None = None,
    cpu: str | None = None,
    fpu: bool = False,
    define: Iterable[str] = (),
    undefine: Iterable[str] = (),
    extend_select: Iterable[str] = (),
) -> Options:
    """Check the settings of a lint run and put them together.

    ``reserved`` names the registers that may not be written without a
    ``lint: allow`` annotation. ``include_dirs`` are searched for include files.
    ``select`` and ``ignore`` choose the rules to report, by code or by the
    beginning of one; nothing selected means all but the style rules, and
    ``extend_select`` adds to either. With ``infer``, code
    need not have headers: what a routine without one does is worked out.
    ``platform`` names the machine the program is for (see ``PLATFORMS``).
    ``syntax`` is the assembler's: ``motorola``, ``gas``, or ``auto`` to decide
    for each file. ``extern`` is a register list: what a routine that is in
    none of the files may change (the C compiler's ``d0-d1/a0-a1``); without
    it a call of such a routine is an error. ``cpu`` is the processor the
    program is for (``68000`` to ``68060``): an instruction it has not is an
    error, and so is one of the floating point unit unless ``fpu`` says there
    is one. Without ``cpu`` every instruction of the family is taken.
    ``define`` (``NAME`` or ``NAME=value``) and ``undefine`` (``NAME``) say
    how conditional assembly that tests those names goes; a conditional on any
    other name is checked both ways.

    Raises ValueError for a setting that makes no sense.
    """
    names = list(reserved)
    registers = [canonical(name) for name in names]
    if None in registers:
        raise ValueError(f"{names[registers.index(None)]} is not a register")
    if platform not in (None, *PLATFORMS):
        raise ValueError(f"{platform!r} is not a platform: {', '.join(PLATFORMS)}")
    if syntax not in SYNTAXES:
        raise ValueError(f"{syntax!r} is not a syntax: {', '.join(SYNTAXES)}")
    if cpu not in (None, *CPUS):
        raise ValueError(f"{cpu!r} is not a processor: {', '.join(CPUS)}")
    symbols: dict[str, str | None] = dict.fromkeys(undefine)
    for item in define:
        name, _, value = item.partition("=")
        symbols[name] = value or "1"
    changed = None if extern in (None, "-") else parse_list(extern or "")
    if extern not in (None, "-") and changed is None:
        raise ValueError(f"{extern!r} is not a register list")
    return Options(
        frozenset(registers),
        tuple(map(Path, include_dirs)),
        frozenset(chosen(select, ignore, extend_select)),
        infer,
        PLATFORMS[platform] if platform else None,
        syntax,
        None if extern is None else Effect(frozenset(changed or ())),
        cpu,
        fpu,
        symbols,
    )
