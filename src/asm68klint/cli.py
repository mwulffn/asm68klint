"""The ``asm68klint`` command."""

import argparse
import sys
import tomllib
from collections.abc import Sequence
from pathlib import Path

from asm68klint.config import find_config, read_config
from asm68klint.findings import ERROR
from asm68klint.linter import describe_files, free_registers, lint_files
from asm68klint.m68k import CPUS
from asm68klint.options import DEFAULT_RESERVED, SYNTAXES
from asm68klint.platforms import PLATFORMS
from asm68klint.registers import format_list
from asm68klint.rules import RULES


def parse_arguments(arguments: Sequence[str] | None) -> argparse.Namespace:
    """Parse the command line."""
    parser = argparse.ArgumentParser(
        prog="asm68klint",
        description=(
            "Check the routine headers of 68000 assembly source (vasm, Motorola"
            " syntax) against what the code does to registers."
        ),
    )
    parser.add_argument("files", nargs="*", type=Path, metavar="FILE")
    parser.add_argument(
        "-I",
        "--include-dir",
        action="append",
        default=[],
        type=Path,
        metavar="DIR",
        help="directory to search for include files (may be repeated)",
    )
    parser.add_argument(
        "--reserved",
        metavar="REGISTERS",
        help=(
            "registers that may not be written without a lint: allow annotation,"
            f" separated by commas; - for none (default: {','.join(DEFAULT_RESERVED)})"
        ),
    )
    parser.add_argument(
        "--select",
        metavar="RULES",
        help="rules to report, by code or its beginning: H,R001 (default: all)",
    )
    parser.add_argument(
        "--ignore", metavar="RULES", help="rules not to report, given the same way"
    )
    parser.add_argument(
        "--config",
        type=Path,
        metavar="FILE",
        help=(
            "configuration file (default: the nearest asm68klint.toml, or"
            " pyproject.toml with a [tool.asm68klint] table)"
        ),
    )
    parser.add_argument(
        "--infer",
        action="store_true",
        help=(
            "code need not have routine headers: what a routine without one"
            " reads and changes is worked out from its code"
        ),
    )
    parser.add_argument(
        "--syntax",
        choices=SYNTAXES,
        help=(
            "the assembler's syntax: motorola, gas (the GNU assembler's Motorola"
            " style), or auto to decide for each file (the default)"
        ),
    )
    parser.add_argument(
        "-D",
        "--define",
        action="append",
        default=[],
        metavar="NAME[=VALUE]",
        help="a name that is defined: conditional assembly that tests it goes one way",
    )
    parser.add_argument(
        "-U",
        "--undefine",
        action="append",
        default=[],
        metavar="NAME",
        help="a name that is not defined",
    )
    parser.add_argument(
        "--cpu",
        choices=CPUS,
        help=(
            "the processor the program is for: an instruction it has not is an"
            " error (default: any of the family)"
        ),
    )
    parser.add_argument(
        "--fpu",
        action="store_true",
        help="with --cpu: there is a floating point unit (a 68881 or 68882)",
    )
    parser.add_argument(
        "--extern",
        metavar="REGISTERS",
        help=(
            "what a routine that is in none of the files may change, such as"
            " d0-d1/a0-a1 for code from a C compiler; without it a call of one"
            " is an error"
        ),
    )
    parser.add_argument(
        "--platform",
        choices=sorted(PLATFORMS),
        help="the machine the program is for: what its system calls change",
    )
    for name, platform in PLATFORMS.items():
        parser.add_argument(
            f"--{name}",
            dest="platform",
            action="store_const",
            const=name,
            help=f"the same as --platform {name}: {platform.summary}",
        )
    parser.add_argument(
        "--effects",
        action="store_true",
        help="list what every routine reads and changes, and do not lint",
    )
    parser.add_argument(
        "--free",
        metavar="FILE:LINE",
        help="say which registers new code at that line may use, and do not lint",
    )
    parser.add_argument("--rules", action="store_true", help="list the rules and exit")
    options = parser.parse_args(arguments)
    if not options.files and not options.rules:
        parser.error("no source files given")
    return options


def split(text: str) -> list[str]:
    """Split a command line list at its commas; ``-`` is the empty list."""
    return [] if text == "-" else text.split(",")


def gather(options: argparse.Namespace) -> dict:
    """Put the settings of the configuration file and the command line together."""
    settings = read_config(options.config) if options.config else find_config(Path())
    settings = {key.replace("-", "_"): value for key, value in (settings or {}).items()}
    for name in ("reserved", "select", "ignore"):
        if getattr(options, name) is not None:
            settings[name] = split(getattr(options, name))
    for name in ("platform", "syntax", "extern", "cpu"):
        if getattr(options, name) is not None:
            settings[name] = getattr(options, name)
    for name in ("define", "undefine"):
        settings[name] = [*settings.get(name, []), *getattr(options, name)]
    settings["infer"] = options.infer or settings.get("infer", False)
    settings["fpu"] = options.fpu or settings.get("fpu", False)
    settings["include_dirs"] = [*options.include_dir, *settings.get("include_dirs", [])]
    return settings


def run(options: argparse.Namespace) -> int:
    """Lint as the options and the configuration file say."""
    settings = gather(options)
    for name in ("select", "ignore"):
        settings.pop(name, None) if options.effects or options.free else None
    if options.effects:
        print(*describe_files(options.files, **settings), sep="\n")
        return 0
    if options.free:
        file, _, line = options.free.rpartition(":")
        if not line.isdigit():
            raise ValueError(f"{options.free!r} is not FILE:LINE")
        found = free_registers(options.files, Path(file), int(line), **settings)
        if found is None:
            raise ValueError(f"{options.free} is in no routine of the files given")
        title, free, busy = found
        print(
            f"{options.free}: in {title}: free {format_list(free)};"
            f" in use {format_list(busy)}"
        )
        return 0
    findings = lint_files(options.files, **settings)
    for finding in findings:
        print(finding)
    return 1 if any(finding.severity == ERROR for finding in findings) else 0


def main(arguments: Sequence[str] | None = None) -> int:
    """Run the linter; return 1 if it found errors and 2 if it could not run."""
    options = parse_arguments(arguments)
    if options.rules:
        for rule in RULES.values():
            print(f"{rule.code}  {rule.severity:7}  {rule.name}: {rule.summary}")
        return 0
    try:
        return run(options)
    except (OSError, ValueError, tomllib.TOMLDecodeError) as error:
        print(f"asm68klint: {error}", file=sys.stderr)
        return 2
