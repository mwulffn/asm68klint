"""What linting other people's source taught: one test for each thing learnt."""

from pathlib import Path

from asm68klint import lint_files

FREE = {"reserved": []}


def routine(body: str, clobbers: str = "-") -> str:
    """Return a routine Foo with the given body; the body starts on line 7."""
    return f";--\n; Foo\n; In:       -\n; Out:      -\n; Clobbers: {clobbers}\nFoo:\n{body}"


def test_local_labels_may_be_numbers(lint):
    body = """\
	moveq	#3,d0
.1	subq.w	#1,d0
	bne	.1
	beq	2$
	nop
2$	rts
"""
    assert lint(routine(body, "d0")) == []


def test_a_local_label_of_another_global_label(lint):
    body = """\
	moveq	#0,d0
	beq	Bar\\.done
Bar:	moveq	#1,d0
.done	rts
"""
    assert lint(routine(body, "d0")) == []


def test_a_size_on_a_branch_target_is_not_part_of_the_name(lint):
    body = """\
	jmp	Bar.l
"""
    other = ";--\n; Bar\n; In: -\n; Out: -\n; Clobbers: d3\nBar:\tmoveq\t#0,d3\n\trts\n"
    assert lint(routine(body, "d3") + other) == []


MACROS = """\
pushm	macro
	ifc	"\\1","all"
	movem.l	d0-a6,-(sp)
	else
	movem.l	\\1,-(sp)
	endc
	endm
popm	macro
	ifc	"\\1","all"
	movem.l	(sp)+,d0-a6
	else
	movem.l	(sp)+,\\1
	endc
	endm
"""


def test_a_conditional_on_a_macro_argument_is_decided(lint):
    body = """\
	pushm	d2-d3
	moveq	#0,d2
	moveq	#0,d3
	popm	d2-d3
	pushm	all
	moveq	#0,d5
	sub.l	a4,a4
	popm	all
	rts
"""
    assert lint(MACROS + routine(body), FREE) == []


def test_conditionals_on_plain_numbers_are_decided(lint):
    body = """\
	ifgt	2-1
	moveq	#0,d0
	else
	moveq	#0,d1
	endc
	ifeq	1
	moveq	#0,d2
	elif	0
	moveq	#0,d3
	else
	moveq	#0,d4
	endc
	ifb
	moveq	#0,d5
	endc
	rts
"""
    assert lint(routine(body, "d0/d4-d5")) == []


def test_a_decided_conditional_inside_one_that_is_not(lint):
    body = """\
	ifd	DEBUG
	ifc	"a","b"
	moveq	#0,d0
	else
	moveq	#0,d1
	endc
	endc
	rts
"""
    assert lint(routine(body, "d1")) == []


def test_include_files_are_found_whatever_the_case(tmp_path: Path):
    (tmp_path / "Include" / "Exec").mkdir(parents=True)
    (tmp_path / "Include" / "Exec" / "Types.i").write_text("; nothing\n")
    main = tmp_path / "main.s"
    main.write_text(
        '\tincdir\t"include:"\n\tinclude\t"exec/types.i"\n'
        "\tinclude\tINCLUDE:exec\\TYPES.I\n"
    )
    assert lint_files([main], include_dirs=[tmp_path / "Include"]) == []
