# asm68klint

A linter for hand-written assembly for the Motorola 68000 family. It follows
what every routine does to the registers and the stack, and checks that
against the routine's header comment: what it takes, what it gives back,
what it destroys. So a header can be trusted, by a person or by a language
model writing the next routine: three lines say which registers are in use,
and the build breaks when they stop being true.

It also works on code that has no such headers: it works out what each
routine reads and changes, lists that, answers which registers are free at a
line, and writes the headers.

Plain Python with no runtime dependencies. It reads the source directly, so
it needs neither an assembler nor a build.

```
uv run asm68klint src/*.s                       # check headers against code
uv run asm68klint --infer --effects src/*.s     # what does each routine do?
uv run asm68klint --free src/player.s:412 src/*.s   # what may new code use here?
uv run asm68klint --infer --fix src/*.s         # write the headers
uv run asm68klint --format src/*.s include/*.i  # lay the source out in columns
```

What it reads:

- **Processors:** 68000, 68010, 68020, 68030, 68040, 68060 and the floating
  point unit, with its registers fp0 to fp7.
- **Syntaxes:** Motorola syntax as vasm, Devpac, AsmOne, PhxAss and asm68k
  write it, and the GNU assembler's Motorola style (`move.l 8(%sp),%d0`,
  with or without the percent signs), including what Atari's MadMac has in
  common with it.
- **Platforms:** the Amiga's library calls and the Atari's system traps are
  known, with `--amiga` and `--atari`.

## The header

```
;--
; RoutineName
; In:       d0 = ..., a0 = ...
; Out:      d0 = ...
; Clobbers: d1, a1
RoutineName:
```

- The header is a line holding only `;--`, followed by comment lines: the
  routine name, then the fields `In:`, `Out:` and `Clobbers:`. All three
  fields are required; `-` means empty. (In source for the GNU assembler,
  where `;` is no comment, the lines start with `|`.)
- `In` and `Out` are free text. A register counts as named there when it
  is followed by `=` (`d0.w = count`, `a0/a1 = pointers`) or stands alone
  between commas. Anything else is prose, so `Out: Z = found` is fine.
  Condition codes are not tracked.
- `Clobbers` is `-` or register lists separated by commas: `d1, a1` or
  `d0-d2/a0/fp0-fp1`. `sp` means `a7`.
- A field may continue on the next comment line if that line is indented
  by two or more spaces. Other comment lines in the header are ignored, so
  a description may follow the fields.
- The next label after the header must be the name the header gives.
- A routine runs from its header to the next header or the end of the
  file. Local labels (`.name`, `name$`, `@name`) belong to the global label
  before them.

## What it checks

Errors:

- A routine without a header (code before the first header of a file, or
  a global label that nothing in its routine reaches), a header without a
  name or without one of the three fields, an empty field, a `Clobbers`
  field that cannot be parsed, and a header whose name is not the label
  that follows. (Not with `--infer`: see Code without headers.)
- A register that the routine may change and that is not listed under
  `Out` or `Clobbers`. This includes registers named under `In`. "May
  change" means that on some path through the routine the register does
  not hold its entry value when the routine returns.
- Registers changed by the routines it calls. A `bsr`, `jsr`, `bra` or
  `jmp` to another routine inherits everything that routine lists under
  `Out` and `Clobbers`. So does falling through into the next routine.
  Routines are looked up in the same source file first (with its include
  files), then among the other files given on the command line, where
  routines exported with `xdef` come before plain global labels.
- Calls that cannot be followed and are not annotated: a call to a label
  that is not a routine, an indirect `jsr (a0)` or `jmp (a0)`, a `trap`.
- A stack that is not balanced when the routine returns, unless `a7` is
  listed under `Out` or `Clobbers`.
- A write to a reserved register (by default a5 and a6) without an
  annotation that allows it, even if the register is saved and restored.
  Calling a routine that changes a reserved register counts as a write.
- In an interrupt handler (a routine that ends in `rte`): any register not
  restored and not under `Out` (a trap handler may give a result), and a
  `Clobbers` field that is not `-`.
- Code the linter cannot follow: an unknown instruction or macro, a branch
  to a label that does not exist, execution running into data or off the
  end of the file, a `movem` whose register list it cannot read.
- With `--cpu`: an instruction the chosen processor does not have.

Warnings:

- A register listed under `Out` or `Clobbers` that the routine never
  changes (a stale header).
- A register read while it holds nothing of use: after a call to a routine
  that lists it under `Clobbers`, before anything has been written to it
  again (R007). This is the conflict a header is there to prevent: the
  caller kept a value in a register the routine it calls uses for itself.
  Passing such a register to another call, or returning it as an `Out`
  register, counts as reading it. A register saved on the stack around
  the call is fine. Only the first read is reported.
- A register read before the routine has written it that is not listed
  under `In` (R008). What a call needs (the `In` of the routine called)
  is needed here too. Pushing a register to save it is not a read, and
  reserved registers always hold something.
- An `Out` register that some path to a return never sets (R009). An
  `Out` register that is only a result some of the time is listed under
  `Clobbers` as well, and is then not checked:
  `Out: Z = found, and then d0 = it` with `Clobbers: d0`.
- A register listed under `In` that the routine never reads (R010).
- Unreachable code, which is not checked.

A register that the routine saves whole and restores counts as preserved:
`movem.l d2-d3/a2,-(sp)` with `movem.l (sp)+,d2-d3/a2`, `move.l d2,-(sp)`
with `move.l (sp)+,d2`, `fmovem.x fp2-fp3,-(sp)` with its opposite.
`link`/`unlk` preserve the frame pointer. The linter follows every path
through the routine, so each exit may restore for itself, and an exit that
skips the restore is found.

More of what is followed:

- **Code called inside the routine.** `bsr .Sub` to a local label is a
  call of code in the same routine: what that code changes counts at the
  call, and the code is checked itself.
- **Jump tables.** `jmp Table(pc,d0.w)` goes where the table says, when
  the table is a list of offsets (`Table: dc.w First-Table,Second-Table`)
  or a row of branches (`Table: bra.w First` and so on).
- **Nops written as data** (`dcb.w 96,$4e71`) are code that does nothing.
- **A conditional return** written `beq.s *+4` / `rts`.

The instruction table knows which operand each instruction writes and
which it reads, including the implicit ones: the counter of `dbcc`, the
address register of `(an)+` and `-(an)` in any operand, both operands of
`exg`, the registers of `movem`, both halves of `divul.l d0,d1:d2`, a bit
field's register. Writes to memory are not register writes.

## Annotations

An annotation is a comment of the form `; lint: keyword arguments`.

| Annotation | Meaning |
| --- | --- |
| `; lint: clobbers d0-d1/a0` | The call or jump on this line changes exactly these registers (`-` for none). For calls the linter cannot follow: indirect calls and jumps, unknown routines, traps. |
| `; lint: targets Foo, Bar, .Case` | The indirect call or jump on this line goes to one of these labels. Routines are inherited from; labels inside the routine are followed. |
| `; lint: allow a5, a6` | These reserved registers may be written. |
| `; lint: inline` | The routine called takes the data that follows the call (a text, say) and returns after it. |
| `; lint: out d0` | With `clobbers` on the same instruction: these registers hold a result of the call; the other registers `clobbers` names hold nothing of use after it, so that reading one is found (R007). |
| `; lint: noreturn` | Execution does not come back from here: a jump into another program, a return into another task. Nothing is checked at this exit, and the jump need not be one the linter can follow. On a conditional branch it is said of the branch taken. |
| `; lint: ignore R004, R006` | These rules are not reported here. A rule is named by its code or the beginning of one. |

Where an annotation applies:

- At the end of an instruction line, it applies to that instruction.
- On a comment line of its own, it applies to the next instruction.
- On (or before) a macro call, it applies to every instruction of that
  macro's expansion. Annotations may also be written inside a macro.
- An instruction takes one annotation of a kind; a second annotation for
  it goes on a comment line of its own before it.
- In a routine header `allow`, `ignore` and `noreturn` are permitted, and
  apply to the whole routine. `allow` is meant for startup code and
  interrupt handlers. `noreturn` in a header says the routine never
  returns: its exits are not checked, a `bsr` to it ends the path in the
  caller, and so does a jump to it.

```
;--
; VBlank
; In:       -
; Out:      -
; Clobbers: -
; lint: allow a5-a6
VBlank:
	movem.l	d0/a5-a6,-(sp)
	lea	State,a5
	lea	$dff000,a6
	...
	movem.l	(sp)+,d0/a5-a6
	rte

	move.l	4.w,a6			; lint: allow a6
	jsr	_LVOForbid(a6)		; lint: clobbers d0-d1/a0-a1

	jmp	(a0)			; lint: targets .Idle, .Dive, .Return
```

An allowed write to a reserved register still follows the normal rules:
the register must be restored or be listed under `Out` or `Clobbers`.

After a call with a `lint: clobbers` annotation the registers it names
count as changed, not as lost: the linter does not know which of them is
a result, unless `lint: out` says.

```
	; lint: out d0
	jsr	(a2)			; lint: clobbers d0-d1/a0-a1

	movem.l	(sp)+,d0-d7/a0-a6
	rte				; lint: noreturn
```

## Code without headers

With `--infer` a routine need not have a header. The linter works out
where the routines are and what each one reads and changes, and uses that
wherever such a routine is called. Routines with headers are checked as
always; the two may be mixed, which is how a code base is brought over a
routine at a time.

- **Where routines start.** A global label starts a routine when it is
  called, exported, jumped to from another file, or reached by nothing the
  source shows (an entry of a table of addresses, an interrupt handler).
  Every other global label is a place inside the routine it is in.
- **Jumps into another routine.** Code that several routines end in
  (`Done: movem.l (sp)+,d2-d3` / `rts`) is followed from each jump to it,
  with the stack as it is there.
- **What a routine does** is what its code changes at its returns, and
  what it reads before writing. Which of the registers it changes are
  results is not known, so none of them counts as lost after a call.
- **Code reached only through its address.** Code with a label that
  nothing in its routine reaches (`lea .handler(pc),a0` somewhere), and a
  row of jumps (`jmp .init(pc)` / `jmp .play(pc)`: a table of entries),
  is checked as an entry of its own. What it changes is not counted as
  the routine's.
- A routine that leaves the stack unbalanced is reported itself; its
  callers are checked as if it did not.

Three things can be had from that, with or without headers:

```
uv run asm68klint --infer --effects FILE...
src/player.s:120: Shot: In d6/a5; Out d7; Clobbers d0-d5/a0-a1
src/old.s:33: DrawAll: In a0; changes d0-d2/a0-a1 (no header)
```

```
uv run asm68klint --free src/player.s:412 FILE...
src/player.s:412: in Shot: free d1-d3/a0-a1; in use d0/d4-d7/a2-a7
```

A register is free at a line when nothing later needs what is in it: no
instruction reads it before writing it, no call takes it, it is not a
result, and the routine may change it (it is under `Clobbers`, or saved on
the stack at that point). New code there may use it without saving it.
Reserved registers and the stack pointer are never free. Before a call
whose inputs are not known (an annotated or indirect one) every register
counts as in use.

```
uv run asm68klint --infer --fix FILE...
```

rewrites the `Clobbers` field of every header that does not match its code,
and gives each routine without a header one: what it reads under `In`,
everything it changes under `Clobbers`, for a person to move the results to
`Out`. A routine whose code could not be followed is left alone.

`--json` prints findings, effects or free registers as JSON.

## Style rules

The `T` rules are about how the source is written. They are off unless
selected: `--extend-select T` adds them to the usual rules, `--select T`
runs them alone.

- **T001** An instruction reads a hardware register that can only be
  written. With `--amiga`: the custom chips' registers by Commodore's
  names (`bltcon0(a6)`, `_custom+dmacon`) or by address (`$dff096`). The strobes (`copjmp1`) are not among them: a read is how they are set off.
  `clr` is the one that is easy to miss: on a 68000 it reads before it
  writes, and reading such a register puts whatever is on the bus in it.
  Write `move.w #0,bltcon1(a6)`. (Not reported for `clr` with `--cpu`
  68010 or later, where it does not read.)
- **T002** A word or long field laid out with `rs` is at an odd offset:
  `rs.w` after an odd number of `rs.b`. vasm does not align it, and a
  68000 traps on the access.
- **T003** An instruction that takes a size has none: `move d0,d1`.
  (`moveq`, `lea`, `Scc`, the bit operations, multiplication and division
  need none.)
- **T004** A branch has a size (`bne.s`): the assembler picks the
  shortest.
- **T005** A displacement with an index register, `Name(a5,d0.w)`, is
  outside -128 to 127. (Not with `--cpu` 68020 or later.)
- **T006** A name is imported with `xref` and not used.
- **T007** A name is exported with `xdef` and no other file given uses
  it.

T002 and T005 need the values of names. Those are worked out from the
source's own `equ`, `=`, `set` and `rs`, for the build that is told
nothing on the assembler's command line but what the linter is given
with `-D` and `-U`: `ifnd SIZE` / `SIZE equ 4` / `endc` gives SIZE the
value 4 unless `-D SIZE=8` says otherwise. A value that depends on
anything else (a conditional that cannot be decided, a name from
outside) is not known, and nothing is reported from it.

## The formatter

```
uv run asm68klint --format FILE...            # rewrite the files
uv run asm68klint --format --check FILE...    # exit 1 if any would change
uv run asm68klint --format --diff FILE...     # show the changes
```

lays source in Motorola syntax out in columns:

```
Label:	move.w	d0,d1				; comment
NAME		equ	4
field		rs.w	1
```

- The label in the first column; a tab; the instruction; a tab; the
  operands. `equ`, `=` and `rs` go in column 16, so that names and values
  line up.
- Comments start in column 48 (`--comment-column N`, or `comment-column`
  in the configuration file). Lines that follow each other have their
  comments in one column: further right if one of them needs it, up to
  two tab stops; a line longer still has its comment after one tab. The
  comment of a label that stands alone stays next to it.
- Mnemonics, directives and register names are written in lower case.
  Macro names, labels and everything else in the operands are not
  touched.
- Blank lines, comment lines and what is between `rem` and `erem` stay as
  they are, less blanks at the end.

Only blanks and case are changed. Each line is read again after it is
laid out, and kept as it was unless label, instruction, operands and
comment are the same as before. Tried by assembling before and after:
ProTracker's source (26,737 lines, of which 21,814 changed) and the
Galaga port's give the same bytes. Source for the GNU assembler is passed
over.

## Running it

```
uv run asm68klint [options] FILE...
uv run asm68klint --rules
```

Give all the source files of the program in one run, so that calls across
files can be followed. Include files are read through the `include`
directives and should not be listed.

| Option | Meaning |
| --- | --- |
| `-I DIR` | Adds a directory to search for include files. A file is also looked for next to the file that includes it, next to the file given on the command line, in the current directory, and in directories named by `incdir`. Names are matched whatever their case. |
| `--reserved a4,a5` | The reserved registers; `-` means none. Default `a5,a6`. |
| `--select H,R001`, `--ignore R002` | The rules to report, by code or by the beginning of one. |
| `--extend-select T` | Rules to report as well as the usual ones: the style rules. |
| `--format`, `--check`, `--diff`, `--comment-column N` | See The formatter. |
| `--infer` | Code need not have headers. |
| `--effects`, `--free FILE:LINE`, `--fix`, `--json` | See Code without headers. |
| `--platform amiga`, `--amiga`, `--atari` | See Platforms. |
| `--syntax auto`, `motorola`, `gas` | See Syntaxes. Default `auto`. |
| `--cpu 68000` ... `68060`, `--fpu` | See Processors. |
| `--extern d0-d1/a0-a1` | What a routine that is in none of the files may change, for a program that calls code from a C compiler. Without it such a call is an error. |
| `-D NAME[=VALUE]`, `-U NAME` | Names that are, or are not, defined: conditional assembly that tests them goes one way. |
| `--config FILE` | See Configuration file. |

Output is one line per finding, `file:line: severity: code message`,
sorted by file and line. A finding in code that comes from a macro is
reported at the line that uses the macro and names the macro. The exit
status is 0 when there are no errors (warnings do not count), 1 when there
are errors and 2 when the linter could not run.

From Python: `asm68klint.lint_files(paths, reserved=..., include_dirs=...,
infer=..., ...)` returns the findings; `describe_files`, and in
`asm68klint.linter` `routine_effects` and `free_registers`, give the rest.
The settings are those of `asm68klint.options.make_options`.

### Configuration file

The same settings can be kept in `asm68klint.toml`, or in the
`[tool.asm68klint]` table of a `pyproject.toml`. The nearest one in the
current directory or above it is read; `--config FILE` names another.

```
reserved = ["a5", "a6"]
include-dirs = ["include", "build"]
select = ["H", "R"]
extend-select = ["T"]
ignore = ["R002"]
comment-column = 48
define = ["DEBUG=0"]
undefine = ["PROFILE"]
infer = false
platform = "amiga"
syntax = "motorola"
cpu = "68000"
fpu = false
extern = "d0-d1/a0-a1"
```

Directories are relative to the file. An option on the command line
replaces the file's setting (`-` for an empty list), except `-I`, `-D` and
`-U`, which add to it.

### Rules

| Code | Name | Reports |
| --- | --- | --- |
| H001 | missing-header | code that no routine header covers |
| H002 | header-name | a header without a routine name |
| H003 | header-field | a header without one of its three fields |
| H004 | header-empty-field | a field with nothing in it |
| H005 | header-clobbers | a `Clobbers` field that is not a register list |
| H006 | header-label | a header not followed by the label it names |
| R001 | undeclared-change | a register changed and not under `Out` or `Clobbers` |
| R002 | stale-header | a register declared and never changed (warning) |
| R003 | reserved-write | a write to a reserved register |
| R004 | interrupt-preserve | an interrupt handler that changes a register |
| R005 | interrupt-clobbers | an interrupt handler whose `Clobbers` is not `-` |
| R006 | unbalanced-stack | a stack that is not as it was found at a return |
| R007 | clobbered-read | a register read after a call clobbered it (warning) |
| R008 | undeclared-input | a register read and not under `In` (warning) |
| R009 | unset-output | an `Out` register some path never sets (warning) |
| R010 | unused-input | an `In` register that is never read (warning) |
| F001 | unknown-call | a call or jump that cannot be followed |
| F002 | missing-label | a branch to a label that is not there |
| F003 | runs-into-data | execution runs into data |
| F004 | runs-off-end | execution runs off the end of the file |
| F005 | unreachable | code that nothing reaches (warning) |
| S001 | include-not-found | an include file that is missing |
| S002 | unknown-instruction | an unknown instruction, directive or macro |
| S003 | macro | a macro that cannot be read or expanded |
| S004 | register-list | a `movem` whose register list cannot be read |
| S005 | annotation | a lint annotation that is wrong or misplaced |
| S006 | processor | an instruction the chosen processor does not have |
| T001 | write-only-read | a hardware register that can only be written is read (off) |
| T002 | odd-field | a word or long field at an odd offset (off) |
| T003 | missing-size | an instruction without a size (warning, off) |
| T004 | sized-branch | a branch with a size (warning, off) |
| T005 | index-displacement | `d8(an,xn)` with a displacement out of range (off) |
| T006 | unused-xref | a name imported and not used (warning, off) |
| T007 | unused-xdef | a name exported that no other file uses (warning, off) |

Ignoring an F or S rule hides the message, not the gap: what the linter
could not follow is still not checked.

## Platforms

| Platform | What is known |
| --- | --- |
| `--amiga` | A call through a library base in a6 (`jsr _LVOOpen(a6)`, `jsr -30(a6)`) changes d0, d1, a0 and a1; d0 is a result, the other three are lost. `Forbid`, `Permit`, `Disable`, `Enable` and `WaitBlit` keep every register. The macros of `exec/types.i` (`STRUCTURE`, `APTR`, `LABEL`, `BITDEF`, ...) emit nothing, so that source can be checked without the system's include files. |
| `--atari` | `trap #1`, `#13` and `#14` (GEMDOS, BIOS, XBIOS) change d0 to d2 and a0 to a2; d0 is a result, the others are lost. |

With a platform the read rules find the classic mistake: `a1` used after
`jsr _LVODoIO(a6)`.

## Syntaxes

`--syntax auto` decides for each file by what is in it.

**Motorola** (vasm, Devpac, AsmOne, PhxAss, asm68k): labels in the first
column or with a colon; `;` comments, and `*` in the first column or
after a label; local labels `.name`, `.1`, `name$`, `1$`, `@name`, and
`Global\.local` for another routine's; `movel` for `move.l`; an
instruction in the first column where it cannot be a label; labels that
differ from their use only in case.

**GNU assembler, Motorola style** (`gas`): `%d0` or `d0`; `|`, `//`,
`/* */` and `;` comments; `label:`; numbered labels `1:` with `1b` and
`1f`; `.L` labels, which unlike Motorola's local labels are found from
anywhere in the file; `jbsr`, `jra`, `jeq` and the other `j` branches;
`movm`; directives with a dot (`.long`, `.globl`, `.macro` with named
arguments, `.ifdef`); `name = value`; the C preprocessor's `#include`,
`#if`, `#ifdef`, `#else`, `#endif` (a conditional like any other) and
`#define` (passed over: a macro of the preprocessor used as an instruction
is an unknown instruction). Its other style, `movel sp@(8),d0`, is not
read.

## Processors

Without `--cpu` every instruction of the family is taken. With it, an
instruction the processor does not have is an error (S006); floating
point instructions need `--fpu`, or a 68040 or 68060. ColdFire and CPU32
are not known.

The floating point registers fp0 to fp7 are followed like the others:
they are listed in headers, saved with `fmovem.x` or `fmove.x`, and the
frame `fsave` puts on the stack comes off with `frestore`.

## Macros: source parsing, not listing files

A routine that uses a macro which writes d0 clobbers d0, so the linter has
to see through macros. There were two ways to do that: lint an assembler's
listing file, in which the macros are expanded, or parse the macro
definitions and expand them in the linter. The linter parses the source.

A listing has one real advantage: the assembler does the expansion, so it
cannot differ from what is assembled. It loses on everything else:

- A listing shows one build configuration. Lines in a conditional branch
  that was not assembled appear without code, so a header could be wrong
  in the debug build and pass in the release build. Parsing the source
  checks every branch.
- It needs a successful assembly first, with the right options for each
  file, so it cannot run on code that does not build yet or in an editor.
- The listing format is not a documented interface and differs between
  assemblers. Source positions have to be reconstructed from it.
- The tests would need the assembler, or listings pasted in as fixtures.

The cost is that the linter has its own small macro processor. It
supports `NAME macro` and `macro NAME`, `endm`, the parameters `\1` to
`\9`, `\0` (the size given on the call, `w` by default), `\#` (the number
of arguments), `\@` (a unique label suffix), arguments in `<...>`, named
arguments (`name: macro first,second=default` with `\first`, and the GNU
assembler's `.macro name first, second`), and macros that use other
macros. Anything else is reported as an error rather than guessed at. When
vasm is installed, a test assembles a sample and compares vasm's listing
with the linter's expansion, instruction by instruction.

What follows from parsing the source:

- **Include files** are read where they are included, so their macros,
  register aliases and routines are seen. A file is read once per source
  file, even if it is included again. A missing include file is an error.
- **Conditional assembly** is not evaluated in general. The linter checks
  a routine once for every combination of outcomes of the conditions in
  it, so the header must hold in every configuration. Conditionals that
  test the same thing (`ifd DEBUG` twice, or `ifd DEBUG` and
  `ifnd DEBUG`, or `if X` and `ifeq X`) are taken consistently. A routine
  with more than eight different conditions is checked once, with every
  conditional going both ways. Three kinds are decided: one that compares
  texts or plain numbers (`ifc "\1","all"` in a macro, once the argument
  is filled in), and one that tests a name given with `-D` or `-U`.
- `equr` and `reg` aliases are resolved. The body of `rept` is checked
  once. `rem`/`erem` blocks are skipped and `end` ends a file.

## Known limitations

Things that are reported although the code may be correct:

- Every configuration of conditional assembly is checked, including
  combinations that are never built. Two conditions that are written
  differently are treated as independent, even if they always agree. Code
  with many build switches (a replay routine with a dozen of them) needs
  `-D` and `-U` to say which build is meant.
- Only saves through the stack count. A register saved in another
  register or in memory and restored later must be listed as clobbered.
  A save of part of a register does not preserve it.
- The stack model is simple. Each path must leave the stack as it found
  it; pushing in a loop, popping the return address, loading the stack
  pointer, or returning through an address pushed with `pea` is reported
  as an unbalanced stack. Space reserved with a named size
  (`lea -FRAME(sp),sp`) must be released with exactly the same expression.
- A register listed under `Out` that the routine passes through unchanged
  gets the stale-header warning.
- A write of any size makes a register good again for the read rules:
  `move.b` into a register whose upper bytes a call clobbered, followed by
  a word read, is not noticed. The other way round, a path that cannot
  happen (a call skipped only when the register is not needed later) is
  reported.
- A routine with a second entry point can only be called through that
  entry if the entry has a header of its own (or with `--infer`).
- Unreachable code is only warned about, never analysed. Code reached
  through an address in a register needs a `targets` annotation.
- A branch to `*+4` is followed only as the conditional return it
  usually is: a short branch (`beq.s *+4`) over an instruction that is
  certain to be one word (`rts`). Any other branch to `*+N` is not.
- Recursive macros, macro features other than those listed above, `iif`,
  ColdFire instructions, and the directives of Macro Assembler AS
  (`switch`, `irpc`, `:=`) are reported as errors.

Things that are not noticed:

- At a call, the linter trusts the header of the routine called. That
  routine's own check is what verifies it, so a wrong header is reported
  there, not at the call.
- Condition codes, and whether `Out` flags are really set.
- What code called inside a routine (`bsr .Sub`) reads: it is not checked
  against `In`.
- A saved register overwritten on the stack through another address
  register (`move.l sp,a0` ... `move.l d0,(a0)`). Writes through `sp`
  itself are seen.
- Exceptions: `chk`, `trapv`, `trapcc` and a division by zero are not
  followed.
- A symbol redefined between two conditionals that test it, and a macro
  defined more than once (the last definition before a use is the one
  expanded, as in vasm, but both branches of a conditional define it).
- `mexit` is ignored: the rest of the macro is treated as if it ran.
- A label defined twice in one routine (in two branches of a conditional)
  resolves to the last definition.
- A macro with the name of a directive is expanded by the linter, while
  the assembler runs the directive.
- In source for the GNU assembler, `;` is taken as a comment, though that
  assembler takes it as the end of a statement: a second statement on the
  line is not seen.

Other notes:

- In Motorola syntax the operand field ends at the first blank, as in
  vasm without `-spaces`; a blank straight after a comma is tolerated
  (vasm itself does not take it). In the GNU assembler's it is the rest
  of the line.

## Development

```
uv sync
uv run pytest
uv run ruff format .
uv run ruff check .
```

The tests hold what linting other people's programs taught: Amiga and
Atari programs, demos, an operating system, a game library, in four
assemblers' syntaxes. None of their code is here; each thing learnt is a
few lines written for the test.

MIT licence.
