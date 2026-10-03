"""The settings of a lint run."""

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from asm68klint.flow import Effect
from asm68klint.platforms import PLATFORMS, Platform
from asm68klint.registers import canonical, parse_list
from asm68klint.rules import chosen

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


def make_options(
    reserved: Iterable[str] = DEFAULT_RESERVED,
    include_dirs: Iterable[Path] = (),
    select: Iterable[str] = (),
    ignore: Iterable[str] = (),
    infer: bool = False,
    platform: str | None = None,
    syntax: str = "auto",
    extern: str | None = None,
) -> Options:
    """Check the settings of a lint run and put them together.

    ``reserved`` names the registers that may not be written without a
    ``lint: allow`` annotation. ``include_dirs`` are searched for include files.
    ``select`` and ``ignore`` choose the rules to report, by code or by the
    beginning of one; nothing selected means all of them. With ``infer``, code
    need not have headers: what a routine without one does is worked out.
    ``platform`` names the machine the program is for (see ``PLATFORMS``).
    ``syntax`` is the assembler's: ``motorola``, ``gas``, or ``auto`` to decide
    for each file. ``extern`` is a register list: what a routine that is in
    none of the files may change (the C compiler's ``d0-d1/a0-a1``); without
    it a call of such a routine is an error.

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
    changed = None if extern in (None, "-") else parse_list(extern or "")
    if extern not in (None, "-") and changed is None:
        raise ValueError(f"{extern!r} is not a register list")
    return Options(
        frozenset(registers),
        tuple(map(Path, include_dirs)),
        frozenset(chosen(select, ignore)),
        infer,
        PLATFORMS[platform] if platform else None,
        syntax,
        None if extern is None else Effect(frozenset(changed or ())),
    )
