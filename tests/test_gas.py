"""Source for the GNU assembler in its Motorola style."""

from pathlib import Path

import pytest

from asm68klint.gas import (
    Numbered,
    looks_like_gas,
    parse_statement,
    strip_block_comments,
)
from asm68klint.linter import describe_files

GAS = {"infer": True, "reserved": [], "syntax": "gas"}


@pytest.mark.parametrize(
    ("line", "label", "mnemonic", "size", "operands"),
    [
        ("  move.l 8(%sp), %d0  | the length", None, "move", "l", ("8(sp)", "d0")),
        ("_start:", "_start", None, None, ()),
        ("loop: dbra %d0,loop // again", "loop", "dbra", None, ("d0", "loop")),
        ("\tjbsr\t_main", None, "bsr", None, ("_main",)),
        ("\tjra\tdone", None, "bra", None, ("done",)),
        ("\tjeq\tdone", None, "beq", None, ("done",)),
        ("\tjbne\tdone", None, "bne", None, ("done",)),
        ("\tjmp\t(%a0)", None, "jmp", None, ("(a0)",)),
        (
            "\tmovm.l\t%d2-%d7/%a2-%a6,-(%sp)",
            None,
            "movem",
            "l",
            ("d2-d7/a2-a6", "-(sp)"),
        ),
        ("\tlink\t%fp,#-8", None, "link", None, ("a6", "#-8")),
        ("\tfmove.x\t%fp0,%fp1", None, "fmove", "x", ("fp0", "fp1")),
        ("\t.long\t1, 2, 3", None, "dc", None, ("1", "2", "3")),
        ('\t.ascii\t"a, b | c"', None, "dc", None, ('"a, b | c"',)),
        ("\t.space\t16", None, "ds", None, ("16",)),
        ("\t.globl\t_main", None, "xdef", None, ("_main",)),
        ("\t.text", None, ".text", None, ()),
        ("\t.ifdef\tDEBUG", None, "ifd", None, ("DEBUG",)),
        ("\t.endif", None, "endc", None, ()),
        ("\t.equ\tSIZE, 12", "SIZE", "equ", None, ("12",)),
        ("SIZE = 12", "SIZE", "=", None, ("12",)),
        ("Size:\t.EQU\t12", "Size", "equ", None, ("12",)),
        ('#include "defs.h"', None, "include", None, ("defs.h",)),
        ("#ifdef __mcoldfire__", None, "ifd", None, ("__mcoldfire__",)),
        ("#if CONF_WITH_FPU && !DEBUG", None, "if", None, ("CONF_WITH_FPU && !DEBUG",)),
        ("#else", None, "else", None, ()),
        ("#endif", None, "endc", None, ()),
        ("#define SIZE 12", None, None, None, ()),
        ("* a comment in the first column", None, None, None, ()),
        (
            "\t.macro\tSAVE first, second=0",
            "SAVE",
            "macro",
            None,
            ("first", "second=0"),
        ),
        (".Ldone:\trts", "..Ldone", "rts", None, ()),
        ("\tjne\t.Ldone", None, "bne", None, ("..Ldone",)),
        ("\tlea\t.Ltable(%pc),%a0", None, "lea", None, ("..Ltable(pc)", "a0")),
    ],
)
def test_a_line_is_read_into_the_usual_form(line, label, mnemonic, size, operands):
    statement = parse_statement("x.s", 1, line)
    found = (statement.label, statement.mnemonic, statement.size, statement.operands)
    assert found == (label, mnemonic, size, operands)


def test_numbered_labels_are_found_before_and_after():
    numbered = Numbered()
    lines = ["1:\tsubq.l #1,d0", "\tjne 1b", "\tjeq 1f", "1:\trts", "\tjra 1b"]
    statements = [parse_statement("x.s", 1, line, numbered) for line in lines]
    assert [statement.label for statement in statements] == [
        "..n1_1",
        None,
        None,
        "..n1_2",
        None,
    ]
    assert statements[1].operands == ("..n1_1",)
    assert statements[2].operands == ("..n1_2",)
    assert statements[4].operands == ("..n1_2",)


def test_block_comments_are_blanked_and_lines_stay_where_they_were():
    lines = ["a /* one */ b", "/* two", "more", "end */ c", "d"]
    assert strip_block_comments(lines) == ["a   b", "", "", " c", "d"]


def test_the_syntax_is_recognised():
    assert looks_like_gas(["\t.text", "\t.globl _main", "_main:", "\tmove.l %d0,%d1"])
    assert looks_like_gas(["#include <x.h>", "// hello", "\tjbsr _f"])
    assert not looks_like_gas(["Start:\tmove.l\td0,d1", "\trts", "; comment"])


PROGRAM = """\
#include "defs.h"
/* a routine that
   doubles */
	.text
	.globl	_double
_double:
	move.l	4(%sp),%d0	| the argument
	jbsr	twice
	rts
twice:	add.l	%d0,%d0
	jeq	1f
	rts
1:	moveq	#1,%d1
.Lout:	rts

	.macro	SAVE first, second
	move.l	\\first,-(%sp)
	move.l	\\second,-(%sp)
	.endm

keeper:	SAVE	%d3, %d2
	moveq	#0,%d2
	moveq	#0,%d3
	movm.l	(%sp)+,%d2-%d3
	jra	.Lout
"""


def test_a_program_is_checked_and_its_routines_found(lint, tmp_path: Path):
    assert lint(PROGRAM, GAS, defs_h="#define SIZE 12\n") == []
    (tmp_path / "defs.h").write_text("")
    path = tmp_path / "program.s"
    path.write_text(PROGRAM)
    lines = describe_files([path], reserved=[], infer=True)
    assert [line.partition(": ")[2] for line in lines] == [
        "_double: In -; changes d0-d1 (no header)",
        "twice: In d0; changes d0-d1 (no header)",
        ".Lout: In -; changes - (no header)",
        "keeper: In -; changes - (no header)",
    ]


def test_a_dot_label_is_found_from_under_another_global_label(lint):
    source = """\
first:	tst.l	%d0
	jeq	.Lzero
	rts
second:	nop
.Lzero:	rts
"""
    assert lint(source, GAS) == []


def test_preprocessor_conditionals_are_checked_both_ways(lint):
    source = """\
	.text
start:
#ifdef FAST
	movem.l	%d2,-(%sp)
#endif
	moveq	#0,%d2
	rts
"""
    assert lint(source, GAS) == [
        "main.s:7: error: the stack is not balanced when start returns here"
    ]
    assert lint(source, {**GAS, "undefine": ["FAST"]}) == []
