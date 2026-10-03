# asm68klint

A linter for hand-written Motorola 68000 assembly, in the spirit of ruff:
rules with codes, a configuration file, later fixes and a formatter. It
began as `asmlint` inside the Galaga port for the Amiga
(`../Galaga-port`), where the build runs it; it was moved here on
2026-10-03 to be a tool of its own. `README.md` is the manual.

## Why it exists

A routine's header (`In`, `Out`, `Clobbers`) is a contract that the linter
proves against the code. That makes the headers safe to reason from: a
person or a language model writing a new routine reads three lines per
routine it calls, not the bodies, and sees which registers are in use.
Features are judged by that use first.

## Scope

- Today: the 68000 instruction set, vasm's Motorola syntax.
- **The whole 68k family is wanted in time** (68010, 68020, 68030, 68040,
  68060, CPU32, and the FPU's registers): the user's decision,
  2026-10-03. Keep everything that depends on the CPU in `m68k.py` and
  `registers.py`, so that it becomes a table chosen by a `cpu` setting.
  Do not scatter instruction names or addressing-mode patterns elsewhere
  (`flow.py` and `graph.py` have a few today: the stack idioms and the
  branches).
- Not in scope: 6502, Z80 and other CPUs.

## Plan

1. Rule codes, `--select`/`--ignore`, configuration file. **Done.**
2. Liveness: a table of what each instruction reads, then
   - a register live across a call that clobbers it,
   - a register read before it is written and not under `In`,
   - a value computed and never used.
3. For the model's workflow: a query (which registers are free at
   file:line), a header generator (`--fix` writes `Clobbers` from the
   analysis), an inference mode for code without headers, JSON output.
4. Style rules: `clr` on a write-only hardware register, instructions
   above the chosen CPU, word fields at odd offsets, displacement out of
   range, missing sizes, unused `xdef`/`xref`.
5. A formatter (columns, case), checked by assembling before and after
   and comparing the binaries.

## Rules of the house

- Rule codes are stable. A rule that goes keeps its number unused.
- A rule is added to `rules.py`, to the README's table and gets tests.
- Whatever the linter cannot follow is an error, never a guess.
- No runtime dependencies. Python by `uv`; `uv run pytest`,
  `uv run ruff format .`, `uv run ruff check .`.
- The Galaga port's sources are the real-world check: 26 files, 168
  routines, clean. From `../Galaga-port/game`, after a `make`:
  `uv run --project ../../asm68klint asm68klint -I include -I build src/*.s`
