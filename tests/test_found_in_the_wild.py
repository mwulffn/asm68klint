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


def test_a_star_after_a_label_starts_a_comment(lint):
    body = """\
.again				* round we go
	moveq	#0,d0		* and so on
	rts
"""
    assert lint(routine(body, "d0")) == []


def test_labels_of_values_are_not_places_in_the_code(lint):
    body = """\
	moveq	#0,d0
	rts
Size	rs.l	1
Count	equ	4
Mask	=	3
"""
    assert lint(routine(body, "d0")) == []


INFER = {"infer": True, "reserved": [], "select": ["H", "R00", "F", "S"]}
HEADERLESS = """\
Start:	moveq	#5,d0
	bsr	Double
	move.l	d0,(a1)
	rts
Double:	add.l	d0,d0
	beq	Zero
Back:	rts
Zero:	moveq	#1,d0
	bra	Back
Table:	dc.w	1,2
Hidden:	move.l	d2,-(sp)
	moveq	#0,d2
	clr.l	d3
	move.l	(sp)+,d2
Loop:	subq.w	#1,d3
	bne	Loop
	bra	Double
"""


def effects(tmp_path: Path, text: str, **options) -> list[str]:
    """Return what the routines of a source text read and change."""
    from asm68klint.linter import describe_files

    path = tmp_path / "main.s"
    path.write_text(text)
    lines = describe_files([path], reserved=[], infer=True, **options)
    return [line.partition(": ")[2] for line in lines]


def test_code_without_headers_is_split_into_routines(lint, tmp_path):
    assert lint(HEADERLESS, INFER) == []
    assert effects(tmp_path, HEADERLESS) == [
        "Start: In a1; changes d0 (no header)",
        "Double: In d0; changes d0 (no header)",
        "Hidden: In d0; changes d0/d3 (no header)",
    ]


def test_what_a_routine_without_a_header_reads_is_what_it_was_given(tmp_path):
    text = """\
First:	bsr	Second
	move.w	d0,d1
	rts
Second:	moveq	#0,d0
	rts
"""
    assert effects(tmp_path, text) == [
        "First: In -; changes d0-d1 (no header)",
        "Second: In -; changes d0 (no header)",
    ]


def test_a_routine_with_a_header_may_call_one_without(lint):
    source = routine("\tbsr\tBare\n\trts\n", "d6") + "Bare:\tmoveq\t#0,d6\n\trts\n"
    assert lint(source, {"infer": True}) == []
    assert lint(source) == [
        "main.s:7: error: cannot analyse the call to Bare: it has no routine header",
        "main.s:9: error: Bare has code but no routine header",
    ]


def test_amiga_library_calls(lint):
    body = """\
	move.l	4.w,a6
	moveq	#0,d2
	jsr	_LVOAllocMem(a6)
	move.l	d0,d2
	jsr	-198(a6)
	move.l	a1,d2
	rts
"""
    source = routine(body, "d0-d2/a0-a1/a6")
    options = {"reserved": [], "platform": "amiga", "select": ["F", "R"]}
    assert lint(source, options) == [
        (
            "main.s:12: warning: a1 is read after the system call jsr -198(a6)"
            " clobbered it"
        )
    ]
    assert len(lint(source, {"reserved": []})) == 2


def test_the_macros_of_the_amiga_includes_need_not_be_there(lint):
    source = """\
	STRUCTURE Thing,0
	APTR	th_Next
	UWORD	th_Size
	LABEL	th_SIZEOF
	BITDEF	TH,DONE,0
""" + routine("\trts\n")
    assert lint(source, {"platform": "amiga", "infer": True}) == []


def test_atari_traps(lint):
    body = """\
	move.w	#2,-(sp)
	trap	#1
	addq.l	#2,sp
	trap	#14
	move.w	d2,d0
	rts
"""
    options = {"reserved": [], "platform": "atari", "select": ["F", "R"]}
    assert lint(routine(body, "d0-d2/a0-a2"), options) == [
        "main.s:11: warning: d2 is read after the system call trap #14 clobbered it"
    ]
