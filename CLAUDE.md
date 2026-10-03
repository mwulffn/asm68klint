# asm68klint

A linter for hand-written assembly for the Motorola 68000 family, in the
spirit of ruff: rules with codes, a configuration file, fixes. It began as
`asmlint` inside the Galaga port for the Amiga (`../Galaga-port`), where the
build runs the old copy; it was moved here on 2026-10-03 to be a tool of
its own. `README.md` is the manual: keep it true.

## Why it exists

A routine's header (`In`, `Out`, `Clobbers`) is a contract that the linter
proves against the code. That makes the headers safe to reason from: a
person or a language model writing a new routine reads three lines per
routine it calls, not the bodies, and sees which registers are in use.
Features are judged by that use first (the user, 2026-10-03: "the value
[is] that the llm can see what is in use while writing new functions").

## Decisions (the user's, 2026-10-03)

- **MIT licence.**
- **Processors:** 68000 to 68060 and the floating point unit with its
  registers. Not ColdFire, not CPU32.
- **Syntaxes:** the Motorola dialects (vasm, Devpac, AsmOne, PhxAss,
  asm68k) and the GNU assembler's Motorola style. Not its MIT style
  (`movel sp@(8),d0`). 6502 and Z80 are out of scope.
- **Platform flags are wanted** where a platform has rules of its own
  (`--amiga`, `--atari`).
- **Other people's code is not kept.** Public programs are linted to
  learn; what is learnt becomes a test of a few lines written for it
  (`tests/test_found_in_the_wild.py`, `tests/test_gas.py`).
  `tools/corpus.py` is the tally that was used.
- The Galaga port keeps its own `asmlint/` until the user says otherwise.
  This tool is not to change anything there.

## How it is put together

| File | What |
| --- | --- |
| `source.py`, `gas.py` | one line into a `Statement`: Motorola syntax, the GNU assembler's. `gas.py` turns its forms into Motorola's, so nothing after it knows of two syntaxes |
| `reader.py` | a file with its includes and macros into one list of statements; picks the syntax per file |
| `directives.py` | which directives are data, conditionals, nothing; which conditionals can be decided |
| `m68k.py`, `registers.py` | **everything that depends on the processor**: what each instruction writes and reads, which processor has it, register names and lists |
| `header.py`, `routines.py`, `infer.py` | headers; splitting a file into routines; where routines without headers start |
| `graph.py`, `tables.py` | a routine's control-flow graph: calls, jumps, jump tables, code borrowed from a routine jumped into |
| `flow.py` | forward over the graph: which registers are changed, what is on the stack |
| `reads.py` | forward: registers read while they hold nothing; backward: which are still needed (free registers) |
| `linter.py` | the checks, inference in rounds (`settle`), effects, free registers |
| `platforms.py`, `options.py`, `config.py`, `rules.py`, `fix.py`, `cli.py` | what their names say |

## Rules of the house

- Rule codes are stable. A rule that goes keeps its number unused.
- A rule is added to `rules.py`, to the README's table and gets tests.
- Whatever the linter cannot follow is an error, never a guess. A
  heuristic is fine when it is about how source is *written* (an
  instruction in the first column) and not about what code *does*.
- New read rules start as warnings.
- No runtime dependencies. Python by `uv`; `uv run pytest`,
  `uv run ruff format .`, `uv run ruff check .`.
- The Galaga port's sources are the regression check for headers: 26
  files, 168 routines, clean under every rule. From
  `../Galaga-port/game`, after a `make`:
  `uv run --project ../../asm68klint asm68klint -I include -I build --cpu 68000 src/*.s`

## What public code showed (2026-10-03)

About 700,000 lines were linted with `--infer`: an Amiga module player
(188,000 lines, AsmOne style), ProTracker, a demo, an emulator's test
suite, an Atari source collection (a sample of 400 files), EmuTOS and
SGDK (GNU assembler), a transcoded arcade game (GNU assembler), a Sega
disassembly (Macro Assembler AS). No crashes. What is left when a program
is linted:

- **Calls through a register and jumps through tables of addresses**
  (F001): not knowable from the source; they need annotations.
- **Stack tricks** (R006): popping the return address, loading sp,
  routines that never return.
- **Conditional assembly with many switches** checked both ways: needs
  `-D`/`-U`.
- **Missing system include files** and what follows from them.
- **Real findings:** registers read after a library call lost them (a1
  after `DoIO` in ProTracker), interrupt handlers that change registers.

Not read: Macro Assembler AS (the Sega disassemblies' `switch`, `irpc`,
`:=`), the Alcyon assembler's `R0`-`R15`, structured-programming macros
(`REPEAT`/`UNTIL`), ColdFire.

Speed: the game (9,700 lines, headers) 0.4 s; a 65,000-line file without
headers 9 s. Inference keeps each routine's graph and looks again only at
routines whose callees changed.

## Not done yet

- Style rules: `clr` on a write-only hardware register, word fields at
  odd offsets, displacements out of range, missing sizes, unused
  `xdef`/`xref`. They want a rule group that is off by default.
- A formatter (columns, case), checked by assembling before and after
  and comparing the binaries.
- What code called inside a routine reads is not checked against `In`.
- Branches to `*+N`.
- A `lint: out d0` annotation, so that a result of an annotated call is
  known and the rest counts as lost.
- CPU32 and ColdFire if ever wanted; 68020 addressing modes are read for
  their registers but not checked against `--cpu`.
