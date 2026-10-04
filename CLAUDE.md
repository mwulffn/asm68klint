# asm68klint

A linter for hand-written assembly for the Motorola 68000 family, in the
spirit of ruff: rules with codes, a configuration file, fixes. It began
inside a port of an arcade game to the Amiga and became a tool of its own.
`README.md` is the manual: keep it true.

## Why it exists

A routine's header (`In`, `Out`, `Clobbers`) is a contract that the linter
proves against the code. That makes the headers safe to reason from: a
person or a language model writing a new routine reads three lines per
routine it calls, not the bodies, and sees which registers are in use.
Features are judged by that use first.

## Decisions

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

## How it is put together

| File | What |
| --- | --- |
| `source.py`, `gas.py` | one line into a `Statement`: Motorola syntax, the GNU assembler's. `gas.py` turns its forms into Motorola's, so nothing after it knows of two syntaxes |
| `reader.py` | a file with its includes and macros into one list of statements; picks the syntax per file |
| `directives.py` | which directives are data, conditionals, nothing; which conditionals can be decided |
| `m68k.py`, `registers.py` | **everything that depends on the processor**: what each instruction writes and reads, which processor has it, register names and lists |
| `header.py`, `routines.py`, `infer.py` | headers; splitting a file into routines; where routines without headers start |
| `model.py` | what a graph is made of: `Node`, `Call`, `Effect`, the kinds of node and of exit |
| `graph.py`, `tables.py` | a routine's control-flow graph: calls, jumps, jump tables, code borrowed from a routine jumped into |
| `flow.py` | forward over the graph: which registers are changed, what is on the stack |
| `reads.py` | forward: registers read while they hold nothing; backward: which are still needed (free registers) |
| `checks.py` | the checks of one routine against its header |
| `units.py` | the files of a run, their routines and graphs; inference in rounds (`settle`) |
| `linter.py` | what is called from outside: lint, effects, free registers; `lint: ignore` |
| `values.py`, `style.py` | the values of names (`equ`, `rs` fields) for the default build; the style rules (`T`, off unless selected) |
| `format.py` | the formatter: blanks and case only, each line read again before it is kept |
| `platforms.py`, `options.py`, `config.py`, `rules.py`, `findings.py`, `annotations.py`, `fix.py`, `cli.py` | what their names say |

## Rules of the house

- Rule codes are stable. A rule that goes keeps its number unused.
- A rule is added to `rules.py`, to the README's table and gets tests.
- Whatever the linter cannot follow is an error, never a guess. A
  heuristic is fine when it is about how source is *written* (an
  instruction in the first column) and not about what code *does*.
- New read rules start as warnings. Style rules are off by default.
- The formatter is proved by assembling before and after and comparing
  the bytes (a test does it with vasm on a messy sample; ProTracker's
  26,737 lines and the game's source were done by hand). Do that again
  after changing it.
- The odd-field rule (T002) was compared with a checker that asks vasm:
  with a field moved to an odd offset both name the same 52 fields.
- No runtime dependencies. Python 3.11 or later, by `uv`; `uv run pytest`,
  `uv run ruff format .`, `uv run ruff check .`.
- The game's sources (not public) are the regression check for headers:
  26 files, 168 routines, clean under every rule. A change that is meant
  to change nothing gives the same output there, byte for byte.

## What public code showed

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

- More style rules, if wanted: a global label that could be local,
  banner dividers, operating system calls outside one file, data in the
  wrong kind of section.
- The formatter does not touch source for the GNU assembler, and has one
  layout with one setting, the comment column.
- What code called inside a routine reads is not checked against `In`.
- Branches to `*+N` other than the conditional return (`beq.s *+4` over a
  one-word instruction): they need the length of instructions.
- An annotation for what an annotated call reads (`lint: in`), which
  would let `--free` say more before such a call.
- CPU32 and ColdFire if ever wanted; 68020 addressing modes are read for
  their registers but not checked against `--cpu`.
