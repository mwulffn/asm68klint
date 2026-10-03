"""The ``asm68klint`` command."""

import argparse
import sys
import tomllib
from collections.abc import Sequence
from pathlib import Path

from asm68klint.config import find_config, read_config
from asm68klint.findings import ERROR
from asm68klint.linter import DEFAULT_RESERVED, describe_files, lint_files
from asm68klint.platforms import PLATFORMS
from asm68klint.registers import canonical
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
    parser.add_argument("--rules", action="store_true", help="list the rules and exit")
    options = parser.parse_args(arguments)
    if not options.files and not options.rules:
        parser.error("no source files given")
    return options


def split(text: str | None, default: list[str]) -> list[str]:
    """Split a command line list at its commas; ``-`` is the empty list."""
    if text is None:
        return default
    return [] if text == "-" else text.split(",")


def run(options: argparse.Namespace) -> int:
    """Lint as the options and the configuration file say."""
    settings = read_config(options.config) if options.config else find_config(Path())
    settings = settings or {}
    names = split(options.reserved, settings.get("reserved", list(DEFAULT_RESERVED)))
    reserved = [canonical(name) for name in names]
    if None in reserved:
        raise ValueError(f"{names[reserved.index(None)]} is not a register")
    include_dirs = [*options.include_dir, *map(Path, settings.get("include-dirs", []))]
    infer = options.infer or settings.get("infer", False)
    platform = options.platform or settings.get("platform")
    if platform not in (None, *PLATFORMS):
        raise ValueError(f"{platform!r} is not a platform: {', '.join(PLATFORMS)}")
    if options.effects:
        lines = describe_files(options.files, reserved, include_dirs, infer, platform)
        print(*lines, sep="\n")
        return 0
    findings = lint_files(
        options.files,
        reserved,
        include_dirs,
        split(options.select, settings.get("select", [])),
        split(options.ignore, settings.get("ignore", [])),
        infer,
        platform,
    )
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
