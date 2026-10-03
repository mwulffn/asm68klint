"""Lint every source file under a directory on its own and tally what is found.

For trying the linter on other people's programs:

    uv run python tools/corpus.py DIRECTORY [platform=amiga] [only=S,F002]
        [top=40] [limit=300]

Each file is linted alone, with --infer and no reserved registers. The tally
is by rule, and by message with the names taken out. ``only`` keeps the rules
whose code starts so, ``limit`` takes a random sample of that many files.
What is learnt goes into tests/test_found_in_the_wild.py as a few lines
written for the test; the programs themselves stay out of this repository.
"""

import collections
import random
import re
import sys
import time
import traceback
from pathlib import Path

from asm68klint import lint_files

SUFFIXES = {".s", ".asm", ".68k", ".x68", ".a", ".S"}
INCLUDE_NAMES = ("include", "includes", "inc")


def shape(code: str, message: str) -> str:
    """Return a message with the names in it taken out."""
    if code == "S002":
        return message.split(" (in macro")[0]
    message = re.sub(r"'[^']*'", "'_'", message)
    words = message.split(" ")
    plain = [word if word.islower() and word.isalpha() else "_" for word in words]
    return " ".join(plain)


def main() -> None:
    """Run the tally and print it."""
    root = Path(sys.argv[1])
    extra = dict(argument.split("=", 1) for argument in sys.argv[2:])
    files = [
        path
        for path in sorted(root.rglob("*"))
        if path.suffix in SUFFIXES and path.is_file()
    ]
    if "limit" in extra:
        random.Random(1).shuffle(files)
        files = files[: int(extra["limit"])]
    include_dirs = [
        path
        for path in root.rglob("*")
        if path.is_dir() and path.name.lower() in INCLUDE_NAMES
    ]
    codes: collections.Counter[str] = collections.Counter()
    shapes: collections.Counter[tuple[str, str]] = collections.Counter()
    lines = crashes = 0
    for path in files:
        start = time.time()
        try:
            lines += len(path.read_text(errors="replace").splitlines())
            found = lint_files(
                [path],
                reserved=[],
                include_dirs=[path.parent, root, *include_dirs],
                infer=True,
                platform=extra.get("platform"),
            )
        except Exception:  # noqa: BLE001
            crashes += 1
            print("CRASH", path, traceback.format_exc().splitlines()[-1])
            continue
        if time.time() - start > 5:
            print("SLOW", path, round(time.time() - start))
        for finding in found:
            codes[finding.code] += 1
            shapes[finding.code, shape(finding.code, finding.message)] += 1
    total = sum(codes.values())
    print(f"{len(files)} files, {lines} lines, {total} findings, {crashes} crashes")
    print(dict(codes.most_common()))
    only = tuple(extra.get("only", "").split(","))
    wanted = [item for item in shapes.most_common() if item[0][0].startswith(only)]
    for (code, text), count in wanted[: int(extra.get("top", 40))]:
        print(f"{count:6} {code} {text[:150]}")


if __name__ == "__main__":
    main()
